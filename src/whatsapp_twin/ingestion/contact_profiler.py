"""Build per-contact profiles by combining export parsing + style analysis."""

from pathlib import Path

from whatsapp_twin.config.logging import get_logger
from whatsapp_twin.config.settings import Settings

log = get_logger(__name__)
from whatsapp_twin.ingestion.export_parser import (
    extract_participants,
    identify_user_name,
    parse_export_file,
)
from whatsapp_twin.ingestion.style_analyzer import analyze_style
from whatsapp_twin.storage.database import Database
from whatsapp_twin.storage.models import MessageDirection


def _extract_group_name_from_messages(messages: list) -> str | None:
    """Extract group name from system messages in the chat content.

    Looks for WhatsApp system messages like:
      - 'User created group "Group Name"'
      - 'User changed the subject to "Group Name"'
      - 'User changed the subject from "Old" to "New"'
    Returns the most recent group name found, or None.
    """
    import re

    # Match quoted or unquoted group names
    _SUBJECT_TO_RE = re.compile(
        r'changed the subject to\s*["\u201c]?(.+?)["\u201d]?\s*$', re.IGNORECASE
    )
    _CREATED_GROUP_RE = re.compile(
        r'created group\s*["\u201c]?(.+?)["\u201d]?\s*$', re.IGNORECASE
    )

    group_name = None
    for m in messages:
        if not m.is_system:
            continue
        text = m.text
        match = _SUBJECT_TO_RE.search(text)
        if match:
            group_name = match.group(1).strip()
            continue
        if group_name is None:
            match = _CREATED_GROUP_RE.search(text)
            if match:
                group_name = match.group(1).strip()

    return group_name


def _extract_group_name_from_filename(export_path: Path) -> str:
    """Extract group name from WhatsApp export filename.

    WhatsApp export filenames are typically:
    "WhatsApp Chat with <name>.txt" or "WhatsApp Chat - <name>.txt"
    """
    stem = export_path.stem
    for prefix in ["WhatsApp Chat with ", "WhatsApp Chat - "]:
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return stem


def import_export(
    export_path: Path,
    db: Database,
    settings: Settings,
    group_name: str | None = None,
) -> dict[str, int]:
    """Import a WhatsApp export file into the database.

    Args:
        export_path: Path to the .txt export file.
        db: Database instance.
        settings: App settings.
        group_name: Override group name (auto-detected from filename if None).

    Returns:
        Dict mapping contact/group names to their contact IDs.
    """
    export_name = export_path.name

    if db.has_export(export_name):
        log.info("Export '%s' already imported, skipping", export_name)
        return {}

    messages = parse_export_file(export_path, user_name=settings.user_name)
    if not messages:
        log.warning("No messages found in '%s'", export_name)
        return {}

    # Identify user's name in the export
    user_display_name = identify_user_name(messages, settings.user_name)
    if not user_display_name:
        participants = extract_participants(messages)
        log.warning("Could not identify user '%s' in export. Participants: %s",
                    settings.user_name, participants)
        return {}

    # Identify other participants (contacts)
    participants = extract_participants(messages)
    contact_names = participants - {user_display_name}

    is_group = len(contact_names) > 1

    if is_group:
        if not group_name:
            group_name = _extract_group_name_from_messages(messages)
        if not group_name:
            group_name = _extract_group_name_from_filename(export_path)
        return _import_group(
            messages, export_name, user_display_name, contact_names,
            db, group_name,
        )
    else:
        return _import_individual(
            messages, export_name, user_display_name, contact_names, db,
        )


def _import_individual(
    messages: list,
    export_name: str,
    user_display_name: str,
    contact_names: set[str],
    db: Database,
) -> dict[str, int]:
    """Import a 1-on-1 chat export."""
    contact_ids = {}
    for name in contact_names:
        cid = db.find_contact_by_alias(name)
        if cid is None:
            cid = db.get_or_create_contact(name)
            db.add_alias(cid, name, source="export")
        contact_ids[name] = cid

    db_messages = []
    for m in messages:
        if m.is_system:
            continue

        if m.sender == user_display_name:
            direction = MessageDirection.SENT.value
            cid = contact_ids[next(iter(contact_names))]
        elif m.sender in contact_ids:
            direction = MessageDirection.RECEIVED.value
            cid = contact_ids[m.sender]
        else:
            continue

        db_messages.append((
            cid, direction, m.sender, m.text,
            m.timestamp.isoformat(), "export", export_name,
        ))

    if db_messages:
        db.insert_messages(db_messages)
        log.info("Imported %d messages from '%s'", len(db_messages), export_name)

    return contact_ids


def _import_group(
    messages: list,
    export_name: str,
    user_display_name: str,
    contact_names: set[str],
    db: Database,
    group_name: str,
) -> dict[str, int]:
    """Import a group chat export.

    Creates a single group contact and stores all messages under it.
    User's messages are direction=sent, everyone else's are direction=received.
    """
    cid = db.find_contact_by_alias(group_name)
    if cid is None:
        cid = db.get_or_create_contact(group_name, is_group=True)
        db.add_alias(cid, group_name, source="export")

    db_messages = []
    for m in messages:
        if m.is_system:
            continue
        if not m.sender:
            continue

        if m.sender == user_display_name:
            direction = MessageDirection.SENT.value
        elif m.sender in contact_names:
            direction = MessageDirection.RECEIVED.value
        else:
            continue

        db_messages.append((
            cid, direction, m.sender, m.text,
            m.timestamp.isoformat(), "export", export_name,
        ))

    if db_messages:
        db.insert_messages(db_messages)
        log.info("Imported %d messages (%d members) from group '%s'",
                 len(db_messages), len(contact_names), group_name)

    return {group_name: cid}


def build_style_profile(
    contact_id: int,
    db: Database,
    settings: Settings,
) -> dict:
    """Build a style profile for the user's writing with a specific contact/group.

    Returns the style profile as a dict.
    """
    # Get all messages for this contact
    msgs = db.get_messages(contact_id, limit=5000)

    if not msgs:
        return {}

    from whatsapp_twin.storage.models import ParsedMessage
    from datetime import datetime

    # Convert DB rows to ParsedMessage for the analyzer
    parsed = []
    user_sender_names = set()
    for m in msgs:
        parsed.append(ParsedMessage(
            timestamp=datetime.fromisoformat(m["timestamp"]),
            sender=m["sender_name"],
            text=m["text"],
            is_system=m["direction"] == "system",
        ))
        if m["direction"] == "sent":
            user_sender_names.add(m["sender_name"])

    # Use the actual sender name from DB (e.g., "Anmol Sahu") rather than
    # settings.user_name ("Anmol") which may not match exactly
    user_name_for_analysis = next(iter(user_sender_names)) if user_sender_names else settings.user_name
    profile = analyze_style(parsed, user_name_for_analysis)

    # Save to database
    conn = db.connect()
    conn.execute(
        "UPDATE contacts SET style_json = ?, updated_at = datetime('now') WHERE id = ?",
        (profile.to_json(), contact_id),
    )
    conn.commit()

    return profile.__dict__
