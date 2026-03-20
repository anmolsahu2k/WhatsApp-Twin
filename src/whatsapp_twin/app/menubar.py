"""Menubar app using rumps — status indicator, import, settings, permissions."""

import threading
from pathlib import Path

import rumps

from whatsapp_twin.config.logging import get_logger, setup_logging

log = get_logger(__name__)

from whatsapp_twin.app.hotkey import HotkeyListener
from whatsapp_twin.app.permissions import (
    check_accessibility,
    check_whatsapp_frontmost,
    check_whatsapp_running,
    open_accessibility_settings,
)
from whatsapp_twin.config.settings import Settings
from whatsapp_twin.generator.claude_client import ClaudeClient
from whatsapp_twin.generator.draft_manager import DraftManager
from whatsapp_twin.generator.prompt_builder import build_prompts
from whatsapp_twin.learning.edit_tracker import EditTracker
from whatsapp_twin.learning.style_updater import process_correction
from whatsapp_twin.output.typer import insert_draft
from whatsapp_twin.reader.accessibility import read_current_chat
from whatsapp_twin.learning.live_learner import LiveLearner
from whatsapp_twin.storage.database import Database


class WhatsAppTwinApp(rumps.App):
    def __init__(self, settings: Settings | None = None):
        super().__init__("WT", quit_button=None)
        self.settings = settings or Settings()
        self.db = Database(self.settings.db_path)
        self.db.initialize()
        self.db.purge_expired(
            messages_days=self.settings.messages_retention_days,
            drafts_days=self.settings.drafts_retention_days,
            corrections_days=self.settings.corrections_retention_days,
        )
        self.claude = ClaudeClient(self.settings)
        self.hotkey = HotkeyListener(callback=self._on_hotkey)
        self.edit_tracker = EditTracker(self.db)
        self.draft_manager = DraftManager()
        self.live_learner = LiveLearner(self.db, self.settings, self.claude)
        self._generating = False

        # Build menu
        self.menu = [
            rumps.MenuItem("Status: Ready", callback=None),
            None,  # separator
            rumps.MenuItem("Import Chat Export...", callback=self._on_import),
            rumps.MenuItem("Contacts", callback=None),
            None,
            rumps.MenuItem("Check Permissions", callback=self._on_check_permissions),
            rumps.MenuItem("Open Accessibility Settings", callback=self._on_open_a11y),
            None,
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]

        self._status_item = self.menu["Status: Ready"]
        self._contacts_menu = self.menu["Contacts"]
        self._refresh_contacts_menu()

    def _set_status(self, text: str):
        """Update status menu item."""
        self._status_item.title = f"Status: {text}"

    def _refresh_contacts_menu(self):
        """Rebuild the contacts submenu."""
        # clear() requires the native menu to be initialized (only after run())
        try:
            self._contacts_menu.clear()
        except AttributeError:
            pass
        contacts = self.db.list_contacts()
        if not contacts:
            self._contacts_menu.add(rumps.MenuItem("(no contacts imported)", callback=None))
            return

        for c in contacts:
            msgs = self.db.message_count(c["id"])
            excluded = " [EXCLUDED]" if c["excluded"] else ""
            contact_menu = rumps.MenuItem(f"{c['canonical_name']} ({msgs} msgs){excluded}")

            # Toggle exclude
            if c["excluded"]:
                contact_menu.add(rumps.MenuItem(
                    "Include in AI",
                    callback=lambda _, cid=c["id"]: self._toggle_exclude(cid, False),
                ))
            else:
                contact_menu.add(rumps.MenuItem(
                    "Exclude from AI",
                    callback=lambda _, cid=c["id"]: self._toggle_exclude(cid, True),
                ))

            # Delete contact
            contact_menu.add(rumps.MenuItem(
                "Delete All Data",
                callback=lambda _, cid=c["id"], name=c["canonical_name"]: self._delete_contact(cid, name),
            ))

            self._contacts_menu.add(contact_menu)

    def _toggle_exclude(self, contact_id: int, exclude: bool):
        """Toggle contact exclusion from AI generation."""
        conn = self.db.connect()
        conn.execute(
            "UPDATE contacts SET excluded = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if exclude else 0, contact_id),
        )
        conn.commit()
        self._refresh_contacts_menu()
        action = "excluded from" if exclude else "included in"
        rumps.notification("WhatsApp Twin", "", f"Contact {action} AI generation")

    def _delete_contact(self, contact_id: int, name: str):
        """Delete all data for a contact after confirmation."""
        response = rumps.alert(
            title="Delete Contact Data",
            message=f"Delete ALL data for '{name}'?\n\nThis includes messages, drafts, corrections, memory, and style profile. This cannot be undone.",
            ok="Delete",
            cancel="Cancel",
        )
        if response == 1:  # OK clicked
            self.db.delete_contact(contact_id)
            self._refresh_contacts_menu()
            rumps.notification("WhatsApp Twin", "", f"Deleted all data for '{name}'")

    def _on_import(self, _):
        """Open file dialog to import a chat export."""
        from AppKit import NSOpenPanel

        panel = NSOpenPanel.openPanel()
        panel.setTitle_("Select WhatsApp Chat Export")
        panel.setAllowedFileTypes_(["txt"])
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)

        if panel.runModal() == 1:  # NSModalResponseOK
            file_url = panel.URLs()[0]
            file_path = Path(file_url.path())
            self._do_import(file_path)

    def _do_import(self, file_path: Path):
        """Run import in a background thread."""
        self._set_status("Importing...")

        def _import():
            try:
                from whatsapp_twin.ingestion.contact_profiler import import_export, build_style_profile
                contact_ids = import_export(file_path, self.db, self.settings)
                if contact_ids:
                    for name, cid in contact_ids.items():
                        build_style_profile(cid, self.db, self.settings)
                    self._refresh_contacts_menu()
                    rumps.notification(
                        "WhatsApp Twin",
                        "Import Complete",
                        f"Imported {len(contact_ids)} contact(s) from {file_path.name}",
                    )
                else:
                    rumps.notification("WhatsApp Twin", "Import", "No new messages found")
            except Exception as e:
                rumps.notification("WhatsApp Twin", "Import Failed", str(e)[:100])
            finally:
                self._set_status("Ready")

        threading.Thread(target=_import, daemon=True).start()

    def _on_check_permissions(self, _):
        """Check and report permission status."""
        a11y = check_accessibility()
        wa_running = check_whatsapp_running()

        msgs = []
        if a11y:
            msgs.append("Accessibility: OK")
        else:
            msgs.append("Accessibility: NOT GRANTED")
        if wa_running:
            msgs.append("WhatsApp: Running")
        else:
            msgs.append("WhatsApp: Not running")

        api_key = self.settings.get_api_key()
        if api_key:
            msgs.append("API Key: Set")
        else:
            msgs.append("API Key: MISSING")

        rumps.alert(title="Permission Check", message="\n".join(msgs))

    def _on_open_a11y(self, _):
        open_accessibility_settings()

    def _on_quit(self, _):
        self.hotkey.stop()
        self.edit_tracker.stop()
        self.db.close()
        rumps.quit_application()

    def _on_hotkey(self):
        """Called when Option+Space is pressed."""
        if self._generating:
            return

        self._generating = True
        self._set_status("Generating...")
        try:
            self._generate_and_insert()
        except Exception as e:
            log.error("Hotkey handler failed: %s", e, exc_info=True)
            rumps.notification("WhatsApp Twin", "Error", str(e)[:100])
        finally:
            self._generating = False
            self._set_status("Ready")

    def _generate_and_insert(self):
        """Core flow: read chat → build prompt → generate → insert.

        Multi-draft behavior:
        - First press: generate and insert draft 1, start a draft set
        - Second press (same chat): generate variant 2, replace composer
        - Third press: generate variant 3, replace composer
        - Fourth+ press: cycle through existing variants (no new API call)
        """
        if not check_whatsapp_frontmost():
            return

        chat = read_current_chat()
        if not chat or not chat.messages:
            rumps.notification("WhatsApp Twin", "", "No chat open or no messages found")
            return

        contact_name = chat.contact_name

        # Check exclusion (both DB flag and settings set)
        contact_id = self.db.find_contact_by_alias(contact_name)
        if contact_id:
            contact = self.db.get_contact(contact_id)
            if contact and contact.get("excluded"):
                return
        else:
            # Auto-create contact from live AX read
            contact_id = self.db.get_or_create_contact(
                contact_name, is_group=chat.is_group,
            )
            self.db.add_alias(contact_id, contact_name, source="live_ax")
            # Note: don't call _refresh_contacts_menu() here — hotkey fires in a
            # daemon thread and rumps menu updates must happen on the main thread.
            # The menu will refresh on next import or app restart.
            log.info("Auto-created contact '%s' from live chat", contact_name)
        if contact_name in self.settings.excluded_contacts:
            return

        # Persist live messages and trigger background learning
        if contact_id is not None:
            self.live_learner.process_live_messages(
                chat.messages, contact_name, contact_id,
            )

        # Multi-draft: if we have a full set for this contact, just cycle
        dm = self.draft_manager
        if (dm.has_active_set
                and dm.current_set.contact_name == contact_name
                and dm.current_set.count >= dm.MAX_DRAFTS):
            draft = dm.cycle_next()
            if draft and chat.composer_element:
                from whatsapp_twin.output.typer import clear_composer
                clear_composer(chat.composer_element)
                insert_draft(draft, chat.composer_element)
                self._set_status(f"Draft {dm.status_text()}")
            return

        # Detect group chat from DB flag or live AX messages
        is_group = chat.is_group
        if not is_group and contact_id:
            contact_row = self.db.get_contact(contact_id)
            if contact_row and contact_row.get("is_group"):
                is_group = True

        # Generate a new draft (or variant)
        system, user = build_prompts(
            live_messages=chat.messages,
            contact_name=contact_name,
            user_name=self.settings.user_name,
            db=self.db,
            contact_id=contact_id,
            is_group=is_group,
        )

        # Use streaming for lower perceived latency
        chunks = []
        for chunk in self.claude.generate_stream(system, user):
            chunks.append(chunk)
        draft = "".join(chunks)
        if not draft:
            rumps.notification("WhatsApp Twin", "", "Empty response from Claude")
            return

        if "[MSG]" in draft:
            parts = [p.strip() for p in draft.split("[MSG]") if p.strip()]
            draft = "\n".join(parts)

        if not check_whatsapp_frontmost():
            rumps.notification("WhatsApp Twin", "", "WhatsApp lost focus during generation")
            return

        if chat.composer_element:
            from whatsapp_twin.output.typer import clear_composer

            if dm.should_generate_variant(contact_name):
                # Replace existing draft with new variant
                clear_composer(chat.composer_element)
                dm.add_variant(draft)
            else:
                # New set — clear composer if there was a previous draft
                if dm.has_active_set:
                    clear_composer(chat.composer_element)
                dm.start_new_set(contact_name, draft)

            success = insert_draft(draft, chat.composer_element)
            if not success:
                rumps.notification("WhatsApp Twin", "", "Could not insert draft")
                return

            self._set_status(f"Draft {dm.status_text()}")

            # Start edit tracking session (requires known contact for DB storage)
            if contact_id is not None:
                session = self.edit_tracker.start_session(
                    contact_name=contact_name,
                    contact_id=contact_id,
                    draft_text=draft,
                    model=self.settings.anthropic_model,
                )
                self._watch_for_correction(session)
            else:
                log.debug("Skipping edit tracking — contact '%s' not in DB", contact_name)
        else:
            rumps.notification("WhatsApp Twin", "", "Composer not found")

    def _watch_for_correction(self, session):
        """Wait for the edit tracker session to complete, then process corrections."""
        def _watch():
            # Wait for session to expire or complete (monitor loop handles this)
            while not session.expired:
                import time
                time.sleep(1)

            if session.expire_reason == "sent" and session.sent_text:
                corrections = process_correction(session, self.db)
                if corrections:
                    cats = set(c["category"] for c in corrections)
                    log.info("Edit learning: %d correction(s) detected: %s",
                             len(corrections), ", ".join(cats))

        threading.Thread(target=_watch, daemon=True).start()

    @rumps.timer(1)
    def _startup_check(self, timer):
        """Run once after the app event loop starts to do preflight checks."""
        # Only run once
        timer.stop()

        if not check_accessibility():
            rumps.alert(
                title="Accessibility Required",
                message="WhatsApp Twin needs Accessibility permission.\n\n"
                        "Go to: System Settings → Privacy & Security → Accessibility\n"
                        "Add this app/terminal.",
                ok="Open Settings",
            )
            open_accessibility_settings()
            rumps.quit_application()
            return

        if not self.settings.get_api_key():
            rumps.alert(
                title="API Key Missing",
                message="Set ANTHROPIC_API_KEY in your environment or .env file.",
            )
            rumps.quit_application()
            return

        if not check_whatsapp_running():
            rumps.notification("WhatsApp Twin", "", "WhatsApp is not running. Start it and open a chat.")

        # Start hotkey listener
        self.hotkey.start()
        self._set_status("Ready")
        log.info("Menubar app running. Option+Space to generate drafts.")

    def run_app(self):
        """Start the menubar app."""
        # rumps.App.run() starts the NSApplication event loop
        # Preflight checks run via the _startup_check timer after the loop starts
        super().run()


def main():
    setup_logging()
    app = WhatsAppTwinApp()
    app.run_app()
