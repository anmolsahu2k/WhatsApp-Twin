"""Multi-draft manager — generate multiple reply options, cycle through them.

Generates 2-3 draft variants with slightly different temperature/instructions
so the user can pick the one that feels right. Activated by pressing the
hotkey multiple times while a draft is already in the composer.
"""

from dataclasses import dataclass, field


@dataclass
class DraftSet:
    """A set of draft variants for a single reply."""
    contact_name: str
    drafts: list[str] = field(default_factory=list)
    current_index: int = 0

    @property
    def current_draft(self) -> str | None:
        if not self.drafts:
            return None
        return self.drafts[self.current_index]

    @property
    def count(self) -> int:
        return len(self.drafts)

    def next(self) -> str | None:
        """Cycle to the next draft variant."""
        if not self.drafts:
            return None
        self.current_index = (self.current_index + 1) % len(self.drafts)
        return self.current_draft

    def previous(self) -> str | None:
        """Cycle to the previous draft variant."""
        if not self.drafts:
            return None
        self.current_index = (self.current_index - 1) % len(self.drafts)
        return self.current_draft

    def add(self, draft: str):
        """Add a draft variant."""
        self.drafts.append(draft)


class DraftManager:
    """Manages multi-draft generation and cycling."""

    MAX_DRAFTS = 3

    def __init__(self):
        self._current_set: DraftSet | None = None

    @property
    def current_set(self) -> DraftSet | None:
        return self._current_set

    @property
    def has_active_set(self) -> bool:
        return self._current_set is not None and self._current_set.count > 0

    def start_new_set(self, contact_name: str, first_draft: str) -> DraftSet:
        """Start a new draft set with the first generated draft."""
        self._current_set = DraftSet(contact_name=contact_name)
        self._current_set.add(first_draft)
        return self._current_set

    def add_variant(self, draft: str) -> DraftSet | None:
        """Add another draft variant to the current set."""
        if not self._current_set:
            return None
        if self._current_set.count >= self.MAX_DRAFTS:
            return self._current_set
        self._current_set.add(draft)
        return self._current_set

    def cycle_next(self) -> str | None:
        """Get the next draft in the cycle."""
        if not self._current_set:
            return None
        return self._current_set.next()

    def cycle_previous(self) -> str | None:
        """Get the previous draft in the cycle."""
        if not self._current_set:
            return None
        return self._current_set.previous()

    def clear(self):
        """Clear the current draft set."""
        self._current_set = None

    def should_generate_variant(self, contact_name: str) -> bool:
        """Check if we should generate a new variant vs. a fresh draft.

        Returns True if there's an active set for the same contact
        with room for more variants.
        """
        if not self._current_set:
            return False
        return (
            self._current_set.contact_name == contact_name
            and self._current_set.count < self.MAX_DRAFTS
        )

    def status_text(self) -> str:
        """Current draft position for display (e.g., '2/3')."""
        if not self._current_set:
            return ""
        s = self._current_set
        return f"{s.current_index + 1}/{s.count}"
