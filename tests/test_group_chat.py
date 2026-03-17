"""Tests for group chat support."""

import tempfile
from pathlib import Path

from whatsapp_twin.generator.prompt_builder import build_prompts
from whatsapp_twin.reader.accessibility import ChatContext, detect_group_chat
from whatsapp_twin.storage.database import Database


def _make_db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Database(Path(tmp.name))
    db.initialize()
    return db


# --- detect_group_chat ---

def test_detect_group_multiple_senders():
    messages = [
        {"direction": "received", "sender": "Rahul", "text": "hey"},
        {"direction": "received", "sender": "Manav", "text": "yo"},
        {"direction": "sent", "sender": None, "text": "sup"},
    ]
    assert detect_group_chat(messages) is True


def test_detect_individual_chat():
    messages = [
        {"direction": "received", "sender": "Rahul", "text": "hey"},
        {"direction": "sent", "sender": None, "text": "sup"},
        {"direction": "received", "sender": "Rahul", "text": "what's up"},
    ]
    assert detect_group_chat(messages) is False


def test_detect_individual_no_sender():
    """1-on-1 chats often have sender=None for received messages."""
    messages = [
        {"direction": "received", "sender": None, "text": "hey"},
        {"direction": "sent", "sender": None, "text": "sup"},
    ]
    assert detect_group_chat(messages) is False


def test_detect_group_empty():
    assert detect_group_chat([]) is False


# --- Database is_group ---

def test_create_group_contact():
    db = _make_db()
    cid = db.get_or_create_contact("College Friends", is_group=True)
    contact = db.get_contact(cid)
    assert contact["is_group"] == 1
    assert contact["canonical_name"] == "College Friends"
    db.close()


def test_create_individual_contact_not_group():
    db = _make_db()
    cid = db.get_or_create_contact("Rahul")
    contact = db.get_contact(cid)
    assert contact["is_group"] == 0
    db.close()


def test_existing_contact_returns_same_id():
    """get_or_create_contact returns existing ID even if is_group differs."""
    db = _make_db()
    cid1 = db.get_or_create_contact("College Friends", is_group=True)
    cid2 = db.get_or_create_contact("College Friends", is_group=False)
    assert cid1 == cid2
    db.close()


# --- Group import ---

def test_import_group_export():
    """Group export creates a single group contact with all messages."""
    db = _make_db()

    # Simulate what _import_group does
    from whatsapp_twin.storage.models import MessageDirection

    cid = db.get_or_create_contact("College Friends", is_group=True)
    db.add_alias(cid, "College Friends", source="export")

    db.insert_messages([
        (cid, "sent", "Anmol Sahu", "hey guys", "2025-01-01T10:00:00", "export", "group.txt"),
        (cid, "received", "Rahul", "sup bro", "2025-01-01T10:01:00", "export", "group.txt"),
        (cid, "received", "Manav", "yo", "2025-01-01T10:02:00", "export", "group.txt"),
        (cid, "sent", "Anmol Sahu", "plans tonight?", "2025-01-01T10:03:00", "export", "group.txt"),
    ])

    assert db.message_count(cid) == 4

    msgs = db.get_messages(cid, limit=10)
    senders = {m["sender_name"] for m in msgs}
    assert senders == {"Anmol Sahu", "Rahul", "Manav"}

    sent = [m for m in msgs if m["direction"] == "sent"]
    assert len(sent) == 2

    contact = db.get_contact(cid)
    assert contact["is_group"] == 1
    db.close()


# --- Group prompt builder ---

def test_group_prompt_uses_group_template():
    messages = [
        {"direction": "received", "sender": "Rahul", "text": "anyone free tonight?", "time": "10:00 PM"},
        {"direction": "received", "sender": "Manav", "text": "i am", "time": "10:01 PM"},
    ]
    system, user = build_prompts(
        live_messages=messages,
        contact_name="College Friends",
        user_name="Anmol",
        is_group=True,
    )

    assert 'group chat "College Friends"' in system
    assert "group dynamics" in system
    assert 'group "College Friends"' in user


def test_individual_prompt_uses_individual_template():
    messages = [
        {"direction": "received", "sender": "Rahul", "text": "hey", "time": "10:00 PM"},
    ]
    system, user = build_prompts(
        live_messages=messages,
        contact_name="Rahul",
        user_name="Anmol",
        is_group=False,
    )

    assert "group" not in system.lower().split("style")[0]  # no "group" before style context
    assert "reply as Anmol would send it to Rahul" in user


def test_group_prompt_default_is_false():
    """is_group defaults to False for backward compatibility."""
    messages = [
        {"direction": "received", "sender": "Rahul", "text": "hey", "time": "10:00 PM"},
    ]
    system, user = build_prompts(
        live_messages=messages,
        contact_name="Rahul",
        user_name="Anmol",
    )
    assert "reply as Anmol would send it to Rahul" in user


# --- ChatContext ---

def test_chat_context_is_group():
    ctx = ChatContext(
        contact_name="College Friends",
        messages=[
            {"direction": "received", "sender": "Rahul", "text": "hey"},
            {"direction": "received", "sender": "Manav", "text": "yo"},
        ],
        is_group=True,
    )
    assert ctx.is_group is True


def test_chat_context_default_not_group():
    ctx = ChatContext(contact_name="Rahul", messages=[])
    assert ctx.is_group is False


# --- Group name extraction ---

def test_extract_group_name_with_prefix():
    from whatsapp_twin.ingestion.contact_profiler import _extract_group_name_from_filename
    assert _extract_group_name_from_filename(Path("WhatsApp Chat with College Friends.txt")) == "College Friends"


def test_extract_group_name_dash_prefix():
    from whatsapp_twin.ingestion.contact_profiler import _extract_group_name_from_filename
    assert _extract_group_name_from_filename(Path("WhatsApp Chat - Work Team.txt")) == "Work Team"


def test_extract_group_name_no_prefix():
    from whatsapp_twin.ingestion.contact_profiler import _extract_group_name_from_filename
    assert _extract_group_name_from_filename(Path("random_export.txt")) == "random_export"


def test_extract_group_name_from_system_messages():
    from whatsapp_twin.ingestion.contact_profiler import _extract_group_name_from_messages
    from whatsapp_twin.storage.models import ParsedMessage
    from datetime import datetime

    messages = [
        ParsedMessage(timestamp=datetime(2024, 1, 1), sender="", text='You created group "Weekend Plans"', is_system=True),
        ParsedMessage(timestamp=datetime(2024, 1, 2), sender="Alice", text="Hey!", is_system=False),
    ]
    assert _extract_group_name_from_messages(messages) == "Weekend Plans"


def test_extract_group_name_subject_change_overrides():
    from whatsapp_twin.ingestion.contact_profiler import _extract_group_name_from_messages
    from whatsapp_twin.storage.models import ParsedMessage
    from datetime import datetime

    messages = [
        ParsedMessage(timestamp=datetime(2024, 1, 1), sender="", text='You created group "Old Name"', is_system=True),
        ParsedMessage(timestamp=datetime(2024, 2, 1), sender="", text='Alice changed the subject to "New Name"', is_system=True),
    ]
    assert _extract_group_name_from_messages(messages) == "New Name"


def test_extract_group_name_no_system_messages():
    from whatsapp_twin.ingestion.contact_profiler import _extract_group_name_from_messages
    from whatsapp_twin.storage.models import ParsedMessage
    from datetime import datetime

    messages = [
        ParsedMessage(timestamp=datetime(2024, 1, 1), sender="Alice", text="Hey!", is_system=False),
    ]
    assert _extract_group_name_from_messages(messages) is None
