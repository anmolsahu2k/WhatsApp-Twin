"""Tests for style analyzer."""

from datetime import datetime

from whatsapp_twin.ingestion.style_analyzer import analyze_style
from whatsapp_twin.intelligence.style_profile import StyleProfile
from whatsapp_twin.storage.models import ParsedMessage


def _msg(sender: str, text: str, minutes_offset: int = 0) -> ParsedMessage:
    return ParsedMessage(
        timestamp=datetime(2026, 3, 16, 14, minutes_offset),
        sender=sender,
        text=text,
    )


def test_basic_style_analysis():
    messages = [
        _msg("Anmol", "haan bhai 6 baje", 0),
        _msg("Rahul", "done, see you there", 1),
        _msg("Anmol", "chal gym me milte hai", 2),
        _msg("Rahul", "ok cool", 3),
        _msg("Anmol", "btw kal ka plan kya hai", 4),
    ]

    profile = analyze_style(messages, "Anmol")

    assert profile.avg_message_length_words > 0
    assert profile.avg_message_length_chars > 0
    assert profile.hinglish_ratio > 0  # all Anmol msgs have Hindi words
    assert len(profile.common_hindi_words) > 0


def test_emoji_detection():
    messages = [
        _msg("Anmol", "haha nice 😂😂", 0),
        _msg("Anmol", "yesss 🔥", 1),
        _msg("Anmol", "lets goooo", 2),
    ]

    profile = analyze_style(messages, "Anmol")
    assert profile.emoji_density > 0
    assert "😂" in profile.top_emojis


def test_laughing_style():
    messages = [
        _msg("Anmol", "hahaha that's funny", 0),
        _msg("Anmol", "haha no way", 1),
        _msg("Anmol", "lol ok", 2),
    ]

    profile = analyze_style(messages, "Anmol")
    assert profile.laughing_style == "haha"  # more haha than lol


def test_capitalization_detection():
    messages = [
        _msg("Anmol", "hey what's up", 0),
        _msg("Anmol", "nothing much bro", 1),
        _msg("Anmol", "chal baad me baat karte", 2),
    ]

    profile = analyze_style(messages, "Anmol")
    assert profile.capitalization_style == "lowercase"


def test_turn_counting():
    messages = [
        _msg("Anmol", "hey", 0),
        _msg("Anmol", "kya kar raha hai", 1),  # 2-message turn
        _msg("Rahul", "nothing much", 2),
        _msg("Anmol", "ok", 3),  # 1-message turn
    ]

    profile = analyze_style(messages, "Anmol")
    assert profile.avg_messages_per_turn == 1.5  # (2+1)/2
    assert profile.split_message_ratio == 0.5  # 1 of 2 turns is split


def test_empty_messages():
    profile = analyze_style([], "Anmol")
    assert profile.avg_message_length_words == 0


def test_only_other_person_messages():
    messages = [
        _msg("Rahul", "hey bro", 0),
        _msg("Rahul", "gym aaj?", 1),
    ]
    profile = analyze_style(messages, "Anmol")
    assert profile.avg_message_length_words == 0  # no user messages


def test_style_profile_serialization():
    profile = StyleProfile(
        avg_message_length_words=5.5,
        hinglish_ratio=0.4,
        common_hindi_words=["bhai", "haan"],
        top_emojis=["😂", "🔥"],
    )

    json_str = profile.to_json()
    restored = StyleProfile.from_json(json_str)

    assert restored.avg_message_length_words == 5.5
    assert restored.hinglish_ratio == 0.4
    assert restored.common_hindi_words == ["bhai", "haan"]
    assert restored.top_emojis == ["😂", "🔥"]


def test_style_profile_prompt_description():
    profile = StyleProfile(
        avg_message_length_words=5.0,
        avg_messages_per_turn=2.5,
        hinglish_ratio=0.45,
        common_hindi_words=["bhai", "haan", "chal"],
        emoji_density=0.8,
        top_emojis=["😂", "🔥"],
        laughing_style="haha",
        capitalization_style="lowercase",
        period_usage_ratio=0.05,
    )

    desc = profile.to_prompt_description()
    assert "5 words" in desc
    assert "2.5 messages per turn" in desc
    assert "Hindi" in desc
    assert "bhai" in desc
    assert "lowercase" in desc
    assert "Rarely uses periods" in desc


from whatsapp_twin.ingestion.style_analyzer import incremental_style_update


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
