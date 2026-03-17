"""Tests for edit learning — edit_tracker and style_updater."""

import tempfile
from pathlib import Path

from whatsapp_twin.intelligence.style_profile import StyleProfile
from whatsapp_twin.learning.edit_tracker import DraftSession, EditTracker, _text_similarity
from whatsapp_twin.learning.style_updater import (
    categorize_corrections,
    process_correction,
    _count_emojis,
    _hindi_word_ratio,
)
from whatsapp_twin.storage.database import Database


def _make_db() -> Database:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Database(Path(tmp.name), encryption_key=None)
    db.initialize()
    return db


# --- text_similarity ---

def test_similarity_identical():
    assert _text_similarity("hello world", "hello world") == 1.0


def test_similarity_empty():
    assert _text_similarity("", "") == 1.0
    assert _text_similarity("hello", "") == 0.0


def test_similarity_case_insensitive():
    s = _text_similarity("Hello World", "hello world")
    assert s == 1.0


def test_similarity_partial():
    s = _text_similarity("hello world", "hello there world")
    assert 0.5 < s < 1.0


def test_similarity_completely_different():
    s = _text_similarity("hello world", "xyz abc 123")
    assert s < 0.3


# --- categorize_corrections ---

def test_correction_length():
    corrections = categorize_corrections("hey whats up", "hey")
    categories = [c["category"] for c in corrections]
    assert "length" in categories


def test_correction_emoji():
    corrections = categorize_corrections("sounds good", "sounds good 😂😂😂")
    categories = [c["category"] for c in corrections]
    assert "emoji" in categories


def test_correction_language():
    corrections = categorize_corrections("yeah that works", "haan chal theek hai")
    categories = [c["category"] for c in corrections]
    assert "language" in categories


def test_correction_punctuation_period():
    corrections = categorize_corrections("ok sure.", "ok sure")
    categories = [c["category"] for c in corrections]
    assert "punctuation" in categories


def test_correction_capitalization():
    corrections = categorize_corrections("Hey there", "hey there")
    categories = [c["category"] for c in corrections]
    assert "punctuation" in categories


def test_no_corrections_identical():
    corrections = categorize_corrections("hello", "hello")
    # Identical text should have no corrections (or minimal)
    categories = [c["category"] for c in corrections]
    assert "length" not in categories
    assert "emoji" not in categories
    assert "language" not in categories


# --- DraftSession + EditTracker ---

def test_start_session():
    db = _make_db()
    tracker = EditTracker(db)

    session = tracker.start_session(
        contact_name="Manav",
        contact_id=None,
        draft_text="sounds good bro",
        model="claude-sonnet-4-6",
    )

    assert session.session_uuid
    assert session.draft_text == "sounds good bro"
    assert not session.expired
    assert tracker.current_session is session
    tracker.stop()
    db.close()


def test_session_expire_on_chat_switch():
    db = _make_db()
    tracker = EditTracker(db)

    session = tracker.start_session(
        contact_name="Manav",
        contact_id=None,
        draft_text="test",
        model="test",
    )

    tracker.on_chat_switched("Rahul")
    assert session.expired
    assert session.expire_reason == "chat_switched"
    tracker.stop()
    db.close()


def test_session_expire_on_inbound():
    db = _make_db()
    tracker = EditTracker(db)

    session = tracker.start_session(
        contact_name="Manav",
        contact_id=None,
        draft_text="test",
        model="test",
    )

    tracker.on_inbound_message()
    assert session.expired
    assert session.expire_reason == "inbound_message"
    tracker.stop()
    db.close()


def test_session_expire_on_composer_cleared():
    db = _make_db()
    tracker = EditTracker(db)

    session = tracker.start_session(
        contact_name="Manav",
        contact_id=None,
        draft_text="test",
        model="test",
    )

    tracker.on_composer_cleared()
    assert session.expired
    assert session.expire_reason == "composer_cleared"
    tracker.stop()
    db.close()


