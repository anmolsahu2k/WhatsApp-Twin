# WhatsApp Twin

macOS desktop assistant that reads WhatsApp Desktop conversations and drafts replies in Anmol Sahu's exact texting style. AI drafts only — user always sends manually.

## Quick Start

```bash
source .venv/bin/activate
whatsapp-twin import data/exports/chat.txt --analyze   # import + style profile
whatsapp-twin menubar                                   # menubar app (recommended)
whatsapp-twin run                                       # terminal mode (Option+Space)
```

## Project Structure

- `src/whatsapp_twin/` — main package
  - `app/` — hotkey (CGEventTap), permissions, menubar (rumps)
  - `reader/` — WhatsApp AX accessibility reader + Vision OCR fallback
  - `ingestion/` — export parser, style analyzer, contact profiler
  - `intelligence/` — style profiles, context builder, memory
  - `generator/` — Claude client (streaming), prompt builder, multi-draft manager
  - `learning/` — edit tracker (draft vs sent), style updater (EMA corrections)
  - `output/` — clipboard paste insertion
  - `storage/` — SQLite database, data models
  - `config/` — settings
- `tests/` — pytest tests (81 passing)
- `data/exports/` — WhatsApp .txt exports (gitignored)

## Tech Stack

- Python 3.14, PyObjC, atomacos (AX traversal), rumps (menubar), anthropic SDK
- SQLite with `check_same_thread=False` (plain sqlite3; pysqlcipher3 incompatible with 3.14)
- Quartz CGEventTap for global hotkeys (QuickMacHotKey is Swift-only, not pip-installable)
- Python `logging` module — `whatsapp_twin.*` namespace, output to stderr

## Key Technical Details

- **WhatsApp is a Catalyst app** — `app.windows()` returns `[]`. Must use `app.AXMainWindow`.
- **AX setValue() is unavailable** on the composer. Clipboard paste (NSPasteboard + Cmd+V) is the only insertion method.
- **Message text is in `AXDescription`** attribute, not `AXValue`.
- **AX datetime format varies** — can be `"1:47 AM"` or `"March14,at1:47 AM"`. Regex handles both.
- **AX element IDs**: `WAMessageBubbleTableViewCell` (messages), `NavigationBar_HeaderViewButton` (contact name), `ChatBar_ComposerTextView` (composer).
- **WhatsApp exports contain Unicode** — `\u200e` (LTR mark), `\u202f` (narrow no-break space before AM/PM). Parser strips these.
- **Export format has no dash separator** — `[M/DD/YY, H:MM:SS PM] Sender: text` (no `-` between timestamp and sender).
- **User name matching** — exports use full name ("Anmol Sahu"), must match exactly for style analysis. `identify_user_name()` does fuzzy matching.
- **Thread safety** — hotkey fires callback in daemon thread; DB uses `check_same_thread=False`.
- **Menubar app** — rumps requires NSApplication event loop; preflight checks run via `@rumps.timer(1)` after `run()`.
- **Model**: `claude-sonnet-4-6` (~2s latency).
- **Streaming** — `generate_stream()` yields text chunks for lower perceived latency.
- **OCR fallback** — if AX finds no messages, Vision framework OCR captures the WhatsApp window and extracts text.
- **Multi-draft** — press hotkey again on same chat to get variant (up to 3); subsequent presses cycle through them.
- **Edit learning** — draft sessions track what user actually sends. Corrections categorized (length, emoji, language, punctuation, tone) and applied via EMA (alpha=0.15) to update style profiles.
- **Group chats** — detected from AX (multiple received senders) or DB `is_group` flag. Groups get their own contact entry and style profile. Group-specific prompt templates include group dynamics and conversation flow rules.
- **Logging** — `config/logging.py` provides `setup_logging()` (called at all 3 entry points: cli, main, menubar) and `get_logger(__name__)` per module. Format: `[I] whatsapp_twin.module: message`. Operational logs go to stderr; user-facing CLI output stays on stdout.
- **Composer clear** — Cmd+A unreliable in WhatsApp's Catalyst composer. Uses Cmd+Down → Cmd+Shift+Up → Delete instead.
- **Edit tracking guard** — skipped when `contact_id` is None (contact not in DB), prevents NOT NULL constraint errors on drafts table.

## Commands

```bash
whatsapp-twin import <file> [--analyze] [--user-name NAME]
whatsapp-twin contacts
whatsapp-twin profile [CONTACT]
whatsapp-twin memory [CONTACT] [--extract]                     # view/extract memories
whatsapp-twin run                                          # terminal mode
whatsapp-twin menubar                                      # menubar app (recommended)
```

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Safety Invariants

- **Never simulate Enter/Return** — drafts are inserted into the composer, never sent.
- **App focus guard** — verify WhatsApp is frontmost before and after generation.
- **Clipboard restore** — original clipboard is always restored after paste.
- **Per-contact exclude list** — excluded contacts get no AI generation (DB flag + settings set).
- **TTL purge** — messages 90d, drafts 30d, corrections 90d. Runs on startup.

## Implementation Plan

Full plan at `.claude/plans/graceful-twirling-ritchie.md`. All phases (0–7) complete.
