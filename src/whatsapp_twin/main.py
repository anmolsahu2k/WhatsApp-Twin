"""WhatsApp Twin — main entry point.

Wires together: hotkey → AX reader → prompt builder → Claude → clipboard insert.
"""

import sys
import time

from whatsapp_twin.app.hotkey import HotkeyListener
from whatsapp_twin.app.permissions import (
    check_accessibility,
    check_whatsapp_frontmost,
    check_whatsapp_running,
)
from whatsapp_twin.config.logging import get_logger, setup_logging
from whatsapp_twin.config.settings import Settings
from whatsapp_twin.generator.claude_client import ClaudeClient
from whatsapp_twin.generator.prompt_builder import build_prompts
from whatsapp_twin.output.typer import insert_draft
from whatsapp_twin.reader.accessibility import read_current_chat
from whatsapp_twin.storage.database import Database

log = get_logger(__name__)


class WhatsAppTwin:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.db = Database(self.settings.db_path)
        self.db.initialize()
        self.db.purge_expired(
            messages_days=self.settings.messages_retention_days,
            drafts_days=self.settings.drafts_retention_days,
            corrections_days=self.settings.corrections_retention_days,
        )
        self.claude = ClaudeClient(self.settings)
        self.hotkey = HotkeyListener(callback=self.on_hotkey)
        self._generating = False

    def on_hotkey(self):
        """Called when Option+Space is pressed."""
        if self._generating:
            return  # prevent double-trigger

        self._generating = True
        try:
            self._generate_and_insert()
        except Exception as e:
            log.error("Generation failed: %s", e, exc_info=True)
        finally:
            self._generating = False

    def _generate_and_insert(self):
        """Core flow: read chat → build prompt → generate → insert."""
        start = time.time()

        # Safety: verify WhatsApp is frontmost
        if not check_whatsapp_frontmost():
            log.debug("WhatsApp is not the frontmost app")
            return

        # Read current chat
        chat = read_current_chat()
        if not chat:
            log.debug("Could not read chat (no chat open?)")
            return

        if not chat.messages:
            log.debug("No messages found in current chat")
            return

        contact_name = chat.contact_name
        log.info("Generating reply for chat with: %s", contact_name)

        # Check if contact is excluded
        if contact_name in self.settings.excluded_contacts:
            log.info("Contact '%s' is excluded, skipping", contact_name)
            return

        # Resolve contact in DB (for style profile + memory)
        contact_id = self.db.find_contact_by_alias(contact_name)

        # Build prompts
        system, user = build_prompts(
            live_messages=chat.messages,
            contact_name=contact_name,
            user_name=self.settings.user_name,
            db=self.db,
            contact_id=contact_id,
            is_group=chat.is_group,
        )

        # Generate draft
        draft = self.claude.generate(system, user)
        if not draft:
            log.warning("Empty response from Claude")
            return

        # Handle multi-message drafts (split by [MSG] delimiter)
        if "[MSG]" in draft:
            parts = [p.strip() for p in draft.split("[MSG]") if p.strip()]
            draft = "\n".join(parts)

        elapsed = time.time() - start
        log.info("Draft generated in %.1fs: %s...", elapsed, draft[:80])

        # Safety: re-verify WhatsApp is still frontmost before inserting
        if not check_whatsapp_frontmost():
            log.warning("WhatsApp lost focus during generation")
            return

        # Insert into composer
        if chat.composer_element:
            success = insert_draft(draft, chat.composer_element)
            if success:
                log.info("Draft inserted into composer (NOT sent)")
            else:
                log.error("Could not insert draft")
        else:
            log.error("Composer element not found")

    def run(self):
        """Start the application."""
        print("=" * 50)
        print("WhatsApp Twin")
        print("=" * 50)

        # Preflight checks
        if not check_accessibility():
            log.error("Accessibility permission not granted")
            print("  Go to: System Settings → Privacy & Security → Accessibility")
            print("  Add this terminal/IDE app.")
            return

        if not check_whatsapp_running():
            log.warning("WhatsApp is not running. Start it and open a chat.")

        log.info("User: %s | Model: %s | Hotkey: Option+Space",
                 self.settings.user_name, self.settings.anthropic_model)
        print("\nPress Option+Space in WhatsApp to generate a draft reply.")
        print("Press Ctrl+C to quit.\n")

        # Start hotkey listener
        self.hotkey.start()

        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Shutting down...")
            self.hotkey.stop()
            self.db.close()


def main():
    setup_logging()
    try:
        app = WhatsAppTwin()
        app.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.critical("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
