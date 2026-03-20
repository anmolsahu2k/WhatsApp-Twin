"""Real-time learning pipeline — persists live messages, updates style & memory.

Called after each hotkey-triggered AX read. Runs asynchronously so it never
blocks draft generation or insertion.
"""

import re
import threading
from datetime import datetime, timezone

from whatsapp_twin.config.logging import get_logger
from whatsapp_twin.config.settings import Settings
from whatsapp_twin.storage.database import Database

log = get_logger(__name__)

# Only run memory extraction every N hotkey presses per contact
MEMORY_EXTRACTION_INTERVAL = 5

# Regex for AX time formats: "10:00 AM", "1:47 AM", "March14,at1:47 AM"
_AX_TIME_RE = re.compile(r'(\d{1,2}):(\d{2})\s*(AM|PM)', re.IGNORECASE)


def _parse_ax_time(ax_time: str) -> str:
    """Parse AX time string into ISO 8601 timestamp.

    AX provides partial times like "10:00 AM" or "March14,at1:47 AM".
    We extract the H:MM AM/PM part and combine with today's date.

    Returns:
        ISO 8601 timestamp string (e.g. "2026-03-19T10:00:00").
    """
    now = datetime.now()
    match = _AX_TIME_RE.search(ax_time)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        ampm = match.group(3).upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()
    return now.isoformat()


class LiveLearner:
    """Orchestrates real-time message persistence, style updates, and memory extraction."""

    def __init__(self, db: Database, settings: Settings, claude_client=None):
        self.db = db
        self.settings = settings
        self.claude = claude_client
        self._hotkey_counts: dict[int, int] = {}  # contact_id -> press count
        self._last_thread: threading.Thread | None = None

    def process_live_messages(
        self,
        ax_messages: list[dict],
        contact_name: str,
        contact_id: int,
    ):
        """Process live AX messages asynchronously.

        Spawns a background thread that:
        1. Persists new messages to DB (dedup)
        2. Incrementally updates style profile from new sent messages
        3. Periodically extracts memories from recent conversation

        This method returns immediately and never blocks the caller.
        """
        thread = threading.Thread(
            target=self._process,
            args=(ax_messages, contact_name, contact_id),
            daemon=True,
        )
        self._last_thread = thread
        thread.start()

    def _process(self, ax_messages: list[dict], contact_name: str, contact_id: int):
        """Background processing of live messages."""
        try:
            # Step 1: Persist new messages
            inserted = self.persist_live_messages(ax_messages, contact_name, contact_id)
            if inserted > 0:
                log.info("Persisted %d new live messages for '%s'", inserted, contact_name)

            # Step 2: Incremental style update (only if new sent messages were inserted)
            if inserted > 0:
                self._update_style(ax_messages, contact_id)

            # Step 3: Memory extraction (throttled)
            self._hotkey_counts[contact_id] = self._hotkey_counts.get(contact_id, 0) + 1
            if (self.claude
                    and self._hotkey_counts[contact_id] % MEMORY_EXTRACTION_INTERVAL == 0):
                self._extract_memories(contact_name, contact_id)

        except Exception as e:
            log.warning("Live learning failed for '%s': %s", contact_name, e)

    def persist_live_messages(
        self,
        ax_messages: list[dict],
        contact_name: str,
        contact_id: int,
    ) -> int:
        """Persist AX messages to DB with deduplication.

        Args:
            ax_messages: Messages from read_current_chat().messages.
                Each has keys: sender, text, time, direction.
            contact_name: Contact or group display name.
            contact_id: DB contact ID.

        Returns:
            Number of newly inserted messages.
        """
        inserted = 0

        for msg in ax_messages:
            text = msg.get("text", "").strip()
            if not text:
                continue

            direction = msg.get("direction", "received")
            if direction == "system":
                continue

            sender = msg.get("sender") or (
                self.settings.user_name if direction == "sent" else contact_name
            )

            # Parse AX time into proper ISO 8601 timestamp.
            timestamp = _parse_ax_time(msg.get("time", ""))

            was_new = self.db.insert_message_if_new(
                contact_id=contact_id,
                direction=direction,
                sender_name=sender,
                text=text,
                timestamp=timestamp,
                source="live_ax",
            )
            if was_new:
                inserted += 1

        return inserted

    def _update_style(self, ax_messages: list[dict], contact_id: int):
        """Incrementally update the style profile from new sent messages."""
        from whatsapp_twin.ingestion.style_analyzer import incremental_style_update
        from whatsapp_twin.intelligence.style_profile import StyleProfile
        from whatsapp_twin.storage.models import ParsedMessage

        # Get current profile, or bootstrap one if contact was auto-created
        contact = self.db.get_contact(contact_id)
        if not contact:
            return

        if contact.get("style_json"):
            profile = StyleProfile.from_json(contact["style_json"])
        else:
            # No profile yet — check if we have enough DB messages to bootstrap
            msg_count = self.db.message_count(contact_id)
            if msg_count < 10:
                return  # Not enough data yet
            # Bootstrap initial profile from all accumulated messages
            from whatsapp_twin.ingestion.contact_profiler import build_style_profile
            build_style_profile(contact_id, self.db, self.settings)
            log.info("Bootstrapped initial style profile for contact %d from %d messages",
                     contact_id, msg_count)
            return  # Profile just built from all messages, no incremental needed

        # Convert AX messages to ParsedMessage for the analyzer
        user_name = self.settings.user_name
        parsed = []
        for msg in ax_messages:
            if msg.get("direction") != "sent":
                continue
            parsed.append(ParsedMessage(
                timestamp=datetime.now(),
                sender=msg.get("sender") or user_name,
                text=msg.get("text", ""),
                is_system=False,
            ))

        if not parsed:
            return

        updated_profile = incremental_style_update(profile, parsed, user_name)

        # Save back to DB
        conn = self.db.connect()
        conn.execute(
            "UPDATE contacts SET style_json = ?, updated_at = datetime('now') WHERE id = ?",
            (updated_profile.to_json(), contact_id),
        )
        conn.commit()
        log.debug("Updated style profile for contact %d from %d live messages",
                  contact_id, len(parsed))

    def _extract_memories(self, contact_name: str, contact_id: int):
        """Extract memories from recent messages."""
        from whatsapp_twin.intelligence.memory import (
            incremental_memory_extraction,
            save_extracted_memories,
        )

        # Get recent messages from DB (last 50)
        recent = self.db.get_messages(contact_id, limit=50)
        if not recent:
            return

        memories = incremental_memory_extraction(
            messages=recent,
            contact_name=contact_name,
            user_name=self.settings.user_name,
            claude_client=self.claude,
        )

        if memories:
            added = save_extracted_memories(self.db, contact_id, memories, source="live_extraction")
            if added > 0:
                log.info("Extracted %d new memories for '%s'", added, contact_name)
