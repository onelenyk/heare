"""Pluggable memory subsystem — SQLite+FTS5 backend, auto-extraction, swappable backends."""

from src.memory.base import MemoryBackend, MemoryEntry, MemoryType, NoopBackend
from src.memory.extractor import extract_and_store, extract_memories

__all__ = [
    "MemoryBackend",
    "MemoryEntry",
    "MemoryType",
    "NoopBackend",
    "extract_and_store",
    "extract_memories",
]
