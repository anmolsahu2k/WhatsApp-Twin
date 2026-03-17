"""Quantitative style analysis from message history."""

import re
import unicodedata
from collections import Counter

from whatsapp_twin.intelligence.style_profile import StyleProfile
from whatsapp_twin.storage.models import ParsedMessage

# Common Hindi words in Roman script (expanded set)
_HINDI_WORDS = {
    "haan", "nahi", "nhi", "accha", "achha", "acha", "chal", "chalo",
    "bhai", "yaar", "bro", "kya", "hai", "hain", "tha", "thi",
    "kar", "karo", "karna", "raha", "rahi", "wala", "wali",
    "aur", "lekin", "par", "toh", "to", "na", "mat", "bol",
    "dekh", "sun", "jaa", "ja", "aa", "aaja", "abhi", "baad",
    "pehle", "baaki", "sab", "kuch", "bohot", "bahut", "thoda",
    "zyada", "kam", "accha", "bura", "sahi", "galat", "pakka",
    "mast", "mazza", "arrey", "arre", "oye", "oyee", "bata",
    "batao", "samajh", "pata", "nahi", "bilkul", "bas", "aisa",
    "waisa", "kaisa", "kab", "kaise", "kahan", "kyun", "kyunki",
    "isliye", "waise", "soch", "socho", "dekho", "suno", "padh",
    "likh", "khaa", "khana", "peena", "jaana", "aana", "milte",
    "milenge", "chalega", "theek", "hona", "hogaya", "hogayi",
    "karunga", "karenge", "jayenge", "ayenge", "rehna", "rehte",
}

# Emoji regex
_EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"  # supplemental
    "\U0001FA00-\U0001FA6F"  # chess
    "\U0001FA70-\U0001FAFF"  # extended-A
    "]+",
    flags=re.UNICODE,
)

_LAUGHING_PATTERNS = {
    "haha": re.compile(r"\b(ha){2,}\b", re.IGNORECASE),
    "hehe": re.compile(r"\b(he){2,}\b", re.IGNORECASE),
    "lol": re.compile(r"\blol+\b", re.IGNORECASE),
    "lmao": re.compile(r"\blmao\b", re.IGNORECASE),
    "rofl": re.compile(r"\brofl\b", re.IGNORECASE),
    "😂": re.compile("😂+"),
    "🤣": re.compile("🤣+"),
}


def analyze_style(messages: list[ParsedMessage], user_name: str) -> StyleProfile:
    """Compute quantitative style metrics from the user's messages.

    Args:
        messages: All parsed messages (both sent and received).
        user_name: The user's display name to filter sent messages.

    Returns:
        StyleProfile with computed metrics.
    """
    # Filter to user's messages only (non-system, non-media)
    user_msgs = [
        m for m in messages
        if m.sender == user_name and not m.is_system and not m.is_media
    ]

    if not user_msgs:
        return StyleProfile()

    profile = StyleProfile()
    texts = [m.text for m in user_msgs]

    # -- Message structure --
    char_lengths = [len(t) for t in texts]
    word_lengths = [len(t.split()) for t in texts]
    profile.avg_message_length_chars = sum(char_lengths) / len(char_lengths)
    profile.avg_message_length_words = sum(word_lengths) / len(word_lengths)

    # Messages per turn: count consecutive messages from user
    turns = _count_turns(messages, user_name)
    if turns:
        profile.avg_messages_per_turn = sum(turns) / len(turns)
        profile.split_message_ratio = sum(1 for t in turns if t > 1) / len(turns)

    # -- Language mixing --
    hindi_count = 0
    all_hindi_words: Counter = Counter()
    for text in texts:
        words = set(text.lower().split())
        hindi_in_msg = words & _HINDI_WORDS
        if hindi_in_msg:
            hindi_count += 1
            all_hindi_words.update(hindi_in_msg)

    profile.hinglish_ratio = hindi_count / len(texts)
    profile.common_hindi_words = [w for w, _ in all_hindi_words.most_common(15)]

    if profile.hinglish_ratio > 0.3:
        profile.primary_language = "hinglish"
    elif profile.hinglish_ratio > 0.05:
        profile.primary_language = "en"  # English with occasional Hindi
    else:
        profile.primary_language = "en"

    # -- Emoji --
    emoji_counts: Counter = Counter()
    total_emojis = 0
    for text in texts:
        emojis = _EMOJI_RE.findall(text)
        for emoji_cluster in emojis:
            for char in emoji_cluster:
                if unicodedata.category(char).startswith("So") or ord(char) > 0x1F000:
                    emoji_counts[char] += 1
                    total_emojis += 1

    profile.emoji_density = total_emojis / len(texts)
    profile.top_emojis = [e for e, _ in emoji_counts.most_common(10)]

    # -- Laughing style --
    laugh_counts: Counter = Counter()
    for text in texts:
        for style, pattern in _LAUGHING_PATTERNS.items():
            if pattern.search(text):
                laugh_counts[style] += 1
    if laugh_counts:
        profile.laughing_style = laugh_counts.most_common(1)[0][0]

    # -- Punctuation/casing --
    period_count = sum(1 for t in texts if t.rstrip().endswith("."))
    profile.period_usage_ratio = period_count / len(texts)

    ellipsis_count = sum(1 for t in texts if "..." in t or "…" in t)
    profile.ellipsis_frequency = ellipsis_count / len(texts)

    # Capitalization style
    starts_upper = sum(1 for t in texts if t and t[0].isupper())
    starts_lower = sum(1 for t in texts if t and t[0].islower())
    if starts_lower > starts_upper * 2:
        profile.capitalization_style = "lowercase"
    elif starts_upper > starts_lower * 2:
        profile.capitalization_style = "sentence"
    else:
        profile.capitalization_style = "mixed"

    # -- Abbreviations --
    profile.common_abbreviations = _detect_abbreviations(texts)
    profile.filler_words = _detect_filler_words(texts)
    profile.greeting_patterns = _detect_greetings(texts)
    profile.farewell_patterns = _detect_farewells(texts)

    # -- Sentence rhythm --
    all_words = sum(word_lengths)
    sentence_count = sum(
        len(re.split(r'[.!?]+', t)) for t in texts
    )
    if sentence_count:
        profile.avg_words_per_sentence = all_words / sentence_count

    return profile


