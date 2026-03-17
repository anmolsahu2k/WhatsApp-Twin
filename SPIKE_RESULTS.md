# Phase 0 Spike Results — 2026-03-16

## Summary: ALL CLEAR — proceed with implementation

## Test Results

| # | Test | Result | Details |
|---|------|--------|---------|
| 1 | WhatsApp running detection | PASS | `NSWorkspace` finds it by bundle ID `net.whatsapp.WhatsApp` |
| 2 | AX hierarchy traversal | PASS | **Must use `app.AXMainWindow`** — `app.windows()` returns `[]` for Catalyst apps |
| 3 | Read chat messages | PASS | `AXStaticText` with `id=WAMessageBubbleTableViewCell`, text in `desc` attribute |
| 4 | Read contact name | PASS | `AXHeading` with `id=NavigationBar_HeaderViewButton`, name in `desc` attribute |
| 5 | Find composer | PASS | `AXTextArea` with `id=ChatBar_ComposerTextView` |
| 6 | AX setValue() | FAIL | **No settable attributes at all** — clipboard paste is only option |
| 7 | Clipboard paste insertion | PASS | NSPasteboard → focus composer via `Press()` → Cmd+V → restore clipboard |
| 8 | Hotkey (CGEventTap) | PASS | Quartz CGEventTap available as fallback (QuickMacHotKey not pip-installable) |
| 9 | Claude API latency | PASS | **2.01s** with `claude-sonnet-4-6` — well within 3s target |

## WhatsApp AX Hierarchy Map

```
AXApplication (net.whatsapp.WhatsApp)
└── AXMainWindow (id=SceneWindow)          ← use app.AXMainWindow, NOT app.windows()
    └── AXGroup (sub=iOSContentGroup)
        └── AXGroup
            ├── Group[0] — Navigation sidebar
            │   └── Sidebar_view: Chats, Calls, Updates, Archived, Starred buttons
            │
            ├── AXSplitter
            │
            ├── Group[2] — Chat list pane
            │   ├── Toolbar: "Chats" heading, "New Chat" button, search field
            │   └── ChatListView_TableView: AXButton per chat (value has preview text, desc has contact name)
            │
            ├── AXSplitter
            │
            └── Group[4] — Active chat pane
                ├── Toolbar
                │   └── AXHeading (id=NavigationBar_HeaderViewButton, desc=contact name)
                └── Chat body
                    ├── ChatMessagesTableView (id=ChatMessagesTableView)
                    │   └── AXStaticText[] (id=WAMessageBubbleTableViewCell)
                    │       desc format: "‎Your message, <text>, <time>, ‎Sent to <contact>"
                    │                  or: "‎message, <text>, <time>, ‎Received from <contact>"
                    ├── AXButton (id=ChatBar_AttachMediaButton)
                    ├── AXTextArea (id=ChatBar_ComposerTextView)  ← composer (NOT settable)
                    ├── AXButton (id=ChatBar_EmojiButton)
                    └── AXButton (id=ChatBar_VoiceMessageButton)
```

## Architecture Decisions

1. **Clipboard paste is the ONLY insertion method** — AX setValue() unavailable on Catalyst composer. No settable attributes at all.
2. **Use `app.AXMainWindow`** instead of `app.windows()` — atomacos bug with Catalyst apps.
3. **Message text is in `desc` attribute** — `value` is always empty string for message bubbles.
4. **Message direction detection**: "Your message" prefix = sent, "message" prefix = received.
5. **Model: `claude-sonnet-4-6`** — 2s latency leaves 1s buffer for AX read + clipboard paste.
6. **CGEventTap for hotkeys** — QuickMacHotKey is a Swift package, not pip-installable. Use Quartz CGEventTap.

## Navigation Path (for code)

```python
import atomacos
app = atomacos.getAppRefByBundleId("net.whatsapp.WhatsApp")
window = app.AXMainWindow
content = window.AXChildren[0]                    # iOSContentGroup
main = content.AXChildren[0]                      # main container
chat_list_pane = main.AXChildren[2]               # chat list (index may vary)
active_chat_pane = main.AXChildren[4]             # active chat (index may vary)
toolbar = active_chat_pane.AXChildren[0]          # toolbar with contact name
chat_body = active_chat_pane.AXChildren[1]        # messages + composer

# Better: find by ID
# Contact: find AXHeading with id=NavigationBar_HeaderViewButton
# Messages: find AXStaticText with id=WAMessageBubbleTableViewCell inside ChatMessagesTableView
# Composer: find AXTextArea with id=ChatBar_ComposerTextView
```
