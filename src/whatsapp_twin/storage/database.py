"""SQLite database with optional encryption, schema management, and TTL purge."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


# Schema version — bump when adding migrations
SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    phone TEXT,
    relationship_type TEXT,
    language_preference TEXT,
    typical_topics TEXT,
    style_json TEXT,
    their_style_json TEXT,
    is_group INTEGER NOT NULL DEFAULT 0,
    excluded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(canonical_name)
);

CREATE TABLE IF NOT EXISTS contact_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    alias_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(alias_name)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK(direction IN ('sent', 'received', 'system')),
    sender_name TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'export',
    export_file TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    session_uuid TEXT NOT NULL,
    draft_text TEXT NOT NULL,
    sent_text TEXT,
    edit_distance REAL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS style_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE SET NULL,
    category TEXT NOT NULL,
    original TEXT NOT NULL,
    corrected TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_contact_ts ON messages(contact_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_drafts_created ON drafts(created_at);
CREATE INDEX IF NOT EXISTS idx_corrections_created ON style_corrections(created_at);
CREATE INDEX IF NOT EXISTS idx_aliases_name ON contact_aliases(alias_name);
CREATE INDEX IF NOT EXISTS idx_messages_source ON messages(source, export_file);
"""


class Database:
    def __init__(self, db_path: Path, encryption_key: str | None = None):
        self.db_path = db_path
        self.encryption_key = encryption_key
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        db_path_str = str(self.db_path)

        if self.encryption_key:
            try:
                from pysqlcipher3 import dbapi2 as sqlcipher
                self._conn = sqlcipher.connect(db_path_str, check_same_thread=False)
                self._conn.execute(f"PRAGMA key = '{self.encryption_key}'")
            except ImportError:
                # Fall back to unencrypted sqlite if pysqlcipher3 not available
                self._conn = sqlite3.connect(db_path_str, check_same_thread=False)
        else:
            self._conn = sqlite3.connect(db_path_str, check_same_thread=False)

        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        return self._conn

    def initialize(self):
        """Create schema and run migrations."""
        conn = self.connect()
        conn.executescript(SCHEMA_SQL)
        # Migration: add is_group column if missing (for existing databases)
        try:
            conn.execute("SELECT is_group FROM contacts LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE contacts ADD COLUMN is_group INTEGER NOT NULL DEFAULT 0")
        conn.commit()

    def purge_expired(self, messages_days: int = 90, drafts_days: int = 30,
                      corrections_days: int = 90):
        """Delete rows older than their retention period."""
        conn = self.connect()
        now = datetime.now(UTC)

        cutoffs = {
            "messages": (now - timedelta(days=messages_days)).isoformat(),
            "drafts": (now - timedelta(days=drafts_days)).isoformat(),
            "style_corrections": (now - timedelta(days=corrections_days)).isoformat(),
        }

        for table, cutoff in cutoffs.items():
            conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))

        conn.commit()

    def delete_contact(self, contact_id: int):
        """Delete a contact and all associated data (CASCADE handles children)."""
        conn = self.connect()
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()

    # -- Contact operations --

    def get_or_create_contact(self, canonical_name: str, phone: str | None = None,
                              is_group: bool = False) -> int:
        """Get existing contact by name or create a new one. Returns contact ID."""
        conn = self.connect()
        row = conn.execute(
            "SELECT id FROM contacts WHERE canonical_name = ?", (canonical_name,)
        ).fetchone()
        if row:
            return row["id"]

        cursor = conn.execute(
            "INSERT INTO contacts (canonical_name, phone, is_group) VALUES (?, ?, ?)",
            (canonical_name, phone, 1 if is_group else 0),
        )
        conn.commit()
        return cursor.lastrowid

    def find_contact_by_alias(self, alias_name: str) -> int | None:
        """Look up a contact by alias name. Returns contact ID or None."""
        conn = self.connect()
        row = conn.execute(
            "SELECT contact_id FROM contact_aliases WHERE alias_name = ?",
            (alias_name,),
        ).fetchone()
        return row["contact_id"] if row else None

    def add_alias(self, contact_id: int, alias_name: str, source: str = "export"):
        """Add an alias for a contact. Ignores if alias already exists."""
        conn = self.connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO contact_aliases (contact_id, alias_name, source) "
                "VALUES (?, ?, ?)",
                (contact_id, alias_name, source),
            )
            conn.commit()
        except Exception:
            pass  # alias already exists for another contact — caller handles

    def get_contact(self, contact_id: int) -> dict | None:
        conn = self.connect()
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        return dict(row) if row else None

    def list_contacts(self) -> list[dict]:
        conn = self.connect()
        rows = conn.execute("SELECT * FROM contacts ORDER BY canonical_name").fetchall()
        return [dict(r) for r in rows]

    # -- Message operations --

    def insert_messages(self, messages: list[tuple]):
        """Bulk insert messages. Each tuple: (contact_id, direction, sender_name, text, timestamp, source, export_file)."""
        conn = self.connect()
        conn.executemany(
            "INSERT INTO messages (contact_id, direction, sender_name, text, timestamp, source, export_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            messages,
        )
        conn.commit()

    def get_messages(self, contact_id: int, limit: int = 50,
                     before: datetime | None = None,
                     exclude_expired: bool = True,
                     max_age_days: int | None = None) -> list[dict]:
        """Get recent messages for a contact, ordered by timestamp descending."""
        conn = self.connect()
        conditions = ["contact_id = ?"]
        params: list = [contact_id]

        if before:
            conditions.append("timestamp < ?")
            params.append(before.isoformat())

        if exclude_expired and max_age_days:
            cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
            conditions.append("created_at >= ?")
            params.append(cutoff)

        where = " AND ".join(conditions)
        params.append(limit)

        rows = conn.execute(
            f"SELECT * FROM messages WHERE {where} ORDER BY timestamp DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in reversed(rows)]  # chronological order

    def message_count(self, contact_id: int) -> int:
        conn = self.connect()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE contact_id = ?", (contact_id,)
        ).fetchone()
        return row["cnt"]

    def has_export(self, export_file: str) -> bool:
        """Check if an export file has already been imported."""
        conn = self.connect()
        row = conn.execute(
            "SELECT 1 FROM messages WHERE export_file = ? LIMIT 1", (export_file,)
        ).fetchone()
        return row is not None

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
