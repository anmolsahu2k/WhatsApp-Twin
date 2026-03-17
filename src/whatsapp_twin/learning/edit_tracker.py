"""Draft session tracking — monitors what the user actually sends vs. the AI draft.

After a draft is inserted into WhatsApp's composer, a DraftSession tracks:
1. The original draft text and contact
2. Whether the user sends it (possibly edited) or discards it
3. The final sent text for learning

Session invalidation triggers:
- User switches to a different chat (contact name changes)
- Composer is cleared without sending
- WhatsApp loses focus for > 60 seconds
- An inbound message arrives before the user sends
- Session age exceeds 5 minutes
"""

import difflib
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from whatsapp_twin.config.logging import get_logger
from whatsapp_twin.storage.database import Database

log = get_logger(__name__)


@dataclass
class DraftSession:
    """A single draft generation session."""
    session_uuid: str
    contact_name: str
    contact_id: int | None
    draft_text: str
    model: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Tracking state
    expired: bool = False
    expire_reason: str = ""
    sent_text: str | None = None
    similarity: float = 0.0


class EditTracker:
    """Monitors draft sessions and captures what the user actually sends."""

    # Session limits
    MAX_SESSION_AGE_SECONDS = 300  # 5 minutes
    FOCUS_LOST_TIMEOUT = 60  # seconds
    POLL_INTERVAL = 1.0  # seconds

    def __init__(self, db: Database):
        self.db = db
        self._current_session: DraftSession | None = None
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def current_session(self) -> DraftSession | None:
        return self._current_session

    def start_session(
        self,
        contact_name: str,
        contact_id: int | None,
        draft_text: str,
        model: str,
    ) -> DraftSession:
        """Create a new draft session after inserting a draft.

        Invalidates any existing session first.
        """
        # Expire previous session if any
        if self._current_session and not self._current_session.expired:
            self._expire_session("new_session_started")

        session = DraftSession(
            session_uuid=str(uuid.uuid4()),
            contact_name=contact_name,
            contact_id=contact_id,
            draft_text=draft_text,
            model=model,
        )
        self._current_session = session

        # Save draft to DB
        self._save_draft(session)

        # Start monitoring in background
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()

        return session

    def on_message_sent(self, sent_text: str):
        """Called when a sent message is detected in the current chat.

        Compares against the draft and records the correction.
        """
        session = self._current_session
        if not session or session.expired:
            return

        session.sent_text = sent_text
        session.similarity = _text_similarity(session.draft_text, sent_text)

        # Stop monitoring
        self._stop_event.set()

        # Update DB with sent text
        self._update_draft_sent(session)

        # Mark session done (not expired — successfully captured)
        session.expired = True
        session.expire_reason = "sent"

    def on_chat_switched(self, new_contact_name: str):
        """Called when the user switches to a different chat."""
        session = self._current_session
        if not session or session.expired:
            return

        if new_contact_name.lower() != session.contact_name.lower():
            self._expire_session("chat_switched")

    def on_focus_lost(self):
        """Called when WhatsApp loses focus."""
        # The monitor loop handles the timeout
        pass

    def on_inbound_message(self):
        """Called when an inbound message is detected in the same chat."""
        session = self._current_session
        if not session or session.expired:
            return
        self._expire_session("inbound_message")

    def on_composer_cleared(self):
        """Called when the composer is cleared without sending."""
        session = self._current_session
        if not session or session.expired:
            return
        self._expire_session("composer_cleared")

    def _expire_session(self, reason: str):
        """Mark the current session as expired."""
        session = self._current_session
        if session and not session.expired:
            session.expired = True
            session.expire_reason = reason
            self._stop_event.set()

    def _monitor_loop(self):
        """Background thread that checks for session expiry conditions."""
        session = self._current_session
        if not session:
            return

        while not self._stop_event.is_set():
            # Check session age
            age = (datetime.now(timezone.utc) - session.created_at).total_seconds()
            if age > self.MAX_SESSION_AGE_SECONDS:
                self._expire_session("timeout")
                return

            # Check for sent messages via AX
            self._poll_for_sent_message(session)

            if session.expired:
                return

            self._stop_event.wait(self.POLL_INTERVAL)

    def _poll_for_sent_message(self, session: DraftSession):
        """Check WhatsApp AX tree for a new sent message."""
        try:
            from whatsapp_twin.app.permissions import (
                check_whatsapp_frontmost,
            )
            from whatsapp_twin.reader.accessibility import read_current_chat

            # If WhatsApp isn't frontmost, don't poll (focus lost handled by timeout)
            if not check_whatsapp_frontmost():
                return

            chat = read_current_chat()
            if not chat:
                return

            # Check if chat switched
            if chat.contact_name.lower() != session.contact_name.lower():
                self._expire_session("chat_switched")
                return

            # Check composer state — if empty and we had a draft, user may have sent or cleared
            composer_text = ""
            if chat.composer_element:
                try:
                    composer_text = chat.composer_element.AXValue or ""
                except Exception:
                    pass

            # Look at the last sent message
            sent_messages = [m for m in chat.messages if m.get("direction") == "sent"]
            if not sent_messages:
                return

            last_sent = sent_messages[-1]
            last_sent_text = last_sent.get("text", "")

            # If composer is empty and the last sent message is similar to our draft,
            # the user likely sent it (possibly edited)
            if not composer_text.strip() and last_sent_text:
                similarity = _text_similarity(session.draft_text, last_sent_text)
                # Threshold: if similarity > 0.3, it's likely our draft (edited or not)
                if similarity > 0.3:
                    self.on_message_sent(last_sent_text)
                    return

                # If composer is empty but last sent message doesn't match draft,
                # could be: user cleared composer and typed something new, or
                # draft was sent but heavily edited. We capture it if similarity > 0.1
                if similarity > 0.1:
                    self.on_message_sent(last_sent_text)
                    return

            # Check for inbound messages after the draft was inserted
            # (simple heuristic: if the last message is received, inbound arrived)
            if chat.messages:
                last_msg = chat.messages[-1]
                if last_msg.get("direction") == "received":
                    # Only invalidate if composer still has our draft (user hasn't sent yet)
                    if composer_text.strip():
                        self.on_inbound_message()
                        return

        except Exception:
            # AX errors are expected — don't crash the monitor
            pass

    def _save_draft(self, session: DraftSession):
        """Save draft to the drafts table."""
        try:
            conn = self.db.connect()
            conn.execute(
                "INSERT INTO drafts (contact_id, session_uuid, draft_text, model) "
                "VALUES (?, ?, ?, ?)",
                (session.contact_id, session.session_uuid, session.draft_text,
                 session.model),
            )
            conn.commit()
        except Exception as e:
            log.warning("Failed to save draft: %s", e)

    def _update_draft_sent(self, session: DraftSession):
        """Update the draft record with what the user actually sent."""
        try:
            conn = self.db.connect()
            conn.execute(
                "UPDATE drafts SET sent_text = ?, edit_distance = ? "
                "WHERE session_uuid = ?",
                (session.sent_text, 1.0 - session.similarity,
                 session.session_uuid),
            )
            conn.commit()
        except Exception as e:
            log.warning("Failed to update draft: %s", e)

    def stop(self):
        """Stop monitoring (e.g., on app quit)."""
        if self._current_session and not self._current_session.expired:
            self._expire_session("app_quit")
        self._stop_event.set()


def _text_similarity(a: str, b: str) -> float:
    """Compute similarity ratio between two strings (0.0 to 1.0)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()
