"""Tests for WhatsApp export parser."""

from datetime import datetime
from pathlib import Path

from whatsapp_twin.ingestion.export_parser import (
    ParsedMessage,
    detect_date_format,
    extract_participants,
    identify_user_name,
    parse_export,
    parse_export_file,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_sample_chat():
    messages = parse_export_file(FIXTURES / "sample_chat.txt")

    # Should parse all lines (1 system + 7 user messages + 1 media + 1 system at end = 10)
    assert len(messages) >= 9

    # First message is system (encryption notice)
    assert messages[0].is_system
    assert "end-to-end encrypted" in messages[0].text.lower()

    # Check a regular message
    rahul_msg = messages[1]
    assert rahul_msg.sender == "Rahul"
    assert rahul_msg.text == "bro gym aaj?"
    assert not rahul_msg.is_system
    assert not rahul_msg.is_media

    # Check sent message
    anmol_msg = messages[2]
    assert anmol_msg.sender == "Anmol"
    assert anmol_msg.text == "haan bhai 6 baje"


def test_multiline_message():
    messages = parse_export_file(FIXTURES / "sample_chat.txt")

    # Find Anmol's multiline message
    multiline = [m for m in messages if "sochte hai" in m.text]
    assert len(multiline) == 1
    assert "abhi tak decide nhi hua" in multiline[0].text
    assert "sochte hai baad me" in multiline[0].text


def test_media_detection():
    messages = parse_export_file(FIXTURES / "sample_chat.txt")
    media_msgs = [m for m in messages if m.is_media]
    assert len(media_msgs) == 1
    assert media_msgs[0].sender == "Anmol"


def test_system_messages():
    messages = parse_export_file(FIXTURES / "sample_chat.txt")
    system_msgs = [m for m in messages if m.is_system]
    assert len(system_msgs) >= 2  # encryption notice + group creation


def test_extract_participants():
    messages = parse_export_file(FIXTURES / "sample_chat.txt")
    participants = extract_participants(messages)
    assert "Anmol" in participants
    assert "Rahul" in participants


def test_identify_user_name():
    messages = parse_export_file(FIXTURES / "sample_chat.txt")

    # Exact match
    assert identify_user_name(messages, "Anmol") == "Anmol"

    # Substring match
    assert identify_user_name(messages, "anmol") == "Anmol"

    # No match
    assert identify_user_name(messages, "Unknown") is None


def test_date_format_detection_dmy():
    """Dates with day > 12 should be detected as DD/MM."""
    lines = [
        "25/03/2026, 14:30 - Test: hello",
        "26/03/2026, 14:31 - Test: world",
    ]
    fmt = detect_date_format(lines)
    assert fmt.order == "DMY"


def test_date_format_detection_mdy():
    """Dates with second part > 12 should be detected as MM/DD."""
    lines = [
        "03/25/2026, 14:30 - Test: hello",
        "03/26/2026, 14:31 - Test: world",
    ]
    fmt = detect_date_format(lines)
    assert fmt.order == "MDY"


def test_date_format_detection_ambiguous():
    """When all dates are ambiguous, default to DD/MM."""
    lines = [
        "01/02/2026, 14:30 - Test: hello",
        "03/04/2026, 14:31 - Test: world",
    ]
    fmt = detect_date_format(lines)
    assert fmt.order == "DMY"  # default for ambiguous


def test_timestamp_with_ampm():
    text = "3/16/26, 2:30 PM - Rahul: hey\n3/16/26, 2:31 PM - Anmol: yo"
    messages = parse_export(text)
    assert len(messages) == 2
    assert messages[0].timestamp.hour == 14


def test_timestamp_with_brackets():
    text = "[16/03/2026, 14:30:00] - Rahul: hey\n[16/03/2026, 14:31:00] - Anmol: yo"
    messages = parse_export(text)
    assert len(messages) == 2
    assert messages[0].sender == "Rahul"


def test_emoji_message():
    text = "16/03/2026, 14:30 - Rahul: 😂😂\n16/03/2026, 14:31 - Anmol: 🔥"
    messages = parse_export(text)
    assert len(messages) == 2
    assert messages[0].text == "😂😂"


def test_empty_export():
    messages = parse_export("")
    assert messages == []
