#!/usr/bin/env python3
"""
Phase 0 Technical Spike — WhatsApp Twin
========================================
Tests:
1. WhatsApp accessibility tree traversal
2. Reading chat messages from AX API
3. Reading contact name from header
4. Finding the input text field
5. AX setValue() vs clipboard paste for text insertion
6. Global hotkey registration (QuickMacHotKey or fallback)
7. End-to-end latency measurement

Run with: python spike.py
Requires: Accessibility permission granted to Terminal/IDE
"""

import time
import subprocess
import sys

# ============================================================
# 1. Check if WhatsApp is running
# ============================================================
def check_whatsapp_running():
    """Check if WhatsApp Desktop is running."""
    from AppKit import NSWorkspace

    running_apps = NSWorkspace.sharedWorkspace().runningApplications()
    for app in running_apps:
        if app.bundleIdentifier() == "net.whatsapp.WhatsApp":
            print(f"[OK] WhatsApp is running (PID: {app.processIdentifier()})")
            return True

    print("[FAIL] WhatsApp is NOT running. Please open it first.")
    return False


# ============================================================
# 2. Get WhatsApp via atomacos and dump AX hierarchy
# ============================================================
def explore_ax_hierarchy():
    """Traverse and print WhatsApp's accessibility tree."""
    import atomacos

    print("\n--- WhatsApp Accessibility Hierarchy ---")
    try:
        app = atomacos.getAppRefByBundleId("net.whatsapp.WhatsApp")
        print(f"[OK] Got AX reference to WhatsApp")
    except Exception as e:
        print(f"[FAIL] Could not get AX reference: {e}")
        print("  -> Make sure Accessibility is enabled for this terminal")
        return None

    # Get the main window
    try:
        windows = app.windows()
        if not windows:
            print("[FAIL] No windows found")
            return None
        window = windows[0]
        print(f"[OK] Main window: {window.AXTitle if hasattr(window, 'AXTitle') else 'untitled'}")
    except Exception as e:
        print(f"[FAIL] Could not get windows: {e}")
        return None

    # Dump the first few levels of the hierarchy
    print("\n--- Top-level AX Tree (3 levels deep) ---")
    dump_ax_tree(window, max_depth=3)

    return app


def dump_ax_tree(element, depth=0, max_depth=4):
    """Recursively print AX element tree."""
    if depth > max_depth:
        return

    indent = "  " * depth
    try:
        role = element.AXRole if hasattr(element, 'AXRole') else "?"
        title = ""
        value = ""
        desc = ""

        try:
            title = element.AXTitle or ""
        except:
            pass
        try:
            value = str(element.AXValue or "")[:80]
        except:
            pass
        try:
            desc = element.AXDescription or ""
        except:
            pass

        info_parts = [f"role={role}"]
        if title:
            info_parts.append(f"title='{title}'")
        if value:
            info_parts.append(f"value='{value}'")
        if desc:
            info_parts.append(f"desc='{desc}'")

        print(f"{indent}{' | '.join(info_parts)}")

        # Get children
        try:
            children = element.AXChildren or []
            for child in children:
                dump_ax_tree(child, depth + 1, max_depth)
        except:
            pass
    except Exception as e:
        print(f"{indent}[error reading element: {e}]")


