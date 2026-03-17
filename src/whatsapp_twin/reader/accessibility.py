"""WhatsApp Accessibility reader — reads chat messages and contact info via AX API.

Key findings from Phase 0 spike:
- WhatsApp is a Catalyst app; app.windows() returns []. Use app.AXMainWindow instead.
- Messages are AXStaticText with id=WAMessageBubbleTableViewCell, text in desc attribute.
- Contact name is in AXHeading with id=NavigationBar_HeaderViewButton, in desc attribute.
- Composer is AXTextArea with id=ChatBar_ComposerTextView (NOT settable via AX).
"""

import re
from dataclasses import dataclass

import atomacos

from whatsapp_twin.config.logging import get_logger

log = get_logger(__name__)


@dataclass
class ChatContext:
    """Current chat state read from WhatsApp's accessibility tree."""
    contact_name: str
    messages: list[dict]  # [{sender, text, time, direction}]
    composer_element: object | None = None  # AX element reference
    is_group: bool = False


def detect_group_chat(messages: list[dict]) -> bool:
    """Detect if messages are from a group chat.

    Group chats have multiple distinct senders in received messages
    (the "Message from X" AX format populates the sender field).
    """
    received_senders = {
        m["sender"] for m in messages
        if m["direction"] == "received" and m.get("sender")
    }
    return len(received_senders) > 1


def get_whatsapp_app():
    """Get AX reference to WhatsApp."""
    try:
        return atomacos.getAppRefByBundleId("net.whatsapp.WhatsApp")
    except Exception:
        return None


def get_main_window(app):
    """Get WhatsApp's main window (Catalyst workaround)."""
    try:
        return app.AXMainWindow
    except Exception:
        return None


def _find_by_id(element, target_id: str, role: str | None = None, max_depth: int = 8):
    """Recursively find an element by AXIdentifier."""
    if max_depth <= 0:
        return None

    try:
        eid = getattr(element, "AXIdentifier", None)
        erole = getattr(element, "AXRole", None)
        if eid == target_id and (role is None or erole == role):
            return element
    except Exception:
        pass

    try:
        children = element.AXChildren or []
        for child in children:
            result = _find_by_id(child, target_id, role, max_depth - 1)
            if result:
                return result
    except Exception:
        pass

    return None


def _find_all_by_id(element, target_id: str, max_depth: int = 8) -> list:
    """Recursively find all elements with a given AXIdentifier."""
    results = []
    if max_depth <= 0:
        return results

    try:
        if getattr(element, "AXIdentifier", None) == target_id:
            results.append(element)
    except Exception:
        pass

    try:
        children = element.AXChildren or []
        for child in children:
            results.extend(_find_all_by_id(child, target_id, max_depth - 1))
    except Exception:
        pass

    return results


# Parse message description format (after LTR/RTL stripping):
# "Your message, <text>, <datetime>, Sent to <contact>, <read_status>"
# "message, <text>, <datetime>, Received from <contact>"
# "Message from <sender>, <text>, <datetime>"
# Datetime can be: "1:47 AM", "March14,at1:47 AM", "March 14, at 1:47 AM", etc.
_DATETIME_PATTERN = r"[A-Za-z]*\d{0,2},?\s*(?:at\s*)?\d{1,2}:\d{2}[\s\u202f]*(?:AM|PM)?"

_MSG_SENT_RE = re.compile(
    r"Your message,\s*(.+?),\s*(" + _DATETIME_PATTERN + r")"
    r"(?:,\s*Sent to .+)?$",
    re.DOTALL,
)
_MSG_RECEIVED_RE = re.compile(
    r"[Mm]essage,\s*(.+?),\s*(" + _DATETIME_PATTERN + r")"
    r"(?:,\s*Received from (.+?))?(?:,\s*\w+)?$",
    re.DOTALL,
)
_MSG_FROM_RE = re.compile(
    r"Message from (.+?),\s*(.+?),\s*(" + _DATETIME_PATTERN + r")",
    re.DOTALL,
)


def _parse_message_desc(desc: str) -> dict | None:
    """Parse a message bubble's AXDescription into structured data."""
    if not desc:
        return None

    # Strip Unicode LTR/RTL markers
    clean = desc.replace("\u200f", "").replace("\u200e", "").strip()

    # Try "Your message" (sent)
    m = _MSG_SENT_RE.search(clean)
    if m:
        return {
            "text": m.group(1).strip(),
            "time": m.group(2).strip(),
            "direction": "sent",
            "sender": None,
        }

    # Try "Message from X" (group received or with sender prefix)
    m = _MSG_FROM_RE.search(clean)
    if m:
        return {
            "sender": m.group(1).strip(),
            "text": m.group(2).strip(),
            "time": m.group(3).strip(),
            "direction": "received",
        }

    # Try "message" (received, 1-on-1)
    m = _MSG_RECEIVED_RE.search(clean)
    if m:
        return {
            "text": m.group(1).strip(),
            "time": m.group(2).strip(),
            "direction": "received",
            "sender": m.group(3).strip() if m.group(3) else None,
        }

    return None


def read_current_chat(app=None) -> ChatContext | None:
    """Read the current chat from WhatsApp's accessibility tree.

    Returns:
        ChatContext with contact name, messages, and composer element.
        None if WhatsApp is not available or no chat is open.
    """
    if app is None:
        app = get_whatsapp_app()
    if app is None:
        return None

    window = get_main_window(app)
    if window is None:
        return None

    # Find contact name
    heading = _find_by_id(window, "NavigationBar_HeaderViewButton", role="AXHeading")
    if heading is None:
        return None

    contact_name = getattr(heading, "AXDescription", None) or ""
    # Strip LTR/RTL markers
    contact_name = contact_name.replace("\u200f", "").replace("\u200e", "").strip()
    if not contact_name:
        return None

    # Find message bubbles
    bubbles = _find_all_by_id(window, "WAMessageBubbleTableViewCell")
    messages = []
    for bubble in bubbles:
        desc = getattr(bubble, "AXDescription", None) or ""
        parsed = _parse_message_desc(desc)
        if parsed:
            messages.append(parsed)

    # OCR fallback: if AX found no messages, try Vision framework OCR
    if not messages:
        try:
            from whatsapp_twin.reader.ocr_fallback import ocr_read_chat
            ocr_messages = ocr_read_chat()
            if ocr_messages:
                messages = ocr_messages
                log.info("AX found no messages, OCR recovered %d", len(messages))
        except Exception as e:
            log.warning("OCR fallback failed: %s", e)

    # Find composer
    composer = _find_by_id(window, "ChatBar_ComposerTextView", role="AXTextArea")

    is_group = detect_group_chat(messages)

    return ChatContext(
        contact_name=contact_name,
        messages=messages,
        composer_element=composer,
        is_group=is_group,
    )
