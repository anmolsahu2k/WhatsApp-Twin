"""Tests for database operations."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from whatsapp_twin.storage.database import Database


def _make_db() -> Database:
    """Create an in-memory database for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Database(Path(tmp.name), encryption_key=None)
    db.initialize()
    return db


def test_create_and_get_contact():
    db = _make_db()
    cid = db.get_or_create_contact("Rahul")
    assert cid > 0

    # Get same contact again
    cid2 = db.get_or_create_contact("Rahul")
    assert cid == cid2

    contact = db.get_contact(cid)
    assert contact["canonical_name"] == "Rahul"
    db.close()


def test_contact_aliases():
    db = _make_db()
    cid = db.get_or_create_contact("Rahul Kumar")
    db.add_alias(cid, "Rahul", source="export")
    db.add_alias(cid, "Rahul K", source="manual")

    found = db.find_contact_by_alias("Rahul")
    assert found == cid

    found2 = db.find_contact_by_alias("Unknown")
    assert found2 is None
    db.close()


def test_insert_and_get_messages():
    db = _make_db()
    cid = db.get_or_create_contact("Rahul")

    now = datetime.now(UTC)
    messages = [
        (cid, "received", "Rahul", "bro gym aaj?", now.isoformat(), "export", "chat.txt"),
        (cid, "sent", "Anmol", "haan bhai 6 baje", (now + timedelta(minutes=1)).isoformat(), "export", "chat.txt"),
    ]
    db.insert_messages(messages)

    result = db.get_messages(cid, limit=10)
    assert len(result) == 2
    assert result[0]["sender_name"] == "Rahul"
    assert result[1]["sender_name"] == "Anmol"
    db.close()


def test_message_count():
    db = _make_db()
    cid = db.get_or_create_contact("Rahul")

    now = datetime.now(UTC)
    messages = [
        (cid, "received", "Rahul", f"msg {i}", (now + timedelta(minutes=i)).isoformat(), "export", "chat.txt")
        for i in range(5)
    ]
    db.insert_messages(messages)

    assert db.message_count(cid) == 5
    db.close()


def test_has_export():
    db = _make_db()
    cid = db.get_or_create_contact("Rahul")

    assert not db.has_export("chat.txt")

    now = datetime.now(UTC)
    db.insert_messages([(cid, "received", "Rahul", "hi", now.isoformat(), "export", "chat.txt")])

    assert db.has_export("chat.txt")
    db.close()


def test_purge_expired():
    db = _make_db()
    cid = db.get_or_create_contact("Rahul")

    conn = db.connect()

    # Insert old message (100 days ago)
    old_time = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    conn.execute(
        "INSERT INTO messages (contact_id, direction, sender_name, text, timestamp, source, created_at) "
        "VALUES (?, 'received', 'Rahul', 'old msg', ?, 'export', ?)",
        (cid, old_time, old_time),
    )

    # Insert recent message
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO messages (contact_id, direction, sender_name, text, timestamp, source, created_at) "
        "VALUES (?, 'received', 'Rahul', 'new msg', ?, 'export', ?)",
        (cid, now, now),
    )
    conn.commit()

    assert db.message_count(cid) == 2

    db.purge_expired(messages_days=90)

    assert db.message_count(cid) == 1
    remaining = db.get_messages(cid)
    assert remaining[0]["text"] == "new msg"
    db.close()


def test_delete_contact_cascades():
    db = _make_db()
    cid = db.get_or_create_contact("Rahul")
    db.add_alias(cid, "Rahul K")

    now = datetime.now(UTC)
    db.insert_messages([(cid, "received", "Rahul", "hi", now.isoformat(), "export", None)])

    db.delete_contact(cid)

    assert db.get_contact(cid) is None
    assert db.message_count(cid) == 0
    assert db.find_contact_by_alias("Rahul K") is None
    db.close()


def test_list_contacts():
    db = _make_db()
    db.get_or_create_contact("Zebra")
    db.get_or_create_contact("Alpha")

    contacts = db.list_contacts()
    assert len(contacts) == 2
    assert contacts[0]["canonical_name"] == "Alpha"  # sorted
    db.close()