# ============================================================
# 3. Find and read chat messages
# ============================================================
def find_chat_messages(app):
    """Try to extract visible chat messages from WhatsApp."""
    print("\n--- Attempting to read chat messages ---")

    window = app.windows()[0]

    # Strategy 1: Find all StaticText elements
    print("\nStrategy 1: Finding all AXStaticText elements...")
    try:
        all_texts = window.findAllR(AXRole="AXStaticText")
        print(f"  Found {len(all_texts)} StaticText elements")

        # Print them with their values
        for i, text_elem in enumerate(all_texts[:30]):  # First 30
            try:
                val = text_elem.AXValue or ""
                if val and len(val) > 2:  # Skip single chars
                    print(f"  [{i}] '{val[:100]}'")
            except:
                pass
    except Exception as e:
        print(f"  [FAIL] {e}")

    # Strategy 2: Find scroll areas (chat area is usually one)
    print("\nStrategy 2: Finding AXScrollArea elements...")
    try:
        scroll_areas = window.findAllR(AXRole="AXScrollArea")
        print(f"  Found {len(scroll_areas)} ScrollArea elements")
        for i, sa in enumerate(scroll_areas):
            try:
                size = sa.AXSize
                pos = sa.AXPosition
                print(f"  ScrollArea[{i}]: pos=({pos[0]:.0f},{pos[1]:.0f}) size=({size[0]:.0f}x{size[1]:.0f})")

                # Check children
                children = sa.AXChildren or []
                print(f"    Children: {len(children)}")
                for j, child in enumerate(children[:5]):
                    try:
                        role = child.AXRole
                        val = ""
                        try:
                            val = str(child.AXValue or "")[:50]
                        except:
                            pass
                        print(f"    [{j}] role={role} val='{val}'")
                    except:
                        pass
            except Exception as e:
                print(f"  [error] {e}")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # Strategy 3: Find groups that might be message bubbles
    print("\nStrategy 3: Finding AXGroup elements in largest scroll area...")
    try:
        scroll_areas = window.findAllR(AXRole="AXScrollArea")
        if scroll_areas:
            # Pick the largest scroll area (likely the chat area)
            largest = max(scroll_areas, key=lambda sa: sa.AXSize[0] * sa.AXSize[1])
            groups = largest.findAllR(AXRole="AXGroup")
            print(f"  Found {len(groups)} Group elements in largest scroll area")

            for i, group in enumerate(groups[:10]):
                texts = group.findAll(AXRole="AXStaticText")
                if texts:
                    vals = []
                    for t in texts:
                        try:
                            v = t.AXValue or ""
                            if v:
                                vals.append(v[:60])
                        except:
                            pass
                    if vals:
                        print(f"  Group[{i}]: {vals}")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # Strategy 4: Find AXTextArea (for input box)
    print("\nStrategy 4: Finding AXTextArea elements (input box)...")
    try:
        text_areas = window.findAllR(AXRole="AXTextArea")
        print(f"  Found {len(text_areas)} TextArea elements")
        for i, ta in enumerate(text_areas):
            try:
                val = ta.AXValue or ""
                placeholder = ""
                try:
                    placeholder = ta.AXPlaceholderValue or ""
                except:
                    pass
                print(f"  TextArea[{i}]: value='{val[:50]}' placeholder='{placeholder}'")
            except Exception as e:
                print(f"  TextArea[{i}]: [error] {e}")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # Strategy 5: Find AXTextField (alternative input)
    print("\nStrategy 5: Finding AXTextField elements...")
    try:
        text_fields = window.findAllR(AXRole="AXTextField")
        print(f"  Found {len(text_fields)} TextField elements")
        for i, tf in enumerate(text_fields):
            try:
                val = tf.AXValue or ""
                placeholder = ""
                try:
                    placeholder = tf.AXPlaceholderValue or ""
                except:
                    pass
                print(f"  TextField[{i}]: value='{val[:50]}' placeholder='{placeholder}'")
            except Exception as e:
                print(f"  TextField[{i}]: [error] {e}")
    except Exception as e:
        print(f"  [FAIL] {e}")


# ============================================================
# 4. Find contact name
# ============================================================
def find_contact_name(app):
    """Try to extract the current conversation's contact name."""
    print("\n--- Attempting to find contact name ---")

    window = app.windows()[0]

    # Look for headings
    try:
        headings = window.findAllR(AXRole="AXHeading")
        print(f"  Found {len(headings)} AXHeading elements")
        for h in headings:
            try:
                print(f"    Heading: '{h.AXValue or h.AXTitle or ''}'")
            except:
                pass
    except:
        pass

    # Look for navigation bar / toolbar area
    try:
        toolbars = window.findAllR(AXRole="AXToolbar")
        print(f"  Found {len(toolbars)} AXToolbar elements")
        for tb in toolbars:
            texts = tb.findAllR(AXRole="AXStaticText")
            for t in texts:
                try:
                    val = t.AXValue or ""
                    if val:
                        print(f"    Toolbar text: '{val}'")
                except:
                    pass
    except:
        pass

    # Look at the window title
    try:
        title = window.AXTitle
        print(f"  Window title: '{title}'")
    except:
        pass


