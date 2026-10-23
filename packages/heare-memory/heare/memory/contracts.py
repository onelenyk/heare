"""What memory agrees on before it grows: a fact, a store, two reports.

The profile is curated; the journal is what happened. A *fact* is a
statement about the owner, with where it came from and when it was true.
These shapes are the border between the three Wave 2 jobs — extraction,
consolidation and the legacy migration — so none of them invents its own
version of the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# A fact is proposed, confirmed, or rejected. Only confirmed facts belong
# in a prompt; pending ones wait for the owner or a later review.
PENDING = "pending"
CONFIRMED = "confirmed"
REJECTED = "rejected"

# Where the words came from, and how much they are trusted. The names are
# deliberately the same as the journal's trust vocabulary.
USER_SAID = "user_said"
USER_CONFIRMED = "user_confirmed"
MODEL_INFERRED = "model_inferred"
TOOL_OUTPUT = "tool_output"
LEGACY = "legacy"
CONSOLIDATED = "consolidated"


@dataclass(frozen=True)
class Fact:
    """One statement, with its provenance and its time."""

    subject: str
    predicate: str
    value: str
    text: str
    source: str
    trust: str
    status: str = PENDING
    confidence: float = 1.0
    valid_from: float = 0.0
    valid_to: float | None = None
    source_ids: tuple[str, ...] = ()
    supersedes: int | None = None
    created_at: float = 0.0
    id: int | None = None


class FactStore(Protocol):
    """Where facts live. ``add`` is idempotent by ``source_ids``: the same
    event, migrated row or consolidation pass never makes a second fact."""

    def add(self, fact: Fact) -> int: ...

    def has_source(self, source_id: str) -> bool: ...

    def get(self, fact_id: int) -> Fact | None: ...

    def current(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        now: float | None = None,
        include_pending: bool = False,
    ) -> list[Fact]: ...

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        now: float | None = None,
        include_pending: bool = False,
    ) -> list[Fact]: ...

    def confirm(self, fact_id: int) -> bool: ...

    def reject(self, fact_id: int) -> bool: ...

    def supersede(self, old_id: int, new: Fact) -> int: ...

    def forget(self, fact_id: int) -> bool: ...


@dataclass(frozen=True)
class ConsolidationReport:
    """One pass over the journal: what was read, added, already known."""

    considered: int = 0
    added: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class ReviewReport:
    """One day read by the model: what it saw, proposed, already knew."""

    considered: int = 0
    added: int = 0
    skipped: int = 0
    error: str | None = None


@dataclass(frozen=True)
class MigrationReport:
    """One pass over the old ``memories`` table."""

    read: int = 0
    imported: int = 0
    archived: int = 0
    duplicates: int = 0
    dry_run: bool = False
