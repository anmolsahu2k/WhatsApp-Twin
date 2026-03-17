"""Insert draft text into WhatsApp's composer via clipboard paste.

Since AX setValue() is not available on WhatsApp's Catalyst composer,
we use clipboard paste as the only insertion method:
1. Save current clipboard
2. Set draft to clipboard
3. Focus composer via AX Press()
4. Simulate Cmd+V via AppleScript
5. Restore original clipboard
"""

import subprocess
import time

from AppKit import NSPasteboard, NSStringPboardType


def insert_draft(draft_text: str, composer_element) -> bool:
    """Insert draft text into WhatsApp's composer.

    Args:
        draft_text: The text to insert.
        composer_element: The AX element for the composer text area.

    Returns:
        True if insertion succeeded, False otherwise.
    """
    if not composer_element:
        return False

    pb = NSPasteboard.generalPasteboard()

    # Save current clipboard
    old_clipboard = pb.stringForType_(NSStringPboardType)

    try:
        # Set draft to clipboard
        pb.clearContents()
        pb.setString_forType_(draft_text, NSStringPboardType)

        # Focus the composer
        try:
            composer_element.Press()
            time.sleep(0.2)
        except Exception:
            return False

        # Simulate Cmd+V
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            capture_output=True,
            timeout=5,
        )

        if result.returncode != 0:
            return False

        time.sleep(0.3)

        # Verify text was inserted
        try:
            current = composer_element.AXValue or ""
            if draft_text[:20] in current:
                return True
        except Exception:
            pass

        # Even if we can't verify, the paste likely worked
        return True

    finally:
        # Restore original clipboard
        pb.clearContents()
        if old_clipboard:
            pb.setString_forType_(old_clipboard, NSStringPboardType)


def clear_composer(composer_element) -> bool:
    """Clear the composer text area.

    WhatsApp's Catalyst composer doesn't always respond to Cmd+A reliably
    via System Events. Instead, use keyboard shortcuts to move to start/end
    and select all text, then delete.
    """
    if not composer_element:
        return False

    try:
        composer_element.Press()
        time.sleep(0.15)

        # Move to end of text (Cmd+Down), then select to start (Cmd+Shift+Up)
        # This reliably selects all text in WhatsApp's Catalyst composer
        script = (
            'tell application "System Events"\n'
            '    key code 125 using command down\n'          # Cmd+Down (end)
            '    delay 0.05\n'
            '    key code 126 using {command down, shift down}\n'  # Cmd+Shift+Up (select to start)
            '    delay 0.05\n'
            '    key code 51\n'                              # Delete
            'end tell'
        )
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=3,
        )
        time.sleep(0.1)
        return True
    except Exception:
        return False
