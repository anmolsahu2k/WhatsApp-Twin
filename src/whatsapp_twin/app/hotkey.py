"""Global hotkey registration using Quartz CGEventTap.

Registers Option+Space as the trigger for draft generation.
CGEventTap is used because QuickMacHotKey is a Swift package (not pip-installable).
"""

import threading
from typing import Callable

from whatsapp_twin.config.logging import get_logger

log = get_logger(__name__)

from Quartz import (
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventTapCreate,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRun,
    CGEventTapEnable,
    kCFRunLoopCommonModes,
    kCGEventKeyDown,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

# Key code for Space = 49
# Option flag = 0x80000 (kCGEventFlagMaskAlternate)
_SPACE_KEYCODE = 49
_OPTION_FLAG = 0x80000


class HotkeyListener:
    def __init__(self, callback: Callable[[], None]):
        """Initialize hotkey listener.

        Args:
            callback: Function to call when Option+Space is pressed.
        """
        self._callback = callback
        self._tap = None
        self._thread: threading.Thread | None = None
        self._running = False

    def _event_callback(self, proxy, event_type, event, refcon):
        """CGEventTap callback — fires on every key down event."""
        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        flags = CGEventGetFlags(event)

        # Check: Option held + Space pressed + no other modifiers (Cmd, Ctrl, Shift)
        option_held = bool(flags & _OPTION_FLAG)
        cmd_held = bool(flags & 0x100000)
        ctrl_held = bool(flags & 0x40000)
        shift_held = bool(flags & 0x20000)

        if keycode == _SPACE_KEYCODE and option_held and not cmd_held and not ctrl_held and not shift_held:
            # Fire callback in a separate thread to not block the event tap
            threading.Thread(target=self._callback, daemon=True).start()
            return None  # Consume the event (don't pass to app)

        return event

    def start(self):
        """Start listening for the hotkey in a background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        """Run the CGEventTap in its own run loop."""
        mask = CGEventMaskBit(kCGEventKeyDown)
        self._tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            0,  # active tap (can modify/consume events)
            mask,
            self._event_callback,
            None,
        )

        if self._tap is None:
            log.error("Failed to create CGEventTap. Grant Accessibility permission.")
            self._running = False
            return

        source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(self._tap, True)
        CFRunLoopRun()

    def stop(self):
        """Stop the hotkey listener."""
        self._running = False
        if self._tap:
            CGEventTapEnable(self._tap, False)