# ============================================================
# 5. Test text insertion
# ============================================================
def test_text_insertion(app):
    """Test inserting text into WhatsApp's input box."""
    print("\n--- Testing text insertion ---")

    window = app.windows()[0]

    # Find the input element
    input_elem = None

    # Try AXTextArea first
    try:
        text_areas = window.findAllR(AXRole="AXTextArea")
        for ta in text_areas:
            try:
                placeholder = ta.AXPlaceholderValue or ""
                if "message" in placeholder.lower() or "type" in placeholder.lower():
                    input_elem = ta
                    print(f"  [OK] Found input via AXTextArea (placeholder: '{placeholder}')")
                    break
            except:
                pass
        if not input_elem and text_areas:
            input_elem = text_areas[-1]  # Last text area is usually the input
            print(f"  [OK] Using last AXTextArea as input")
    except:
        pass

    # Try AXTextField if no TextArea found
    if not input_elem:
        try:
            text_fields = window.findAllR(AXRole="AXTextField")
            for tf in text_fields:
                try:
                    placeholder = tf.AXPlaceholderValue or ""
                    if "message" in placeholder.lower() or "type" in placeholder.lower():
                        input_elem = tf
                        print(f"  [OK] Found input via AXTextField (placeholder: '{placeholder}')")
                        break
                except:
                    pass
        except:
            pass

    if not input_elem:
        print("  [FAIL] Could not find input element")
        return

    test_text = "Hello from WhatsApp Twin spike test!"

    # Method 1: AX setValue
    print(f"\n  Method 1: AX setValue('{test_text[:30]}...')")
    try:
        input_elem.AXValue = test_text
        time.sleep(0.5)
        current_val = input_elem.AXValue or ""
        if test_text in current_val:
            print(f"  [OK] AX setValue WORKS! Value is now: '{current_val[:50]}'")
        else:
            print(f"  [PARTIAL] setValue called but value is: '{current_val[:50]}'")
            print(f"  -> AX setValue may not work for this Catalyst app")
    except Exception as e:
        print(f"  [FAIL] AX setValue failed: {e}")

    # Clear it
    try:
        input_elem.AXValue = ""
        time.sleep(0.3)
    except:
        pass

    # Method 2: Clipboard paste
    print(f"\n  Method 2: Clipboard paste")
    try:
        from AppKit import NSPasteboard, NSStringPboardType

        # Save current clipboard
        board = NSPasteboard.generalPasteboard()
        old_clipboard = board.stringForType_(NSStringPboardType)

        # Set new clipboard content
        board.clearContents()
        board.setString_forType_(test_text, NSStringPboardType)

        # Focus WhatsApp and the input field
        try:
            input_elem.AXFocused = True
            time.sleep(0.2)
        except:
            pass

        # Simulate Cmd+V
        subprocess.run([
            'osascript', '-e',
            'tell application "System Events" to keystroke "v" using command down'
        ], capture_output=True)

        time.sleep(0.5)

        current_val = input_elem.AXValue or ""
        if test_text in current_val:
            print(f"  [OK] Clipboard paste WORKS! Value is now: '{current_val[:50]}'")
        else:
            print(f"  [PARTIAL] Paste attempted, value is: '{current_val[:50]}'")

        # Restore clipboard
        if old_clipboard:
            board.clearContents()
            board.setString_forType_(old_clipboard, NSStringPboardType)
            print(f"  [OK] Original clipboard restored")

    except Exception as e:
        print(f"  [FAIL] Clipboard paste failed: {e}")

    # Clear input
    print("\n  Clearing input field...")
    try:
        # Select all and delete
        subprocess.run([
            'osascript', '-e',
            'tell application "System Events" to keystroke "a" using command down'
        ], capture_output=True)
        time.sleep(0.1)
        subprocess.run([
            'osascript', '-e',
            'tell application "System Events" to key code 51'  # Delete key
        ], capture_output=True)
        time.sleep(0.2)
        print(f"  [OK] Input cleared")
    except Exception as e:
        print(f"  [WARN] Could not clear: {e}")


