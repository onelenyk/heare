"""Memory backend factory — selects the backend based on configuration."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Settings
from src.memory.base import MemoryBackend, NoopBackend


def create_memory_backend(settings: "Settings") -> MemoryBackend:
    backend = settings.memory_backend
    match backend:
        case "sqlite":
            from src.memory.sqlite_backend import SQLiteBackend

            return SQLiteBackend(db_path=settings.db_path)
        case "noop":
            return NoopBackend()
        case "engram":
            raise NotImplementedError("Engram backend not yet implemented")
        case "mem0":
            raise NotImplementedError("Mem0 backend not yet implemented")
        case _:
            raise ValueError(f"Unknown memory backend: {backend!r}")
