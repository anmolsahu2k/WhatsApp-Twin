"""Application settings and configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _project_data_dir() -> Path:
    """Default data directory alongside the source tree."""
    return Path(__file__).resolve().parents[3] / "data"


@dataclass
class Settings:
    # Paths
    data_dir: Path = field(default_factory=_project_data_dir)
    exports_dir: Path = field(default=None)
    db_path: Path = field(default=None)

    # User identity
    user_name: str = "Anmol Sahu"

    # API
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_max_tokens: int = 300

    # Retention (days)
    messages_retention_days: int = 90
    drafts_retention_days: int = 30
    corrections_retention_days: int = 90

    # Hotkey
    hotkey_modifiers: list[str] = field(default_factory=lambda: ["option"])
    hotkey_key: str = "space"

    # Excluded contacts (canonical IDs)
    excluded_contacts: set[str] = field(default_factory=set)

    def __post_init__(self):
        if self.exports_dir is None:
            self.exports_dir = self.data_dir / "exports"
        if self.db_path is None:
            self.db_path = self.data_dir / "whatsapp_twin.db"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def get_api_key(self) -> str | None:
        """Get Anthropic API key from environment or .env file."""
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            return key
        env_file = Path(__file__).resolve().parents[3] / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        return None

    def get_db_key(self) -> str:
        """Get database encryption key. For now, uses a derived key from API key.
        TODO: Phase 4 — store in macOS Keychain."""
        import hashlib
        api_key = self.get_api_key() or "whatsapp-twin-dev"
        return hashlib.sha256(f"whatsapp-twin-db-{api_key}".encode()).hexdigest()[:32]
