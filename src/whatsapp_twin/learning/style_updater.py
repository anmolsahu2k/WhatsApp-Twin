"""Style profile updater — learns from user corrections to AI drafts.

Compares draft text vs. sent text, categorizes the corrections,
saves them to the database, and updates the style profile using
exponential moving average so recent corrections have more influence.
"""

import json
import re
import unicodedata
from collections import Counter

from whatsapp_twin.intelligence.style_profile import StyleProfile
from whatsapp_twin.learning.edit_tracker import DraftSession, _text_similarity
from whatsapp_twin.storage.database import Database

# Correction categories
CATEGORIES = ["tone", "length", "emoji", "language", "punctuation", "structure", "content"]

# EMA smoothing factor — higher = more weight on new corrections
EMA_ALPHA = 0.15

# Minimum similarity to consider a sent message as a correction of the draft
# (below this, it's a completely different message — no learning)
MIN_SIMILARITY_FOR_LEARNING = 0.3

# Number of high-confidence corrections before triggering qualitative re-analysis
REANALYSIS_THRESHOLD = 10

# Emoji regex (same as style_analyzer)
_EMOJI_RE = re.compile(
    "[\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "]+",
    flags=re.UNICODE,
)


def process_correction(
    session: DraftSession,
    db: Database,
) -> list[dict]:
    """Analyze differences between draft and sent text, save corrections.

    Args:
        session: Completed DraftSession with sent_text populated.
        db: Database instance.

    Returns:
        List of correction dicts with keys: category, original, corrected.
    """
    if not session.sent_text or session.expired and session.expire_reason != "sent":
        return []

    if session.similarity < MIN_SIMILARITY_FOR_LEARNING:
        return []

    draft = session.draft_text
    sent = session.sent_text
    corrections = categorize_corrections(draft, sent)

    if not corrections:
        return []

    # Save corrections to DB
    draft_id = _get_draft_id(db, session.session_uuid)
    conn = db.connect()
    for corr in corrections:
        conn.execute(
            "INSERT INTO style_corrections (contact_id, draft_id, category, original, corrected) "
            "VALUES (?, ?, ?, ?, ?)",
            (session.contact_id, draft_id, corr["category"],
             corr["original"], corr["corrected"]),
        )
    conn.commit()

    # Update style profile if we have a contact
    if session.contact_id:
        _update_profile_from_corrections(db, session.contact_id, corrections)

        # Check if we should trigger qualitative re-analysis
        _check_reanalysis_threshold(db, session.contact_id)

    return corrections


def categorize_corrections(draft: str, sent: str) -> list[dict]:
    """Compare draft vs sent text and categorize the differences.

    Returns list of correction dicts with: category, original, corrected.
    """
    corrections = []

    # Length correction
    draft_words = len(draft.split())
    sent_words = len(sent.split())
    if draft_words > 0:
        length_ratio = sent_words / draft_words
        if length_ratio < 0.6 or length_ratio > 1.5:
            corrections.append({
                "category": "length",
                "original": f"{draft_words} words",
                "corrected": f"{sent_words} words",
            })

    # Emoji correction
    draft_emojis = _count_emojis(draft)
    sent_emojis = _count_emojis(sent)
    if abs(draft_emojis - sent_emojis) >= 2 or (draft_emojis == 0) != (sent_emojis == 0):
        corrections.append({
            "category": "emoji",
            "original": f"{draft_emojis} emojis",
            "corrected": f"{sent_emojis} emojis",
        })

    # Language correction (Hinglish detection)
    draft_hindi = _hindi_word_ratio(draft)
    sent_hindi = _hindi_word_ratio(sent)
    if abs(draft_hindi - sent_hindi) > 0.15:
        corrections.append({
            "category": "language",
            "original": f"hindi_ratio={draft_hindi:.2f}",
            "corrected": f"hindi_ratio={sent_hindi:.2f}",
        })

    # Punctuation correction
    draft_period = draft.rstrip().endswith(".")
    sent_period = sent.rstrip().endswith(".")
    if draft_period != sent_period:
        corrections.append({
            "category": "punctuation",
            "original": "ends_with_period" if draft_period else "no_period",
            "corrected": "ends_with_period" if sent_period else "no_period",
        })

    # Capitalization correction
    if draft and sent and draft[0].isupper() != sent[0].isupper():
        corrections.append({
            "category": "punctuation",
            "original": "uppercase_start" if draft[0].isupper() else "lowercase_start",
            "corrected": "uppercase_start" if sent[0].isupper() else "lowercase_start",
        })

    # Structure correction (message splitting)
    draft_parts = len(draft.split("[MSG]")) if "[MSG]" in draft else 1
    sent_lines = len([l for l in sent.split("\n") if l.strip()]) if "\n" in sent else 1
    if abs(draft_parts - sent_lines) >= 1 and (draft_parts > 1 or sent_lines > 1):
        corrections.append({
            "category": "structure",
            "original": f"{draft_parts} parts",
            "corrected": f"{sent_lines} parts",
        })

    # Tone/content correction (catch-all for significant text changes)
    similarity = _text_similarity(draft, sent)
    if similarity < 0.8:
        corrections.append({
            "category": "tone",
            "original": draft[:100],
            "corrected": sent[:100],
        })

    return corrections