# ============================================================
# 6. Test hotkey registration
# ============================================================
def test_hotkey():
    """Test if QuickMacHotKey works, fall back to CGEventTap."""
    print("\n--- Testing hotkey registration ---")

    # Try QuickMacHotKey
    try:
        from quickmachotkey import quickHotKey, mask
        print("  [OK] QuickMacHotKey is available")
        return True
    except ImportError:
        print("  [INFO] QuickMacHotKey not installed, testing CGEventTap fallback...")

    # Try CGEventTap approach
    try:
        from Quartz import (
            CGEventTapCreate, kCGSessionEventTap, kCGHeadInsertEventTap,
            kCGEventTapOptionDefault, CGEventMaskBit, kCGEventKeyDown
        )
        print("  [OK] CGEventTap (Quartz) is available as fallback")
        return True
    except ImportError as e:
        print(f"  [FAIL] Neither hotkey approach works: {e}")
        return False


# ============================================================
# 7. Test Claude API latency
# ============================================================
def test_claude_latency():
    """Measure Claude API response time with a simple style-matching prompt."""
    print("\n--- Testing Claude API latency ---")

    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Try .env file
        env_path = "/Users/anmolsahu2k/Stuff/Create/WhatsappTwin/.env"
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("ANTHROPIC_API_KEY="):
                        api_key = line.strip().split("=", 1)[1].strip('"').strip("'")

    if not api_key:
        print("  [SKIP] No ANTHROPIC_API_KEY found. Set it in environment or .env file.")
        return

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Simulate a real prompt
        system_prompt = """You are ghostwriting a WhatsApp reply for Anmol. Match his exact style:
- Casual, lowercase, minimal punctuation
- Hinglish mixing (Hindi in Roman script + English)
- Short messages, sometimes split across lines
- Uses "haan", "accha", "chal" frequently
Output ONLY the message text."""

        context = """Recent chat with Rahul:
Rahul: bro gym aaj?
Anmol: haan bhai 6 baje
Rahul: done, see you there
Rahul: btw kal ka plan kya hai
"""

        start = time.time()

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            system=system_prompt,
            messages=[{"role": "user", "content": f"<conversation>\n{context}\n</conversation>\n\nWrite a reply as Anmol."}]
        )

        elapsed = time.time() - start
        reply = message.content[0].text

        print(f"  [OK] Claude Sonnet response in {elapsed:.2f}s")
        print(f"  Reply: '{reply}'")
        print(f"  Tokens: input={message.usage.input_tokens}, output={message.usage.output_tokens}")

        if elapsed < 3.0:
            print(f"  [OK] Latency ({elapsed:.2f}s) is within 3s target")
        else:
            print(f"  [WARN] Latency ({elapsed:.2f}s) exceeds 3s target - consider streaming")

    except Exception as e:
        print(f"  [FAIL] Claude API error: {e}")


# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("WhatsApp Twin — Phase 0 Technical Spike")
    print("=" * 60)

    # 1. Check WhatsApp
    if not check_whatsapp_running():
        sys.exit(1)

    # 2. Explore AX hierarchy
    app = explore_ax_hierarchy()
    if not app:
        sys.exit(1)

    # 3. Find contact name
    find_contact_name(app)

    # 4. Find and read messages
    find_chat_messages(app)

    # 5. Test text insertion
    print("\n[!] The next test will try to insert text into WhatsApp's input box.")
    print("[!] Make sure a chat is open and the input field is visible.")
    response = input("[?] Proceed with insertion test? (y/n): ").strip().lower()
    if response == 'y':
        test_text_insertion(app)
    else:
        print("  [SKIP] Insertion test skipped")

    # 6. Test hotkey
    test_hotkey()

    # 7. Test Claude API latency
    test_claude_latency()

    print("\n" + "=" * 60)
    print("Spike complete! Review results above.")
    print("=" * 60)


if __name__ == "__main__":
    main()
