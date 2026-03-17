"""CLI commands for WhatsApp Twin — import exports, view profiles, run the app."""

import argparse
import sys
from pathlib import Path

from whatsapp_twin.config.logging import get_logger, setup_logging
from whatsapp_twin.config.settings import Settings

log = get_logger(__name__)


def cmd_import(args):
    """Import a WhatsApp .txt export."""
    from whatsapp_twin.ingestion.contact_profiler import import_export, build_style_profile
    from whatsapp_twin.storage.database import Database

    settings = Settings()
    if args.user_name:
        settings.user_name = args.user_name

    db = Database(settings.db_path)
    db.initialize()

    export_path = Path(args.file)
    if not export_path.exists():
        log.error("File not found: %s", export_path)
        sys.exit(1)

    contact_ids = import_export(export_path, db, settings)

    if args.analyze and contact_ids:
        log.info("Building style profiles...")
        for name, cid in contact_ids.items():
            profile = build_style_profile(cid, db, settings)
            if profile:
                print(f"\n--- Style Profile: {name} ---")
                for key, val in profile.items():
                    if val and val != 0 and val != 0.0 and val != []:
                        print(f"  {key}: {val}")

    db.close()


def cmd_profile(args):
    """Show style profile for a contact."""
    from whatsapp_twin.intelligence.style_profile import StyleProfile
    from whatsapp_twin.storage.database import Database

    settings = Settings()
    db = Database(settings.db_path)
    db.initialize()

    contacts = db.list_contacts()
    if args.contact:
        contacts = [c for c in contacts if args.contact.lower() in c["canonical_name"].lower()]

    if not contacts:
        print("No matching contacts found.")
        db.close()
        return

    for contact in contacts:
        print(f"\n=== {contact['canonical_name']} ===")
        print(f"  Messages: {db.message_count(contact['id'])}")
        if contact.get("style_json"):
            profile = StyleProfile.from_json(contact["style_json"])
            print(profile.to_prompt_description())
        else:
            print("  (no style profile yet — run import with --analyze)")

    db.close()


def cmd_contacts(args):
    """List all contacts."""
    from whatsapp_twin.storage.database import Database

    settings = Settings()
    db = Database(settings.db_path)
    db.initialize()

    contacts = db.list_contacts()
    if not contacts:
        print("No contacts imported yet.")
    else:
        for c in contacts:
            msgs = db.message_count(c["id"])
            excluded = " [EXCLUDED]" if c["excluded"] else ""
            group = " [GROUP]" if c.get("is_group") else ""
            print(f"  {c['canonical_name']} ({msgs} messages){group}{excluded}")

    db.close()


def cmd_run(args):
    """Run the WhatsApp Twin assistant (terminal mode)."""
    from whatsapp_twin.main import WhatsAppTwin

    settings = Settings()
    app = WhatsAppTwin(settings)
    app.run()


def cmd_menubar(args):
    """Run the WhatsApp Twin menubar app."""
    from whatsapp_twin.app.menubar import WhatsAppTwinApp

    app = WhatsAppTwinApp()
    app.run_app()


def cmd_memory(args):
    """View or extract memories for a contact."""
    from whatsapp_twin.intelligence.memory import (
        extract_memories_from_messages,
        get_memories,
        save_extracted_memories,
    )
    from whatsapp_twin.storage.database import Database

    settings = Settings()
    db = Database(settings.db_path)
    db.initialize()

    # Find contact
    contacts = db.list_contacts()
    if args.contact:
        contacts = [c for c in contacts if args.contact.lower() in c["canonical_name"].lower()]

    if not contacts:
        print("No matching contacts found.")
        db.close()
        return

    for contact in contacts:
        cid = contact["id"]
        name = contact["canonical_name"]

        if args.extract:
            # LLM-powered extraction from chat history
            from whatsapp_twin.generator.claude_client import ClaudeClient

            log.info("Extracting memories for %s...", name)
            print(f"NOTE: Conversation chunks will be sent to the Anthropic API.")

            msgs = db.get_messages(cid, limit=5000)
            if not msgs:
                print(f"  No messages found for {name}")
                continue

            claude = ClaudeClient(settings)
            memories = extract_memories_from_messages(
                [dict(m) for m in msgs],
                contact_name=name,
                user_name=settings.user_name,
                claude_client=claude,
            )

            added = save_extracted_memories(db, cid, memories)
            print(f"  Extracted {len(memories)} memories, added {added} new entries")

        # Show memories
        memories = get_memories(db, cid)
        if memories:
            print(f"\n=== Memories: {name} ({len(memories)} entries) ===")
            for m in memories:
                print(f"  [{m['category']}] {m['content']}")
        else:
            print(f"\n=== {name}: no memories yet (use --extract) ===")

    db.close()


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="WhatsApp Twin — AI reply assistant")
    sub = parser.add_subparsers(dest="command")

    # import
    p_import = sub.add_parser("import", help="Import a WhatsApp .txt export")
    p_import.add_argument("file", help="Path to the .txt export file")
    p_import.add_argument("--user-name", help="Your display name in the export")
    p_import.add_argument("--analyze", action="store_true", help="Build style profile after import")
    p_import.set_defaults(func=cmd_import)

    # profile
    p_profile = sub.add_parser("profile", help="Show style profile for a contact")
    p_profile.add_argument("contact", nargs="?", help="Contact name (partial match)")
    p_profile.set_defaults(func=cmd_profile)

    # contacts
    p_contacts = sub.add_parser("contacts", help="List all contacts")
    p_contacts.set_defaults(func=cmd_contacts)

    # memory
    p_memory = sub.add_parser("memory", help="View or extract memories for a contact")
    p_memory.add_argument("contact", nargs="?", help="Contact name (partial match)")
    p_memory.add_argument("--extract", action="store_true",
                          help="Extract memories from chat history via LLM (sends data to API)")
    p_memory.set_defaults(func=cmd_memory)

    # run (terminal mode)
    p_run = sub.add_parser("run", help="Start the assistant in terminal mode (Option+Space hotkey)")
    p_run.set_defaults(func=cmd_run)

    # menubar
    p_menubar = sub.add_parser("menubar", help="Start as menubar app (recommended)")
    p_menubar.set_defaults(func=cmd_menubar)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
