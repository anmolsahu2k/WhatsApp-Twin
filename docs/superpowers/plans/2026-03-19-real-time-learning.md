# Real-Time Learning: Live Style Profile & Memory Updates

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically persist live AX messages to the database and incrementally update style profiles and contact memories in real-time, so the system continuously improves without requiring manual re-imports.

**Architecture:** Each time a draft is generated (hotkey press), the AX messages are already read. We add a background pipeline that: (1) persists new live messages to the DB with deduplication, (2) incrementally updates the quantitative style profile from new sent messages, and (3) periodically extracts new memories from recent conversation chunks. All three run asynchronously after draft insertion so they never block the user.

**Tech Stack:** Python threading, SQLite, existing `style_analyzer.py` + `memory.py` modules, anthropic SDK for memory extraction.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/whatsapp_twin/learning/live_learner.py` | Orchestrator: persist messages, trigger style & memory updates |
| Modify | `src/whatsapp_twin/storage/database.py` | Add `insert_message_if_new()` dedup method, `get_recent_messages_since()` |
| Modify | `src/whatsapp_twin/ingestion/style_analyzer.py` | Add `incremental_style_update()` for partial re-analysis |
| Modify | `src/whatsapp_twin/intelligence/memory.py` | Add `incremental_memory_extraction()` for recent messages only |
| Modify | `src/whatsapp_twin/app/menubar.py` | Wire `LiveLearner` into the hotkey flow |
| Create | `tests/test_live_learner.py` | Tests for the new live learning pipeline |
| Modify | `tests/test_database.py` | Tests for new DB dedup methods |

---

### Task 1: Database Deduplication Methods

**Files:**
- Modify: `src/whatsapp_twin/storage/database.py:204-254`
- Test: `tests/test_database.py`

We need two new DB methods: one to insert a single message only if it doesn't already exist (dedup by contact_id + sender_name + text + timestamp), and one to get messages since a specific timestamp for incremental processing.

- [ ] **Step 1: Write failing tests for `insert_message_if_new()`**

Add to `tests/test_database.py` (note: uses standalone functions with `_make_db()` helper, matching existing test style):

```python
def test_insert_message_if_new_inserts_novel_message():
    """New message should be inserted and return True."""
    db = _make_db()
    cid = db.get_or_create_contact("Alice")
    result = db.insert_message_if_new(
        contact_id=cid, direction="sent", sender_name="Anmol Sahu",
        text="hello there", timestamp="2026-03-19T10:00:00",
        source="live_ax",
    )
    assert result is True
    assert db.message_count(cid) == 1
    db.close()

def test_insert_message_if_new_skips_duplicate():
    """Duplicate message (same contact, sender, text, timestamp) should return False."""
    db = _make_db()
    cid = db.get_or_create_contact("Alice")
    db.insert_message_if_new(
        contact_id=cid, direction="sent", sender_name="Anmol Sahu",
        text="hello there", timestamp="2026-03-19T10:00:00",
        source="live_ax",
    )
    result = db.insert_message_if_new(
        contact_id=cid, direction="sent", sender_name="Anmol Sahu",
        text="hello there", timestamp="2026-03-19T10:00:00",
        source="live_ax",
    )
    assert result is False
    assert db.message_count(cid) == 1
    db.close()

def test_get_recent_messages_since():
    """Should return only messages after the given timestamp."""
    db = _make_db()
    cid = db.get_or_create_contact("Alice")
    db.insert_messages([
        (cid, "sent", "Anmol", "old msg", "2026-03-18T10:00:00", "export", None),
        (cid, "sent", "Anmol", "new msg", "2026-03-19T10:00:00", "live_ax", None),
        (cid, "received", "Alice", "reply", "2026-03-19T10:01:00", "live_ax", None),
    ])
    recent = db.get_recent_messages_since(cid, "2026-03-18T23:59:59")
    assert len(recent) == 2
    assert recent[0]["text"] == "new msg"
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_database.py -v -k "insert_message_if_new or get_recent_messages_since"`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Implement `insert_message_if_new()` and `get_recent_messages_since()`**

Add to `src/whatsapp_twin/storage/database.py` in the `Database` class, after the existing `has_export()` method:

```python
def insert_message_if_new(
    self, contact_id: int, direction: str, sender_name: str,
    text: str, timestamp: str, source: str = "live_ax",
) -> bool:
    """Insert a message only if an identical one doesn't exist.

    Deduplicates on (contact_id, sender_name, text, timestamp).
    Returns True if inserted, False if duplicate.
    """
    conn = self.connect()
    existing = conn.execute(
        "SELECT 1 FROM messages WHERE contact_id = ? AND sender_name = ? "
        "AND text = ? AND timestamp = ? LIMIT 1",
        (contact_id, sender_name, text, timestamp),
    ).fetchone()
    if existing:
        return False
    conn.execute(
        "INSERT INTO messages (contact_id, direction, sender_name, text, timestamp, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (contact_id, direction, sender_name, text, timestamp, source),
    )
    conn.commit()
    return True