def _count_turns(messages: list[ParsedMessage], user_name: str) -> list[int]:
    """Count consecutive messages from user (turn lengths)."""
    turns = []
    current_count = 0
    for m in messages:
        if m.is_system:
            continue
        if m.sender == user_name:
            current_count += 1
        else:
            if current_count > 0:
                turns.append(current_count)
            current_count = 0
    if current_count > 0:
        turns.append(current_count)
    return turns


def _detect_abbreviations(texts: list[str]) -> dict[str, str]:
    """Detect common abbreviations/shorthand in texts."""
    abbrev_map = {
        "u": "you", "ur": "your", "r": "are", "k": "ok",
        "msg": "message", "pls": "please", "plz": "please",
        "tmrw": "tomorrow", "tomo": "tomorrow", "kal": "tomorrow/yesterday",
        "abt": "about", "bc": "because", "cuz": "because",
        "rn": "right now", "ngl": "not gonna lie", "tbh": "to be honest",
        "brb": "be right back", "idk": "I don't know", "imo": "in my opinion",
        "nvm": "never mind", "smth": "something", "sth": "something",
    }

    found = {}
    word_counter = Counter()
    for text in texts:
        words = text.lower().split()
        word_counter.update(words)

    for abbrev, full in abbrev_map.items():
        if word_counter[abbrev] >= 2:  # used at least twice
            found[abbrev] = full

    return found


def _detect_filler_words(texts: list[str]) -> list[str]:
    """Detect common filler words."""
    fillers = ["like", "basically", "actually", "literally", "honestly",
               "legit", "lowkey", "highkey", "ngl", "tbh", "fr"]
    counter = Counter()
    for text in texts:
        words = text.lower().split()
        for w in words:
            if w in fillers:
                counter[w] += 1
    return [w for w, c in counter.most_common() if c >= 2]


def _detect_greetings(texts: list[str]) -> list[str]:
    """Detect greeting patterns."""
    greetings = ["hey", "hi", "hello", "yo", "sup", "heyyy", "heyy",
                 "hii", "hiiii", "wassup", "namaste"]
    found = []
    for g in greetings:
        for text in texts:
            if text.lower().startswith(g):
                found.append(g)
                break
    return found


def _detect_farewells(texts: list[str]) -> list[str]:
    """Detect farewell patterns."""
    farewells = ["bye", "cya", "later", "goodnight", "gn", "chal",
                 "ttyl", "tc", "take care", "night"]
    found = []
    for f in farewells:
        for text in texts:
            if f in text.lower():
                found.append(f)
                break
    return found
