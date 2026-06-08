"""Unified capability index — single read-only view across tools, skills, MCPs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.skills.agent_skills import get_skills_loader
from src.skills.mcp_utils import read_mcp_servers
from src.agent.tools.registry import TOOLS, _DYNAMIC_TOOLS

logger = logging.getLogger("heare.capability_index")

Source = Literal["builtin", "dynamic", "skill", "mcp"]

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "with",
        "from",
        "as",
        "it",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

_SOURCE_PRIORITY: dict[Source, int] = {
    "skill": 3,
    "mcp": 2,
    "dynamic": 1,
    "builtin": 0,
}


@dataclass(frozen=True)
class IndexEntry:
    source: Source
    name: str
    description: str
    args_schema: dict | None = None
    network_required: bool = False
    popularity_score: float | None = None
    install_url: str | None = None
    schema_version: int = 1
    checksum: str | None = None
    launch: dict | None = None


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class CapabilityIndex:
    def __init__(self, settings: object, workspace_dir: Path) -> None:
        self._settings = settings
        self._workspace_dir = Path(workspace_dir).expanduser().resolve()
        self._entries: list[IndexEntry] = []
        self._inverted: dict[str, set[int]] = {}

    @property
    def entries(self) -> list[IndexEntry]:
        return list(self._entries)

    def build(self) -> None:
        entries: list[IndexEntry] = []

        for tool in TOOLS.values():
            if not tool.enabled:
                continue
            entries.append(
                IndexEntry(
                    source="builtin",
                    name=tool.name,
                    description=tool.description,
                )
            )

        for tool in _DYNAMIC_TOOLS.values():
            if not tool.enabled:
                continue
            entries.append(
                IndexEntry(
                    source="dynamic",
                    name=tool.name,
                    description=tool.description,
                )
            )

        try:
            loader = get_skills_loader(self._settings)
            for meta in loader.discover():
                entries.append(
                    IndexEntry(
                        source="skill",
                        name=meta.name,
                        description=meta.description,
                    )
                )
        except Exception as exc:
            logger.debug("skill discovery failed: %s", exc)

        try:
            servers = read_mcp_servers(self._workspace_dir)
            for name, entry in servers.items():
                desc = (
                    entry["description"]
                    if isinstance(entry, dict) and entry.get("description")
                    else f"MCP server: {name}"
                )
                entries.append(
                    IndexEntry(
                        source="mcp",
                        name=name,
                        description=desc,
                    )
                )
        except FileNotFoundError as exc:
            logger.debug("no .mcp.json present: %s", exc)
        except Exception as exc:
            logger.debug("mcp read failed: %s", exc)

        inverted: dict[str, set[int]] = {}
        for idx, e in enumerate(entries):
            for tok in _tokenize(f"{e.name} {e.description}"):
                inverted.setdefault(tok, set()).add(idx)

        self._entries = entries
        self._inverted = inverted

    def rebuild(self) -> None:
        loader = get_skills_loader(self._settings)
        invalidate = getattr(loader, "invalidate", None)
        if callable(invalidate):
            invalidate()
        self.build()

    def query(self, intent: str, top_k: int = 3) -> list[IndexEntry]:
        if not self._entries:
            return []

        tokens = _tokenize(intent)
        scores: dict[int, int] = {}
        for tok in tokens:
            for idx in self._inverted.get(tok, ()):
                scores[idx] = scores.get(idx, 0) + 1

        if not scores:
            needle = intent.lower().strip()
            if not needle:
                return []
            hits = [
                idx
                for idx, e in enumerate(self._entries)
                if needle in e.description.lower() or needle in e.name.lower()
            ]
            ranked = sorted(hits, key=lambda i: self._sort_key(i, 1))
            return [self._entries[i] for i in ranked[:top_k]]

        ranked = sorted(scores.keys(), key=lambda i: self._sort_key(i, scores[i]))
        return [self._entries[i] for i in ranked[:top_k]]

    def _sort_key(self, idx: int, score: int) -> tuple:
        e = self._entries[idx]
        pop = e.popularity_score if e.popularity_score is not None else float("-inf")
        return (-score, -pop, -_SOURCE_PRIORITY[e.source], idx)


def build_capability_index(settings: object, workspace_dir: Path) -> CapabilityIndex:
    idx = CapabilityIndex(settings, workspace_dir)
    idx.build()
    return idx


__all__ = ["IndexEntry", "CapabilityIndex", "build_capability_index"]
