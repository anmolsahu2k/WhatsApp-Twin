"""macOS permission checks for Accessibility and Automation."""

import subprocess


def check_accessibility() -> bool:
    """Check if the current process has Accessibility permission."""
    try:
        from ApplicationServices import AXIsProcessTrusted
        return AXIsProcessTrusted()
    except ImportError:
        # Fallback: try to use atomacos
        try:
            import atomacos
            atomacos.getAppRefByBundleId("com.apple.finder")
            return True
        except Exception:
            return False


def check_whatsapp_running() -> bool:
    """Check if WhatsApp Desktop is running."""
    from AppKit import NSWorkspace
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == "net.whatsapp.WhatsApp":
            return True
    return False


def check_whatsapp_frontmost() -> bool:
    """Check if WhatsApp is the frontmost application."""
    from AppKit import NSWorkspace
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    return front and front.bundleIdentifier() == "net.whatsapp.WhatsApp"


def open_accessibility_settings():
    """Open System Settings to the Accessibility pane."""
    subprocess.run([
        "open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    ])


def activate_whatsapp():
    """Bring WhatsApp to the foreground."""
    subprocess.run(["osascript", "-e", 'tell application "WhatsApp" to activate'],
                   capture_output=True)
