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
