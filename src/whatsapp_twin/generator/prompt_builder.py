"""Build the system and user prompts for draft generation."""

from whatsapp_twin.intelligence.context_builder import (
    build_conversation_context,
    build_memory_context,
    build_style_context,
)
from whatsapp_twin.storage.database import Database

SYSTEM_TEMPLATE = """You are ghostwriting a WhatsApp reply for {user_name}. You must perfectly match {user_name}'s exact texting style with {contact_name}.

STRICT RULES:
1. Output ONLY the raw message text — no preamble, no analysis, no explanations, no quotes, no prefixes, no "Looking at..." or "Here's..." or any meta-commentary
2. Your ENTIRE response must be exactly what {user_name} would type and send — nothing more
3. Match {user_name}'s exact capitalization, punctuation, and emoji usage
4. Match the language (English, Hindi in Roman script, or mix) they use with this contact
5. Match their typical message length and splitting pattern
6. If they typically send multiple short messages, separate them with [MSG] delimiter
7. Be natural — this should be indistinguishable from how {user_name} actually texts

{style_context}

{memory_context}"""

GROUP_SYSTEM_TEMPLATE = """You are ghostwriting a WhatsApp reply for {user_name} in the group chat "{group_name}". You must perfectly match {user_name}'s exact texting style in this group.

STRICT RULES:
1. Output ONLY the raw message text — no preamble, no analysis, no explanations, no quotes, no prefixes, no "Looking at..." or "Here's..." or any meta-commentary
2. Your ENTIRE response must be exactly what {user_name} would type and send — nothing more
3. Match {user_name}'s exact capitalization, punctuation, and emoji usage
4. Match the language (English, Hindi in Roman script, or mix) they use in this group
5. Match their typical message length and splitting pattern
6. If they typically send multiple short messages, separate them with [MSG] delimiter
7. Be natural — this should be indistinguishable from how {user_name} actually texts
8. Consider the group dynamics — who's talking, the topic, and the vibe of the conversation
9. Reply to the flow of the group conversation, not to a single person (unless the context makes it clear)

{style_context}

{memory_context}"""

USER_TEMPLATE = """Here is the recent conversation:

{conversation}

Write a reply as {user_name} would send it to {contact_name}. Output ONLY the message text."""

GROUP_USER_TEMPLATE = """Here is the recent group conversation:

{conversation}

Write a reply as {user_name} would send it in the group "{group_name}". Output ONLY the message text."""


def build_prompts(
    live_messages: list[dict],
    contact_name: str,
    user_name: str,
    db: Database | None = None,
    contact_id: int | None = None,
    is_group: bool = False,
) -> tuple[str, str]:
    """Build system and user prompts for draft generation.

    Args:
        live_messages: Messages from AX reader.
        contact_name: Contact or group display name.
        user_name: The user's name.
        db: Database for fetching style/memory context.
        contact_id: Contact ID for DB queries.
        is_group: Whether this is a group chat.

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    conversation = build_conversation_context(
        live_messages, contact_name, user_name, db, contact_id,
    )

    style_context = build_style_context(contact_id, db, user_name)
    memory_context = build_memory_context(contact_id, db)

    if is_group:
        system = GROUP_SYSTEM_TEMPLATE.format(
            user_name=user_name,
            group_name=contact_name,
            style_context=style_context,
            memory_context=memory_context,
        )
        user = GROUP_USER_TEMPLATE.format(
            conversation=conversation,
            user_name=user_name,
            group_name=contact_name,
        )
    else:
        system = SYSTEM_TEMPLATE.format(
            user_name=user_name,
            contact_name=contact_name,
            style_context=style_context,
            memory_context=memory_context,
        )
        user = USER_TEMPLATE.format(
            conversation=conversation,
            user_name=user_name,
            contact_name=contact_name,
        )

    return system, user