def get_recent_messages_since(
    self, contact_id: int, since_timestamp: str, limit: int = 500,
) -> list[dict]:
    """Get messages for a contact after a given timestamp, chronological order."""
    conn = self.connect()
    rows = conn.execute(
        "SELECT * FROM messages WHERE contact_id = ? AND timestamp > ? "
        "ORDER BY timestamp ASC LIMIT ?",
        (contact_id, since_timestamp, limit),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_database.py -v -k "insert_message_if_new or get_recent_messages_since"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/whatsapp_twin/storage/database.py tests/test_database.py
git commit -m "feat: add message dedup and recent-since query to Database"
```

---

### Task 2: Incremental Style Update

**Files:**
- Modify: `src/whatsapp_twin/ingestion/style_analyzer.py`
- Test: `tests/test_style_analyzer.py`

Add a function that takes a small batch of new sent messages and updates an existing `StyleProfile` using EMA blending — the same approach already used in `style_updater.py` for corrections.

- [ ] **Step 1: Write failing test for `incremental_style_update()`**

Add to `tests/test_style_analyzer.py`:

```python
from whatsapp_twin.intelligence.style_profile import StyleProfile
from whatsapp_twin.ingestion.style_analyzer import incremental_style_update
from whatsapp_twin.storage.models import ParsedMessage
from datetime import datetime

def test_incremental_style_update_adjusts_message_length():
    """New messages should nudge avg_message_length_words toward their average."""
    profile = StyleProfile(avg_message_length_words=5.0, emoji_density=0.0)
    new_messages = [
        ParsedMessage(timestamp=datetime.now(), sender="Anmol Sahu",
                      text="this is a much longer message than usual with many words", is_system=False),
        ParsedMessage(timestamp=datetime.now(), sender="Anmol Sahu",
                      text="another long message with several extra words in it", is_system=False),
    ]
    updated = incremental_style_update(profile, new_messages, "Anmol Sahu")
    # Should move toward ~10 words but not jump there instantly
    assert updated.avg_message_length_words > 5.0
    assert updated.avg_message_length_words < 10.0

def test_incremental_style_update_ignores_received_messages():
    """Only user's sent messages should influence the profile."""
    profile = StyleProfile(avg_message_length_words=5.0)
    new_messages = [
        ParsedMessage(timestamp=datetime.now(), sender="Alice",
                      text="this is from someone else and should be ignored completely", is_system=False),
    ]
    updated = incremental_style_update(profile, new_messages, "Anmol Sahu")
    assert updated.avg_message_length_words == 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_style_analyzer.py -v -k "incremental"`
Expected: FAIL — `incremental_style_update` doesn't exist.

- [ ] **Step 3: Implement `incremental_style_update()`**

Add to `src/whatsapp_twin/ingestion/style_analyzer.py`:

```python
def incremental_style_update(
    profile: StyleProfile,
    new_messages: list,
    user_name: str,
    alpha: float = 0.15,
) -> StyleProfile:
    """Update an existing StyleProfile incrementally from new messages.

    Only processes messages sent by user_name. Uses EMA blending so
    recent messages shift the profile gradually without erasing history.

    Args:
        profile: Current StyleProfile to update.
        new_messages: List of ParsedMessage objects (new messages only).
        user_name: The user's sender name to filter by.
        alpha: EMA smoothing factor (0-1). Higher = more weight on new data.

    Returns:
        Updated StyleProfile (same object, mutated in place and returned).
    """
    user_msgs = [m for m in new_messages if m.sender == user_name and not m.is_system]
    if not user_msgs:
        return profile

    # Compute stats from new messages batch
    new_profile = analyze_style(new_messages, user_name)

    # Blend each numeric field via EMA
    def ema(old: float, new: float) -> float:
        return old * (1 - alpha) + new * alpha

    profile.avg_message_length_chars = ema(profile.avg_message_length_chars, new_profile.avg_message_length_chars)
    profile.avg_message_length_words = ema(profile.avg_message_length_words, new_profile.avg_message_length_words)
    profile.avg_messages_per_turn = ema(profile.avg_messages_per_turn, new_profile.avg_messages_per_turn)
    profile.split_message_ratio = ema(profile.split_message_ratio, new_profile.split_message_ratio)
    profile.hinglish_ratio = ema(profile.hinglish_ratio, new_profile.hinglish_ratio)
    profile.emoji_density = ema(profile.emoji_density, new_profile.emoji_density)
    profile.period_usage_ratio = ema(profile.period_usage_ratio, new_profile.period_usage_ratio)
    profile.ellipsis_frequency = ema(profile.ellipsis_frequency, new_profile.ellipsis_frequency)
    profile.avg_words_per_sentence = ema(profile.avg_words_per_sentence, new_profile.avg_words_per_sentence)

    # Merge top emojis (union, keep top 10)
    if new_profile.top_emojis:
        combined = list(dict.fromkeys(profile.top_emojis + new_profile.top_emojis))
        profile.top_emojis = combined[:10]

    # Update laughing style if new messages have one
    if new_profile.laughing_style:
        profile.laughing_style = new_profile.laughing_style

    # Update language if it shifted
    if new_profile.primary_language != "en" and new_profile.primary_language != profile.primary_language:
        profile.primary_language = new_profile.primary_language

    # Merge hindi words
    if new_profile.common_hindi_words:
        combined = list(dict.fromkeys(profile.common_hindi_words + new_profile.common_hindi_words))
        profile.common_hindi_words = combined[:15]

    # Merge abbreviations
    if new_profile.common_abbreviations:
        profile.common_abbreviations.update(new_profile.common_abbreviations)

    return profile
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_style_analyzer.py -v -k "incremental"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/whatsapp_twin/ingestion/style_analyzer.py tests/test_style_analyzer.py
git commit -m "feat: add incremental_style_update for EMA-based live profile updates"
```

---

### Task 3: Incremental Memory Extraction

**Files:**
- Modify: `src/whatsapp_twin/intelligence/memory.py`
- Test: `tests/test_live_learner.py` (we'll create this file in Task 4, but write the memory test here)

Add a lightweight function that extracts memories from a small batch of recent messages (no chunking needed since batches are small). This reuses the existing extraction prompt but with a smaller context window.

- [ ] **Step 1: Write failing test**

Create `tests/test_live_learner.py`:

```python
"""Tests for real-time learning pipeline."""

from unittest.mock import MagicMock, patch
from whatsapp_twin.intelligence.memory import incremental_memory_extraction


def test_incremental_memory_extraction_returns_memories():
    """Should extract memories from a small batch of messages."""
    messages = [
        {"direction": "received", "sender_name": "Alice", "text": "I got the new job at Google!"},
        {"direction": "sent", "sender_name": "Anmol Sahu", "text": "congrats!! when do you start?"},
        {"direction": "received", "sender_name": "Alice", "text": "Next Monday, March 24th"},
    ]

    mock_client = MagicMock()
    mock_client.generate.return_value = '[{"category": "fact", "content": "Alice got a new job at Google, starting March 24th"}]'

    memories = incremental_memory_extraction(
        messages=messages,
        contact_name="Alice",
        user_name="Anmol Sahu",
        claude_client=mock_client,
    )
    assert len(memories) == 1
    assert memories[0]["category"] == "fact"
    assert "Google" in memories[0]["content"]


def test_incremental_memory_extraction_empty_on_short_conversation():
    """Should return empty list if fewer than 5 messages."""
    messages = [
        {"direction": "received", "sender_name": "Alice", "text": "hey"},
        {"direction": "sent", "sender_name": "Anmol Sahu", "text": "hi"},
    ]

    mock_client = MagicMock()
    memories = incremental_memory_extraction(
        messages=messages,
        contact_name="Alice",
        user_name="Anmol Sahu",
        claude_client=mock_client,
    )
    assert memories == []
    mock_client.generate.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_live_learner.py -v -k "incremental_memory"`
Expected: FAIL — `incremental_memory_extraction` doesn't exist.

- [ ] **Step 3: Implement `incremental_memory_extraction()`**

Add to `src/whatsapp_twin/intelligence/memory.py`:

```python
# Minimum messages to bother extracting memories from
MIN_MESSAGES_FOR_EXTRACTION = 5


def incremental_memory_extraction(
    messages: list[dict],
    contact_name: str,
    user_name: str,
    claude_client,
) -> list[dict]:
    """Extract memories from a small batch of recent messages.

    Unlike extract_memories_from_messages(), this is designed for small
    live batches (10-50 messages) and doesn't chunk. Skips if batch
    is too small to contain meaningful facts.

    Args:
        messages: Recent message dicts with keys: direction, sender_name, text.
        contact_name: Contact display name.
        user_name: User's display name.
        claude_client: ClaudeClient instance.

    Returns:
        List of dicts with keys: category, content.
    """
    if len(messages) < MIN_MESSAGES_FOR_EXTRACTION:
        return []

    chunk_text = _format_messages_for_extraction(messages, contact_name, user_name)

    system = (
        "You are extracting key facts and relationship information from a recent WhatsApp conversation "
        f"between {user_name} and {contact_name}.\n\n"
        "Extract ONLY concrete, new information. Categories:\n"
        "- fact: personal details (birthday, job, location, family, hobbies)\n"
        "- commitment: plans, promises, things to follow up on\n"
        "- event: significant shared events or milestones\n"
        "- preference: known likes/dislikes, opinions\n"
        "- relationship: nature of relationship, shared context, inside jokes\n\n"
        "Return a JSON array of objects with 'category' and 'content' fields.\n"
        "Each content should be a concise statement (1 sentence max).\n"
        "Return ONLY valid JSON, no markdown fencing. Return [] if nothing noteworthy."
    )

    user_msg = (
        f"Extract key memories from this recent conversation between {user_name} and {contact_name}:\n\n"
        f"<conversation>\n{chunk_text}\n</conversation>"
    )

    try:
        response = claude_client.generate(system, user_msg, max_tokens=500)
        return _parse_extraction_response(response)
    except Exception as e:
        log.warning("Incremental memory extraction failed: %s", e)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_live_learner.py -v -k "incremental_memory"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/whatsapp_twin/intelligence/memory.py tests/test_live_learner.py
git commit -m "feat: add incremental_memory_extraction for live memory updates"
```

---

### Task 4: LiveLearner Orchestrator

**Files:**
- Create: `src/whatsapp_twin/learning/live_learner.py`
- Test: `tests/test_live_learner.py` (append)

This is the core new module. It takes live AX messages, persists new ones, and triggers incremental style + memory updates in the background.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_live_learner.py`:

```python
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch

from whatsapp_twin.learning.live_learner import LiveLearner
from whatsapp_twin.storage.database import Database
from whatsapp_twin.config.settings import Settings


class TestLiveLearner:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(Path(self.tmp.name))
        self.db.initialize()
        self.settings = MagicMock(spec=Settings)
        self.settings.user_name = "Anmol Sahu"
        self.claude = MagicMock()
        self.learner = LiveLearner(self.db, self.settings, self.claude)

    def teardown_method(self):
        self.db.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_persist_new_messages_inserts_to_db(self):
        """Live AX messages should be persisted with source='live_ax'."""
        cid = self.db.get_or_create_contact("Alice")
        self.db.add_alias(cid, "Alice", source="manual")

        ax_messages = [
            {"sender": "Anmol Sahu", "text": "hey", "time": "10:00 AM", "direction": "sent"},
            {"sender": "Alice", "text": "hi!", "time": "10:01 AM", "direction": "received"},
        ]
        inserted = self.learner.persist_live_messages(ax_messages, "Alice", cid)
        assert inserted >= 1
        assert self.db.message_count(cid) >= 1

    def test_persist_deduplicates_messages(self):
        """Same messages persisted twice should not create duplicates."""
        cid = self.db.get_or_create_contact("Alice")
        ax_messages = [
            {"sender": "Anmol Sahu", "text": "hey", "time": "10:00 AM", "direction": "sent"},
        ]
        self.learner.persist_live_messages(ax_messages, "Alice", cid)
        self.learner.persist_live_messages(ax_messages, "Alice", cid)
        assert self.db.message_count(cid) == 1

    def test_process_does_not_block(self):
        """process_live_messages should return immediately (async)."""
        cid = self.db.get_or_create_contact("Alice")
        ax_messages = [
            {"sender": "Anmol Sahu", "text": "hey", "time": "10:00 AM", "direction": "sent"},
        ]
        import time
        start = time.monotonic()
        self.learner.process_live_messages(ax_messages, "Alice", cid)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0  # Should not block
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_live_learner.py::TestLiveLearner -v`
Expected: FAIL — `LiveLearner` doesn't exist.

- [ ] **Step 3: Implement `LiveLearner`**

Create `src/whatsapp_twin/learning/live_learner.py`:

```python
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
        self._hotkey_counts: dict[int, int] = {}  # contact_id → press count

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
            # AX provides partial times like "10:00 AM" or "March14,at1:47 AM".
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
        from whatsapp_twin.ingestion.style_analyzer import analyze_style, incremental_style_update
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_live_learner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/whatsapp_twin/learning/live_learner.py tests/test_live_learner.py
git commit -m "feat: add LiveLearner orchestrator for real-time learning pipeline"
```

---

### Task 5: Wire LiveLearner into Menubar App

**Files:**
- Modify: `src/whatsapp_twin/app/menubar.py:30-45` (init) and `src/whatsapp_twin/app/menubar.py:219-325` (hotkey flow)

Connect the LiveLearner so it runs automatically on every hotkey press.

- [ ] **Step 1: Add LiveLearner import and initialization**

In `src/whatsapp_twin/app/menubar.py`, add import at the top (after existing imports around line 27):

```python
from whatsapp_twin.learning.live_learner import LiveLearner
```

In `WhatsAppTwinApp.__init__()`, after `self.draft_manager = DraftManager()` (line 44), add:

```python
self.live_learner = LiveLearner(self.db, self.settings, self.claude)
```

- [ ] **Step 2: Call LiveLearner in the hotkey flow**

In `_generate_and_insert()`, after the contact lookup succeeds and before the multi-draft check (around line 247, after the exclusion checks), add:

```python
# Persist live messages and trigger background learning
if contact_id is not None:
    self.live_learner.process_live_messages(
        chat.messages, contact_name, contact_id,
    )
```

- [ ] **Step 3: Run existing tests to verify nothing is broken**

Run: `python -m pytest tests/ -v`
Expected: All 81+ tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/whatsapp_twin/app/menubar.py
git commit -m "feat: wire LiveLearner into menubar hotkey flow for real-time learning"
```

---

### Task 6: Auto-Create Contacts from Live AX

**Files:**
- Modify: `src/whatsapp_twin/app/menubar.py:239-245`

Currently, if a contact isn't in the DB (`contact_id is None`), no learning happens and edit tracking is skipped. We should auto-create contacts from live AX reads so learning starts immediately even without a prior import.

- [ ] **Step 1: Add auto-create logic**

In `_generate_and_insert()`, replace the section after `contact_id = self.db.find_contact_by_alias(contact_name)` (around line 239-245). Currently:

```python
contact_id = self.db.find_contact_by_alias(contact_name)
if contact_id:
    contact = self.db.get_contact(contact_id)
    if contact and contact.get("excluded"):
        return
if contact_name in self.settings.excluded_contacts:
    return
```

Replace with:

```python
contact_id = self.db.find_contact_by_alias(contact_name)
if contact_id:
    contact = self.db.get_contact(contact_id)
    if contact and contact.get("excluded"):
        return
else:
    # Auto-create contact from live AX read
    contact_id = self.db.get_or_create_contact(
        contact_name, is_group=chat.is_group,
    )
    self.db.add_alias(contact_id, contact_name, source="live_ax")
    # Note: don't call _refresh_contacts_menu() here — hotkey fires in a
    # daemon thread and rumps menu updates must happen on the main thread.
    # The menu will refresh on next import or app restart.
    log.info("Auto-created contact '%s' from live chat", contact_name)
if contact_name in self.settings.excluded_contacts:
    return
```

- [ ] **Step 2: Run existing tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/whatsapp_twin/app/menubar.py
git commit -m "feat: auto-create contacts from live AX reads for immediate learning"
```

---

### Task 7: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

Document the new real-time learning behavior.

- [ ] **Step 1: Add real-time learning section to CLAUDE.md**

Add after the "Edit learning" bullet in the Key Technical Details section:

```markdown
- **Real-time learning** — every hotkey press persists live AX messages to DB (`source='live_ax'`), incrementally updates the quantitative style profile via EMA, and extracts new memories every 5th press per contact. All runs in background threads. Contacts not in DB are auto-created on first AX read.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document real-time learning pipeline in CLAUDE.md"
```
