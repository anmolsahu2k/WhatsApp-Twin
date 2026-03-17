"""Parser for WhatsApp .txt chat exports.

WhatsApp exports follow this general pattern:
    DD/MM/YYYY, HH:MM - Sender: Message text
    MM/DD/YY, HH:MM AM - Sender: Message text

Multiline messages continue on the next line without a timestamp prefix.
System messages have no sender (e.g., "Messages and calls are end-to-end encrypted").
Media messages contain "<Media omitted>" or "image omitted", "video omitted", etc.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from whatsapp_twin.storage.models import ParsedMessage

# Matches the start of a WhatsApp message line:
#   [DD/MM/YYYY, HH:MM:SS] or DD/MM/YYYY, HH:MM - or MM/DD/YY, H:MM AM -
# Group 1: date, Group 2: time (with optional AM/PM), Group 3: rest of line
_TIMESTAMP_RE = re.compile(
    r"^\u200e?\[?"               # optional LTR mark + opening bracket
    r"(\d{1,2}/\d{1,2}/\d{2,4})"  # date: D/M/Y or M/D/Y
    r",?\s+"                      # comma + space
    r"(\d{1,2}:\d{2}(?::\d{2})?"  # time: H:MM or H:MM:SS
    r"(?:[\s\u202f]*[APap][Mm])?)"  # optional AM/PM (with narrow no-break space)
    r"\]?\s*(?:[-–]\s*)?"         # optional bracket, optional dash separator
    r"(.+)$",                     # rest of line
    re.MULTILINE,
)

# Splits "Sender: message" — sender cannot contain ":"
# System messages have no sender prefix
_SENDER_RE = re.compile(r"^([^:]+?):\s(.+)$", re.DOTALL)

# Media placeholders
_MEDIA_RE = re.compile(
    r"<?(Media omitted|image omitted|video omitted|audio omitted|"
    r"sticker omitted|document omitted|GIF omitted|Contact card omitted)>?",
    re.IGNORECASE,
)

# System message indicators
_SYSTEM_INDICATORS = [
    "messages and calls are end-to-end encrypted",
    "created group",
    "added you",
    "removed you",
    "left the group",
    "changed the subject",
    "changed this group",
    "changed the group",
    "you were added",
    "security code changed",
    "disappeared",
    "message timer",
    "pinned a message",
]


@dataclass
class DateFormat:
    """Represents a detected date format."""
    order: str  # "DMY" or "MDY"
    year_digits: int  # 2 or 4
    fmt_str: str  # strptime format string


def detect_date_format(lines: list[str]) -> DateFormat:
    """Detect whether dates are DD/MM/YYYY or MM/DD/YYYY by analyzing all timestamps.

    Strategy: if any date has day > 12, it disambiguates the format.
    If all dates are ambiguous (both parts ≤ 12), try both and check chronological order.
    """
    timestamps = []
    for line in lines:
        m = _TIMESTAMP_RE.match(line)
        if m:
            timestamps.append(m.group(1))

    if not timestamps:
        # Default to DD/MM/YYYY (most common internationally)
        return DateFormat("DMY", 4, "%d/%m/%Y")

    # Check for disambiguating dates
    first_parts = []
    second_parts = []
    year_digits = None

    for ts in timestamps:
        parts = ts.split("/")
        first_parts.append(int(parts[0]))
        second_parts.append(int(parts[1]))
        if year_digits is None:
            year_digits = len(parts[2])

    year_digits = year_digits or 4
    year_fmt = "%Y" if year_digits == 4 else "%y"

    max_first = max(first_parts)
    max_second = max(second_parts)

    if max_first > 12 and max_second <= 12:
        # First part must be day (DD/MM)
        return DateFormat("DMY", year_digits, f"%d/%m/{year_fmt}")
    elif max_second > 12 and max_first <= 12:
        # Second part must be day (MM/DD)
        return DateFormat("MDY", year_digits, f"%m/%d/{year_fmt}")
    else:
        # Ambiguous — try both and check chronological order
        # Default to DD/MM (more common for WhatsApp exports from India)
        return DateFormat("DMY", year_digits, f"%d/%m/{year_fmt}")


def _parse_timestamp(date_str: str, time_str: str, date_fmt: DateFormat) -> datetime:
    """Parse a date + time string into a datetime."""
    # Normalize AM/PM spacing and Unicode whitespace
    time_clean = time_str.strip().replace("\u202f", " ")

    # Determine time format
    has_ampm = any(x in time_clean.upper() for x in ["AM", "PM"])
    has_seconds = time_clean.count(":") == 2

    if has_ampm:
        # Normalize: ensure space before AM/PM
        time_clean = re.sub(r"(\d)([APap][Mm])", r"\1 \2", time_clean)
        time_fmt = "%I:%M:%S %p" if has_seconds else "%I:%M %p"
    else:
        time_fmt = "%H:%M:%S" if has_seconds else "%H:%M"

    full_str = f"{date_str} {time_clean}"
    full_fmt = f"{date_fmt.fmt_str} {time_fmt}"

    return datetime.strptime(full_str, full_fmt)


def _is_system_message(text: str) -> bool:
    """Check if a message is a system message (no sender)."""
    text_lower = text.lower()
    return any(indicator in text_lower for indicator in _SYSTEM_INDICATORS)


def parse_export(text: str, user_name: str | None = None) -> list[ParsedMessage]:
    """Parse a WhatsApp .txt export into a list of ParsedMessage objects.

    Args:
        text: Raw content of the export file.
        user_name: The user's display name in the export (to identify sent messages).
                   If None, direction detection is deferred.

    Returns:
        List of ParsedMessage objects in chronological order.
    """
    lines = text.split("\n")
    date_fmt = detect_date_format(lines)

    # First pass: split into raw message blocks by timestamp
    blocks: list[tuple[str, str, str]] = []  # (date, time, rest_of_line)
    continuation_lines: list[str] = []

    for line in lines:
        m = _TIMESTAMP_RE.match(line)
        if m:
            # Save continuation lines to previous block
            if blocks and continuation_lines:
                date, time_str, rest = blocks[-1]
                blocks[-1] = (date, time_str, rest + "\n" + "\n".join(continuation_lines))
                continuation_lines = []
            blocks.append((m.group(1), m.group(2), m.group(3)))
        elif blocks:
            # Continuation of previous message
            if line.strip():
                continuation_lines.append(line)

    # Handle trailing continuation lines
    if blocks and continuation_lines:
        date, time_str, rest = blocks[-1]
        blocks[-1] = (date, time_str, rest + "\n" + "\n".join(continuation_lines))

    # Second pass: parse each block into a message
    messages: list[ParsedMessage] = []

    for date_str, time_str, rest in blocks:
        try:
            ts = _parse_timestamp(date_str, time_str, date_fmt)
        except ValueError:
            continue  # skip unparseable timestamps

        # Check for sender: message pattern
        sender_match = _SENDER_RE.match(rest)

        if sender_match:
            sender = sender_match.group(1).strip()
            msg_text = sender_match.group(2).strip().lstrip("\u200e")
            is_system = False
        else:
            # No sender — system message
            sender = ""
            msg_text = rest.strip().lstrip("\u200e")
            is_system = True

        # Check for system message even with sender format
        if not is_system and _is_system_message(msg_text):
            is_system = True

        is_media = bool(_MEDIA_RE.search(msg_text))

        messages.append(ParsedMessage(
            timestamp=ts,
            sender=sender,
            text=msg_text,
            is_system=is_system,
            is_media=is_media,
        ))

    return messages


def parse_export_file(path: Path, user_name: str | None = None) -> list[ParsedMessage]:
    """Parse a WhatsApp export file from disk."""
    # WhatsApp exports can be UTF-8 or UTF-8 with BOM
    text = path.read_text(encoding="utf-8-sig")
    return parse_export(text, user_name=user_name)


def extract_participants(messages: list[ParsedMessage]) -> set[str]:
    """Extract unique participant names from parsed messages."""
    return {m.sender for m in messages if m.sender and not m.is_system}


def identify_user_name(messages: list[ParsedMessage], known_name: str) -> str | None:
    """Find the user's display name in the export by fuzzy matching against known name."""
    participants = extract_participants(messages)
    known_lower = known_name.lower()

    # Exact match
    for p in participants:
        if p.lower() == known_lower:
            return p

    # Substring match
    for p in participants:
        if known_lower in p.lower() or p.lower() in known_lower:
            return p

    return None