def test_on_message_sent():
    db = _make_db()
    tracker = EditTracker(db)

    session = tracker.start_session(
        contact_name="Manav",
        contact_id=None,
        draft_text="sounds good bro",
        model="test",
    )

    tracker.on_message_sent("sounds good bro!")
    assert session.expired
    assert session.expire_reason == "sent"
    assert session.sent_text == "sounds good bro!"
    assert session.similarity > 0.8
    tracker.stop()
    db.close()


def test_draft_saved_to_db():
    db = _make_db()
    cid = db.get_or_create_contact("Manav")
    tracker = EditTracker(db)

    session = tracker.start_session(
        contact_name="Manav",
        contact_id=cid,
        draft_text="test draft",
        model="claude-sonnet-4-6",
    )

    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM drafts WHERE session_uuid = ?", (session.session_uuid,)
    ).fetchone()
    assert row is not None
    assert row["draft_text"] == "test draft"
    assert row["model"] == "claude-sonnet-4-6"
    tracker.stop()
    db.close()


def test_sent_text_saved_to_db():
    db = _make_db()
    cid = db.get_or_create_contact("Manav")
    tracker = EditTracker(db)

    session = tracker.start_session(
        contact_name="Manav",
        contact_id=cid,
        draft_text="sounds good",
        model="test",
    )

    tracker.on_message_sent("sounds great!")
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM drafts WHERE session_uuid = ?", (session.session_uuid,)
    ).fetchone()
    assert row["sent_text"] == "sounds great!"
    assert row["edit_distance"] is not None
    tracker.stop()
    db.close()


# --- process_correction integration ---

def test_process_correction_saves_to_db():
    db = _make_db()
    cid = db.get_or_create_contact("Manav")

    # Set up a style profile
    profile = StyleProfile(avg_message_length_words=5.0, emoji_density=0.5)
    conn = db.connect()
    conn.execute(
        "UPDATE contacts SET style_json = ? WHERE id = ?",
        (profile.to_json(), cid),
    )
    conn.commit()

    tracker = EditTracker(db)
    session = tracker.start_session(
        contact_name="Manav",
        contact_id=cid,
        draft_text="hey whats up man",
        model="test",
    )
    tracker.on_message_sent("hey")

    corrections = process_correction(session, db)
    assert len(corrections) > 0

    # Check corrections in DB
    rows = conn.execute(
        "SELECT * FROM style_corrections WHERE contact_id = ?", (cid,)
    ).fetchall()
    assert len(rows) > 0
    tracker.stop()
    db.close()


def test_process_correction_updates_profile():
    db = _make_db()
    cid = db.get_or_create_contact("Manav")

    profile = StyleProfile(avg_message_length_words=10.0, emoji_density=0.0)
    conn = db.connect()
    conn.execute(
        "UPDATE contacts SET style_json = ? WHERE id = ?",
        (profile.to_json(), cid),
    )
    conn.commit()

    tracker = EditTracker(db)
    session = tracker.start_session(
        contact_name="Manav",
        contact_id=cid,
        draft_text="hey how are you doing today bro",
        model="test",
    )
    # User shortens it significantly
    tracker.on_message_sent("hey bro")

    process_correction(session, db)

    # Profile should have been nudged toward shorter messages
    row = conn.execute("SELECT style_json FROM contacts WHERE id = ?", (cid,)).fetchone()
    updated = StyleProfile.from_json(row["style_json"])
    assert updated.avg_message_length_words < 10.0
    tracker.stop()
    db.close()


# --- helpers ---

def test_count_emojis():
    assert _count_emojis("hello 😂😂 world 🔥") >= 3
    assert _count_emojis("no emojis here") == 0


def test_hindi_word_ratio():
    assert _hindi_word_ratio("haan chal theek hai") > 0.5
    assert _hindi_word_ratio("hello how are you") == 0.0


def test_new_session_expires_previous():
    db = _make_db()
    tracker = EditTracker(db)

    s1 = tracker.start_session("Manav", None, "draft1", "test")
    s2 = tracker.start_session("Rahul", None, "draft2", "test")

    assert s1.expired
    assert s1.expire_reason == "new_session_started"
    assert not s2.expired
    assert tracker.current_session is s2
    tracker.stop()
    db.close()
