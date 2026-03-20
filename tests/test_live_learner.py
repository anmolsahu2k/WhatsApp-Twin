"""Tests for real-time learning pipeline."""

from unittest.mock import MagicMock
from whatsapp_twin.intelligence.memory import incremental_memory_extraction


def test_incremental_memory_extraction_returns_memories():
    """Should extract memories from a small batch of messages."""
    messages = [
        {"direction": "received", "sender_name": "Alice", "text": "I got the new job at Google!"},
        {"direction": "sent", "sender_name": "Anmol Sahu", "text": "congrats!! when do you start?"},
        {"direction": "received", "sender_name": "Alice", "text": "Next Monday, March 24th"},
        {"direction": "sent", "sender_name": "Anmol Sahu", "text": "that's awesome, we should celebrate"},
        {"direction": "received", "sender_name": "Alice", "text": "definitely! let's grab dinner this weekend"},
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


import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from whatsapp_twin.learning.live_learner import LiveLearner
from whatsapp_twin.storage.database import Database


class TestLiveLearner:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(Path(self.tmp.name))
        self.db.initialize()
        self.settings = MagicMock()
        self.settings.user_name = "Anmol Sahu"
        self.claude = MagicMock()
        self.learner = LiveLearner(self.db, self.settings, self.claude)

    def teardown_method(self):
        # Join background thread before closing DB to avoid use-after-close segfault
        if self.learner._last_thread and self.learner._last_thread.is_alive():
            self.learner._last_thread.join(timeout=2.0)
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
