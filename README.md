# WhatsApp Twin

A macOS desktop assistant that reads your WhatsApp Desktop conversations and drafts replies in your exact texting style. The AI only drafts — you always send manually.

Press **Option+Space** while WhatsApp is open, and a style-matched reply appears in the composer within ~2 seconds. Review it, edit if needed, and hit Enter yourself.

## How It Works

1. **Import** your WhatsApp chat exports to build a style profile (message length, emoji usage, Hinglish mixing, abbreviations, tone)
2. **Run** the menubar app — it listens for Option+Space
3. **Generate** — reads the current chat via macOS Accessibility APIs, sends context + style profile to Claude, inserts the draft into the composer
4. **Learn** — tracks what you actually send vs. what was drafted, and adjusts your style profile over time

## Install

```bash
# Clone and set up
git clone <repo-url> && cd WhatsappTwin
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Set your API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

### macOS Permissions Required

- **Accessibility** — to read WhatsApp's UI and insert drafts
- **Automation** — for Cmd+V keystroke simulation (clipboard paste)

The app guides you through granting these on first launch.

## Usage

### 1. Import Chat History

Export a chat from WhatsApp (Settings > Chats > Export Chat > Without Media), then:

```bash
whatsapp-twin import data/exports/chat.txt --analyze
```

This parses messages, builds a per-contact style profile, and stores everything locally. Works for both individual and group chats — groups are automatically detected and profiled as a whole.

### 2. Run the App

```bash
whatsapp-twin menubar    # recommended — runs as menubar icon
whatsapp-twin run        # alternative — terminal mode
```

### 3. Generate Drafts

- Open a WhatsApp chat
- Press **Option+Space** — draft appears in the composer
- Press again for a **variant** (up to 3 alternatives)
- Press again to **cycle** through variants (no API call)
- Edit if needed, then press Enter to send

### Other Commands

```bash
whatsapp-twin contacts                    # list imported contacts
whatsapp-twin profile [CONTACT]           # view style profile
whatsapp-twin memory [CONTACT]            # view stored memories
whatsapp-twin memory [CONTACT] --extract  # extract memories via LLM
```

## Features

- **Style matching** — learns your emoji density, capitalization, Hinglish mixing, abbreviations, message splitting patterns, and tone per contact
- **Group chat support** — groups are detected automatically and profiled as a whole (people text differently in groups). Group-aware prompts consider conversation flow and dynamics.
- **Multi-draft** — generates up to 3 variants per reply, cycle with repeated hotkey presses
- **Streaming** — uses Claude's streaming API for lower perceived latency
- **Memory system** — extracts and stores facts, commitments, events, preferences per contact for context-aware replies
- **Edit learning** — compares what the AI drafted vs. what you actually sent, categorizes corrections (length, emoji, language, punctuation, tone), and updates your style profile via exponential moving average
- **OCR fallback** — if Accessibility API can't read messages, falls back to Vision framework OCR
- **Per-contact controls** — exclude sensitive contacts from AI generation, delete all data per contact
- **Data retention** — messages auto-purge after 90 days, drafts after 30 days, corrections after 90 days

## Safety

- **Never sends messages** — drafts are inserted into the composer, never sent. No Enter key simulation, ever.
- **App focus guard** — verifies WhatsApp is the frontmost app before and after generation
- **Clipboard restore** — original clipboard is always restored after paste insertion
- **Data stays local** — SQLite database on disk. Only the current conversation context is sent to the Anthropic API for generation.

## Tech Stack

- Python 3.14, PyObjC, atomacos (Accessibility traversal), rumps (menubar)
- Quartz CGEventTap (global hotkeys), Vision framework (OCR fallback)
- Anthropic Claude API (claude-sonnet-4-6)
- SQLite with TTL-based retention

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -v    # 81 tests
```

## Project Structure

```
src/whatsapp_twin/
  app/           hotkey, permissions, menubar
  reader/        AX accessibility reader, OCR fallback
  ingestion/     export parser, style analyzer, contact profiler
  intelligence/  style profiles, context builder, memory
  generator/     Claude client (streaming), prompt builder, draft manager
  learning/      edit tracker, style updater
  output/        clipboard paste insertion
  storage/       SQLite database, data models
  config/        settings, logging
```

## Limitations

- **macOS only** — relies on Accessibility APIs, PyObjC, Quartz CGEventTap
- **WhatsApp Desktop only** — AX element IDs are hardcoded; a WhatsApp update can break the reader
- **Visible messages only** — AX reads what's on screen (~20-30 messages); no scroll-back
- **No media awareness** — images, voice notes, stickers, and reactions are invisible to the AI
- **No encryption at rest** — plain SQLite (pysqlcipher3 incompatible with Python 3.14)
- **Clipboard briefly overwritten** — AX setValue() unavailable on the Catalyst composer, so clipboard paste is the only insertion method

## Future Scope

- Sound/haptic feedback when draft is ready
- Undo support — restore composer contents on a separate hotkey
- **Per-contact model selection (Haiku for casual, Opus for important)**
- SQLite encryption once pysqlcipher3 supports Python 3.14
- API key storage in macOS Keychain
- **Scroll-back context — programmatically scroll up for more messages**
- **Media-aware prompts — detect `<Media omitted>` and inform Claude**
- **Reaction awareness from AX tree**
- Voice note transcription via Whisper
- **Conversation summarization ("catch me up on this chat")**
- Cross-platform support (Windows/Linux via screen capture + OCR)
- Multi-messenger support (iMessage, Telegram, Signal)
- **Local model option for fully offline operation**
- **Fine-tuned small model on user's messages for better style matching**
- **Intent controls** — regenerate as shorter, warmer, more direct, more playful, buy-time, or decline politely
- **Draft confidence + provenance** — show which memories/context influenced the draft and flag stale or ambiguous context
- **Situational style profiles** — adapt style not just per contact, but per context (work, casual, serious, late-night, group vs 1:1)
- **Follow-up assistant** — turn commitments, events, and promised actions into reminders and suggested replies
- **Personal CRM / relationship memory** — searchable timeline of shared events, promises, preferences, and recurring topics
- **Inbox triage** — rank chats by urgency, summarize unread deltas, and suggest who to reply to first
- **Privacy controls** — redaction before API calls, sensitive-topic detection, contact-level privacy modes, and audit logs
- **Evaluation / replay mode** — test on historical conversations and track acceptance rate, edit distance, and per-model quality
- **Cross-app identity graph** — maintain a shared relationship/style model across multiple messaging platforms
- **Assistive communication mode** — help users with anxiety, ADHD, or language friction reply faster in their own voice

## License

Private — not for redistribution.
