"""Data models for WhatsApp Twin."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MessageDirection(Enum):
    SENT = "sent"
    RECEIVED = "received"
    SYSTEM = "system"


@dataclass
class Contact:
    id: int | None = None
    canonical_name: str = ""
    phone: str | None = None
    relationship_type: str | None = None
    language_preference: str | None = None
    typical_topics: str | None = None
    style_json: str | None = None  # serialized StyleProfile
    their_style_json: str | None = None
    is_group: bool = False
    excluded: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ContactAlias:
    id: int | None = None
    contact_id: int = 0
    alias_name: str = ""
    source: str = ""  # "export", "live_ax", "manual"
    created_at: datetime | None = None


@dataclass
class Message:
    id: int | None = None
    contact_id: int = 0
    direction: MessageDirection = MessageDirection.RECEIVED
    sender_name: str = ""
    text: str = ""
    timestamp: datetime | None = None
    source: str = ""  # "export", "live_ax"
    export_file: str | None = None
    created_at: datetime | None = None


@dataclass
class Draft:
    id: int | None = None
    contact_id: int = 0
    session_uuid: str = ""
    draft_text: str = ""
    sent_text: str | None = None
    edit_distance: float | None = None
    model: str = ""
    created_at: datetime | None = None


@dataclass
class StyleCorrection:
    id: int | None = None
    contact_id: int = 0
    draft_id: int = 0
    category: str = ""  # "tone", "length", "emoji", "language", "other"
    original: str = ""
    corrected: str = ""
    created_at: datetime | None = None


@dataclass
class Memory:
    id: int | None = None
    contact_id: int = 0
    category: str = ""  # "fact", "commitment", "event", "preference"
    content: str = ""
    source: str = ""  # "llm_extract", "manual"
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ParsedMessage:
    """Intermediate representation from export parser, before DB insertion."""
    timestamp: datetime
    sender: str
    text: str
    is_system: bool = False
    is_media: bool = False