def _update_profile_from_corrections(
    db: Database,
    contact_id: int,
    corrections: list[dict],
):
    """Update the style profile based on corrections using EMA."""
    conn = db.connect()
    row = conn.execute(
        "SELECT style_json FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()

    if not row or not row["style_json"]:
        return

    profile = StyleProfile.from_json(row["style_json"])

    for corr in corrections:
        cat = corr["category"]

        if cat == "length":
            # Extract the corrected word count and nudge profile toward it
            try:
                corrected_words = int(corr["corrected"].split()[0])
                profile.avg_message_length_words = _ema(
                    profile.avg_message_length_words, corrected_words
                )
            except (ValueError, IndexError):
                pass

        elif cat == "emoji":
            try:
                corrected_count = int(corr["corrected"].split()[0])
                # Nudge emoji density
                profile.emoji_density = _ema(profile.emoji_density, corrected_count)
            except (ValueError, IndexError):
                pass

        elif cat == "language":
            try:
                corrected_ratio = float(corr["corrected"].split("=")[1])
                profile.hinglish_ratio = _ema(profile.hinglish_ratio, corrected_ratio)
            except (ValueError, IndexError):
                pass

        elif cat == "punctuation":
            if "period" in corr["corrected"]:
                target = 1.0 if corr["corrected"] == "ends_with_period" else 0.0
                profile.period_usage_ratio = _ema(profile.period_usage_ratio, target)
            elif "lowercase" in corr["corrected"]:
                profile.capitalization_style = "lowercase"
            elif "uppercase" in corr["corrected"]:
                profile.capitalization_style = "sentence"

    # Save updated profile
    conn.execute(
        "UPDATE contacts SET style_json = ?, updated_at = datetime('now') WHERE id = ?",
        (profile.to_json(), contact_id),
    )
    conn.commit()


def _check_reanalysis_threshold(db: Database, contact_id: int):
    """Check if we've accumulated enough corrections to warrant re-analysis."""
    conn = db.connect()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM style_corrections WHERE contact_id = ?",
        (contact_id,),
    ).fetchone()

    count = row["cnt"] if row else 0
    # Trigger at multiples of the threshold
    if count > 0 and count % REANALYSIS_THRESHOLD == 0:
        print(
            f"[INFO] {count} corrections for contact {contact_id} — "
            f"consider re-running style analysis (whatsapp-twin import --analyze)"
        )


def _ema(old: float, new: float) -> float:
    """Exponential moving average update."""
    return old * (1 - EMA_ALPHA) + new * EMA_ALPHA


def _count_emojis(text: str) -> int:
    """Count emoji characters in text."""
    count = 0
    for match in _EMOJI_RE.finditer(text):
        for char in match.group():
            if unicodedata.category(char).startswith("So") or ord(char) > 0x1F000:
                count += 1
    return count


_HINDI_WORDS = {
    "haan", "nahi", "nhi", "accha", "achha", "acha", "chal", "chalo",
    "bhai", "yaar", "kya", "hai", "hain", "tha", "thi", "kar", "karo",
    "aur", "lekin", "toh", "to", "na", "mat", "bol", "dekh", "sun",
    "abhi", "baad", "sab", "kuch", "bohot", "bahut", "thoda", "zyada",
    "mast", "arrey", "arre", "bata", "batao", "pata", "bilkul", "bas",
    "soch", "dekho", "suno", "theek", "chalega",
}


def _hindi_word_ratio(text: str) -> float:
    """Fraction of words that are Hindi (Roman script)."""
    words = text.lower().split()
    if not words:
        return 0.0
    hindi_count = sum(1 for w in words if w in _HINDI_WORDS)
    return hindi_count / len(words)


def _get_draft_id(db: Database, session_uuid: str) -> int | None:
    """Get the draft row ID for a session UUID."""
    conn = db.connect()
    row = conn.execute(
        "SELECT id FROM drafts WHERE session_uuid = ?", (session_uuid,)
    ).fetchone()
    return row["id"] if row else None
