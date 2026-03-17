"""Assemble LLM context from chat messages, style profile, and memory."""

from whatsapp_twin.intelligence.style_profile import StyleProfile
from whatsapp_twin.storage.database import Database


def build_conversation_context(
    live_messages: list[dict],
    contact_name: str,
    user_name: str,
    db: Database | None = None,
    contact_id: int | None = None,
    max_messages: int = 50,
) -> str:
    """Build the conversation context string for the LLM prompt.

    Args:
        live_messages: Messages from AX reader [{text, direction, sender, time}].
        contact_name: Current contact's display name.
        user_name: The user's name.
        db: Database for fetching additional context.
        contact_id: Contact ID for DB queries.
        max_messages: Maximum messages to include.

    Returns:
        Formatted conversation string wrapped in XML tags.
    """
    # Format live messages
    lines = []
    for msg in live_messages[-max_messages:]:
        sender = user_name if msg["direction"] == "sent" else (msg.get("sender") or contact_name)
        lines.append(f"{sender}: {msg['text']}")

    conversation = "\n".join(lines)

    # Wrap in XML delimiters for prompt injection defense
    return f"<conversation>\n{conversation}\n</conversation>"


def build_style_context(
    contact_id: int | None,
    db: Database | None,
    user_name: str,
) -> str:
    """Build style profile context for the prompt.

    Includes quantitative metrics and on-demand exemplar selection.
    """
    if not db or not contact_id:
        return ""

    contact = db.get_contact(contact_id)
    if not contact or not contact.get("style_json"):
        return ""

    profile = StyleProfile.from_json(contact["style_json"])
    style_desc = profile.to_prompt_description()

    # Select exemplar messages on-demand (non-expired only)
    exemplars = _select_exemplars(db, contact_id, user_name, count=15)

    parts = [f"<style_profile>\n{style_desc}\n</style_profile>"]

    if exemplars:
        exemplar_text = "\n".join(f"- {e}" for e in exemplars)
        parts.append(f"<exemplar_messages>\n{exemplar_text}\n</exemplar_messages>")

    return "\n\n".join(parts)


def build_memory_context(
    contact_id: int | None,
    db: Database | None,
) -> str:
    """Build memory context (facts, commitments) for the prompt."""
    if not db or not contact_id:
        return ""

    conn = db.connect()
    rows = conn.execute(
        "SELECT category, content FROM memory WHERE contact_id = ? ORDER BY updated_at DESC LIMIT 20",
        (contact_id,),
    ).fetchall()

    if not rows:
        return ""

    lines = [f"- [{r['category']}] {r['content']}" for r in rows]
    return f"<memory>\n" + "\n".join(lines) + "\n</memory>"


def _select_exemplars(
    db: Database,
    contact_id: int,
    user_name: str,
    count: int = 15,
) -> list[str]:
    """Select representative exemplar messages from non-expired messages."""
    msgs = db.get_messages(
        contact_id,
        limit=count * 3,  # fetch more, then filter
        exclude_expired=True,
        max_age_days=90,
    )

    # Filter to user's sent messages only
    user_msgs = [m for m in msgs if m["direction"] == "sent"]

    if not user_msgs:
        return []

    # Pick diverse messages: spread across the time range
    step = max(1, len(user_msgs) // count)
    selected = user_msgs[::step][:count]

    return [m["text"] for m in selected]
