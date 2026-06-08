"""Pluggable memory backend interface.

Defines the abstract base class and data structures that all memory
backends must implement. NoopBackend provides a silent no-op implementation
for testing and disabled-memory modes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MemoryType(StrEnum):
    """Categories of memories the system can store."""

    FACT = "fact"
    PREFERENCE = "preference"
    DECISION = "decision"
    EVENT = "event"


@dataclass
class MemoryEntry:
    """A single memory record.

    Attributes:
        id: Unique identifier for this memory.
        type: The category of memory (fact, preference, decision, event).
        content: The text content of the memory.
        source: Origin of the memory (e.g., "user_stated", "inferred", "system").
        confidence: How confident the system is in this memory (0.0–1.0).
        created_ts: Unix timestamp when the memory was created.
        last_accessed: Unix timestamp of last retrieval.
        metadata: Arbitrary key-value data attached to the memory.
    """

    id: str
    type: MemoryType
    content: str
    source: str = "user_stated"
    confidence: float = 1.0
    created_ts: float = 0.0
    last_accessed: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryBackend(ABC):
    """Abstract interface for pluggable memory backends.

    Concrete implementations (SQLite, JSON file, vector DB, etc.) must
    implement all seven methods.  Use NoopBackend for silent / test mode.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Set up the backend (create tables, open connections, etc.)."""
        ...

    @abstractmethod
    async def store(self, entry: MemoryEntry) -> str:
        """Persist a memory entry.

        Args:
            entry: The memory to store.

        Returns:
            The memory ID (may differ from entry.id if the backend
            assigns its own).
        """
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        types: list[MemoryType] | None = None,
    ) -> list[MemoryEntry]:
        """Search memories by semantic or keyword match.

        Args:
            query: The search query string.
            limit: Maximum number of results to return.
            types: Optional filter by memory types.

        Returns:
            List of matching MemoryEntry objects, ordered by relevance.
        """
        ...

    @abstractmethod
    async def context(
        self,
        query: str | None = None,
        *,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Retrieve contextually relevant memories.

        Args:
            query: Optional query to guide relevance ranking.
            limit: Maximum number of results to return.

        Returns:
            List of MemoryEntry objects relevant to the current context.
        """
        ...

    @abstractmethod
    async def forget(self, memory_id: str) -> bool:
        """Remove a memory by ID.

        Args:
            memory_id: The ID of the memory to remove.

        Returns:
            True if the memory was found and removed, False otherwise.
        """
        ...

    @abstractmethod
    async def stats(self) -> dict:
        """Return backend statistics.

        Returns:
            Dictionary with at least a "total" key (total memory count).
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release backend resources (connections, file handles, etc.)."""
        ...


class NoopBackend(MemoryBackend):
    """Silent no-op memory backend.

    All methods succeed with neutral defaults.  Useful for testing,
    disabled-memory mode, and as a fallback reference implementation.
    """

    async def initialize(self) -> None:
        return

    async def store(self, entry: MemoryEntry) -> str:
        return entry.id

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        types: list[MemoryType] | None = None,
    ) -> list[MemoryEntry]:
        return []

    async def context(
        self,
        query: str | None = None,
        *,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        return []

    async def forget(self, memory_id: str) -> bool:
        return True

    async def stats(self) -> dict:
        return {"total": 0}

    async def close(self) -> None:
        return
