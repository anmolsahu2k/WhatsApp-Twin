"""Long-term memory system — facts, commitments, events per contact.

Memory is durable (no auto-expire) but per-contact deletable.
Extraction from chat history is opt-in and uses the Claude API.
"""

import json

from whatsapp_twin.config.logging import get_logger
from whatsapp_twin.storage.database import Database

log = get_logger(__name__)


# Memory categories
CATEGORIES = ["fact", "commitment", "event", "preference", "relationship"]


def add_memory(
    db: Database,
    contact_id: int,
    category: str,
    content: str,
    source: str = "manual",
) -> int:
    """Add a memory entry for a contact.

    Returns:
        The memory row ID.
    """
    conn = db.connect()
    cursor = conn.execute(
        "INSERT INTO memory (contact_id, category, content, source) VALUES (?, ?, ?, ?)",
        (contact_id, category, content, source),
    )
    conn.commit()
    return cursor.lastrowid


def get_memories(
    db: Database,
    contact_id: int,
    category: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Get memory entries for a contact."""
    conn = db.connect()
    if category:
        rows = conn.execute(
            "SELECT id, category, content, source, created_at, updated_at "
            "FROM memory WHERE contact_id = ? AND category = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (contact_id, category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, category, content, source, created_at, updated_at "
            "FROM memory WHERE contact_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (contact_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def update_memory(db: Database, memory_id: int, content: str):
    """Update the content of an existing memory entry."""
    conn = db.connect()
    conn.execute(
        "UPDATE memory SET content = ?, updated_at = datetime('now') WHERE id = ?",
        (content, memory_id),
    )
    conn.commit()


def delete_memory(db: Database, memory_id: int):
    """Delete a memory entry."""
    conn = db.connect()
    conn.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
    conn.commit()


def delete_memories_for_contact(db: Database, contact_id: int):
    """Delete all memory entries for a contact."""
    conn = db.connect()
    conn.execute("DELETE FROM memory WHERE contact_id = ?", (contact_id,))
    conn.commit()


def extract_memories_from_messages(
    messages: list[dict],
    contact_name: str,
    user_name: str,
    claude_client,
    chunk_size: int = 200,
) -> list[dict]:
    """Use Claude to extract facts, commitments, and events from conversation history.

    Args:
        messages: List of message dicts with keys: sender_name, text, direction, timestamp.
        contact_name: The contact's display name.
        user_name: The user's display name.
        claude_client: ClaudeClient instance.
        chunk_size: Number of messages per API call.

    Returns:
        List of dicts with keys: category, content.
    """
    all_memories = []
    total_chunks = (len(messages) + chunk_size - 1) // chunk_size

    # Process in chunks
    for i in range(0, len(messages), chunk_size):
        chunk_num = i // chunk_size + 1
        chunk = messages[i:i + chunk_size]
        log.info("Processing chunk %d/%d (messages %d–%d)",
                 chunk_num, total_chunks, i + 1, i + len(chunk))
        chunk_text = _format_messages_for_extraction(chunk, contact_name, user_name)

        system = (
            "You are extracting key facts and relationship information from a WhatsApp conversation "
            f"between {user_name} and {contact_name}.\n\n"
            "Extract ONLY concrete, useful information. Categories:\n"
            "- fact: personal details (birthday, job, location, family, hobbies)\n"
            "- commitment: plans, promises, things to follow up on\n"
            "- event: significant shared events or milestones\n"
            "- preference: known likes/dislikes, opinions\n"
            "- relationship: nature of relationship, shared context, inside jokes\n\n"
            "Return a JSON array of objects with 'category' and 'content' fields.\n"
            "Each content should be a concise statement (1 sentence max).\n"
            "Return ONLY valid JSON, no markdown fencing. Return [] if nothing noteworthy."
        )

        user_msg = (
            f"Extract key memories from this conversation between {user_name} and {contact_name}:\n\n"
            f"<conversation>\n{chunk_text}\n</conversation>"
        )

        try:
            response = claude_client.generate(system, user_msg, max_tokens=1000)
            memories = _parse_extraction_response(response)
            all_memories.extend(memories)
        except Exception as e:
            log.warning("Memory extraction failed for chunk %d: %s", i, e)
            continue

    # Deduplicate similar memories
    return _deduplicate_memories(all_memories)


def save_extracted_memories(
    db: Database,
    contact_id: int,
    memories: list[dict],
    source: str = "llm_extraction",
):
    """Save extracted memories to the database, skipping duplicates."""
    existing = get_memories(db, contact_id, limit=500)
    existing_contents = {m["content"].lower().strip() for m in existing}

    added = 0
    for mem in memories:
        content = mem.get("content", "").strip()
        category = mem.get("category", "fact")
        if not content:
            continue
        if content.lower() in existing_contents:
            continue
        if category not in CATEGORIES:
            category = "fact"

        add_memory(db, contact_id, category, content, source=source)
        existing_contents.add(content.lower())
        added += 1

    return added


def _format_messages_for_extraction(
    messages: list[dict],
    contact_name: str,
    user_name: str,
) -> str:
    """Format messages for the extraction prompt."""
    lines = []
    for m in messages:
        if m.get("direction") == "sent":
            sender = user_name
        else:
            sender = m.get("sender_name") or contact_name
        lines.append(f"{sender}: {m.get('text', '')}")
    return "\n".join(lines)


def _parse_extraction_response(response: str) -> list[dict]:
    """Parse Claude's extraction response into memory dicts."""
    cleaned = response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]

    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [
                m for m in data
                if isinstance(m, dict) and "content" in m and "category" in m
            ]
    except json.JSONDecodeError:
        pass

    return []


def _deduplicate_memories(memories: list[dict]) -> list[dict]:
    """Remove near-duplicate memories, keeping the first occurrence."""
    seen = set()
    unique = []
    for m in memories:
        key = m.get("content", "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(m)
    return unique
