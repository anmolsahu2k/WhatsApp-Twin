"""OCR fallback using macOS Vision framework.

Used when the Accessibility API fails to read message text from WhatsApp.
Captures the WhatsApp window via CGWindowListCreateImage, then runs
VNRecognizeTextRequest to extract text.
"""

import re

import Quartz
from Foundation import NSURL, NSAutoreleasePool
from Vision import (
    VNImageRequestHandler,
    VNRecognizeTextRequest,
    VNRequestTextRecognitionLevelAccurate,
)


def capture_whatsapp_window() -> Quartz.CGImageRef | None:
    """Capture a screenshot of the WhatsApp window.

    Returns:
        CGImageRef of the window, or None if not found.
    """
    # Find WhatsApp window
    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )

    wa_window = None
    for window in window_list:
        owner = window.get(Quartz.kCGWindowOwnerName, "")
        if owner == "WhatsApp" and window.get(Quartz.kCGWindowLayer, -1) == 0:
            wa_window = window
            break

    if wa_window is None:
        return None

    bounds = wa_window[Quartz.kCGWindowBounds]
    rect = Quartz.CGRectMake(
        bounds["X"], bounds["Y"], bounds["Width"], bounds["Height"]
    )

    image = Quartz.CGWindowListCreateImage(
        rect,
        Quartz.kCGWindowListOptionOnScreenOnly,
        Quartz.kCGNullWindowID,
        Quartz.kCGWindowImageDefault,
    )

    return image


def ocr_image(cg_image) -> list[str]:
    """Run OCR on a CGImage using Vision framework.

    Args:
        cg_image: CGImageRef to process.

    Returns:
        List of recognized text lines, ordered top to bottom.
    """
    pool = NSAutoreleasePool.alloc().init()
    results = []

    try:
        handler = VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, None
        )

        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        request.setRecognitionLanguages_(["en", "hi"])

        success = handler.performRequests_error_([request], None)
        if not success[0]:
            return []

        observations = request.results()
        if not observations:
            return []

        # Sort by Y position (top to bottom — Vision uses bottom-left origin)
        sorted_obs = sorted(
            observations,
            key=lambda o: 1.0 - o.boundingBox().origin.y,
        )

        for obs in sorted_obs:
            candidate = obs.topCandidates_(1)
            if candidate:
                text = candidate[0].string()
                if text and text.strip():
                    results.append(text.strip())
    finally:
        del pool

    return results


def parse_ocr_messages(lines: list[str]) -> list[dict]:
    """Parse OCR text lines into message dicts.

    Attempts to identify message boundaries and directions from visual cues.
    OCR output is less structured than AX, so this is best-effort.

    Returns:
        List of dicts with keys: text, direction (sent/received/unknown), sender.
    """
    messages = []

    # Simple heuristic: group consecutive lines, use timestamp-like patterns
    # to detect message boundaries
    _TIME_RE = re.compile(r"\d{1,2}:\d{2}(?:\s*[APap][Mm])?")

    current_text = []
    for line in lines:
        # Skip UI elements (common WhatsApp UI text)
        if _is_ui_element(line):
            continue

        # Timestamp on its own line often indicates message boundary
        if _TIME_RE.fullmatch(line.strip()):
            if current_text:
                messages.append({
                    "text": " ".join(current_text),
                    "direction": "unknown",
                    "sender": None,
                    "time": line.strip(),
                })
                current_text = []
            continue

        current_text.append(line)

    # Flush remaining
    if current_text:
        messages.append({
            "text": " ".join(current_text),
            "direction": "unknown",
            "sender": None,
            "time": None,
        })

    return messages


def ocr_read_chat() -> list[dict] | None:
    """Full OCR pipeline: capture → OCR → parse.

    Returns:
        List of message dicts, or None if capture/OCR fails.
    """
    image = capture_whatsapp_window()
    if image is None:
        return None

    lines = ocr_image(image)
    if not lines:
        return None

    return parse_ocr_messages(lines)


_UI_KEYWORDS = {
    "type a message", "search", "mute", "disappearing messages",
    "whatsapp", "end-to-end encrypted", "block", "report",
    "media, links, and docs", "starred messages",
}


def _is_ui_element(text: str) -> bool:
    """Check if a text line is likely a UI element rather than a message."""
    lower = text.lower().strip()
    return any(kw in lower for kw in _UI_KEYWORDS) or len(lower) < 2
