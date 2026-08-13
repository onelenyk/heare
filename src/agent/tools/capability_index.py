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
        # Ukrainian function words. The intents this index matches against
        # are spoken Ukrainian; without these, «будь ласка» outscores verbs.
        "і", "й", "та", "а", "але", "у", "в", "на", "з", "із", "зі",
        "до", "по", "за", "про", "під", "над", "при", "без",
        "це", "ця", "цей", "ці", "що", "як", "чи", "не", "ні", "так",
        "я", "ти", "ми", "він", "вона", "воно", "вони",
        "мені", "мене", "тобі", "собі", "мій", "моя", "моє", "мої",
        "є", "бо", "ж", "би", "б", "будь", "ласка", "скільки", "де", "коли",
    }
)

# Cyrillic must survive tokenisation: the user speaks Ukrainian, and a
# pattern of [a-z0-9_] silently reduced every spoken intent to zero tokens —
# the index could never match anything said aloud.
_TOKEN_RE = re.compile(r"[a-zа-щьюяіїєґё0-9_']+")

_CYRILLIC_RE = re.compile(r"[а-щьюяіїєґё]")

# Ukrainian is inflected: «вкладки», «вкладок», «вкладку» are one word.
# A full stemmer is overkill; stripping one common ending (keeping a stem of
# at least 4 letters) plus prefix matching in query() covers the paradigm.
_UK_SUFFIXES = (
    "ями", "ами", "ові", "еві", "єві", "ього", "ьому", "ого", "ому",
    "ими", "іми", "ах", "ях", "ів", "їв", "ам", "ям", "ом", "ем", "єм",
    "ій", "ий", "ої", "ею", "ою", "ок", "ку", "ці", "ти", "ло", "ла", "ли",
    "и", "і", "ї", "а", "я", "у", "ю", "е", "є", "о", "ь",
)


def _stem(token: str) -> str:
    for suf in _UK_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 4:
            return token[: -len(suf)]
    return token

_SOURCE_PRIORITY: dict[Source, int] = {
    "skill": 3,
    "mcp": 2,
    "dynamic": 1,
    "builtin": 0,
}

# Tools that mutate the capability set. Advertising them while
# capability_install_enabled is off means offering and then refusing;
# they are dropped from the index (and from the worker's schema) instead.
INSTALL_TOOLS: frozenset[str] = frozenset(
    {
        "install_skill_tool",
        "create_skill",
        "install_mcp_server_tool",
        "register_mcp_server",
    }
)

# Ukrainian search keywords for the builtin tools. Descriptions are English
# and stay English (the LLM reads those fine); this map only feeds the
# inverted index so that a spoken Ukrainian intent can *find* the tool.
_UK_KEYWORDS: dict[str, str] = {
    "bash": "команда термінал консоль запусти виконай скрипт диск система",
    "read": "прочитай файл відкрий подивись вміст",
    "write": "запиши файл створи збережи",
    "web_search": "пошук інтернет знайди загугли гугл новини",
    "web_fetch": "сторінка сайт завантаж посилання лінк адреса",
    "list_browser_tabs": "вкладки таби браузер хром відкриті",
    "read_browser_page": "прочитай сторінку браузер хром",
    "navigate_browser": "відкрий сайт перейди браузер адреса",
    "open_browser_tab": "нова вкладка відкрий браузер",
    "activate_browser_tab": "перемкни вкладку браузер",
    "click_in_browser": "натисни клікни кнопка браузер",
    "fill_in_browser": "заповни форму введи поле браузер",
    "extract_in_browser": "витягни дані таблицю сторінка браузер",
    "show_text": "покажи текст екран панель дисплей",
    "show_canvas": "покажи намалюй графік схему екран",
    "read_display": "що на екрані панель дисплей",
    "set_mode": "режим тихий фокус зустріч асистент перемкни",
    "set_provider": "модель провайдер перемкни нейронка",
    "stop_daemon": "вимкнись зупинись стоп демон",
    "restart_daemon": "перезапустись рестарт демон",
    "cancel": "скасуй зупини стоп відміни",
    "remember": "запам'ятай збережи пам'ять нотатка",
    "recall": "згадай пригадай пам'ять казав",
    "forget": "забудь видали пам'ять",
    "memory_status": "пам'ять статус",
    "list_skills": "скіли навички вміння список",
    "run_skill": "запусти скіл навичку",
    "discover_capability": "знайди встанови можливість вміння скіл",
    "run_agent": "агент завдання доручи фоново",
    "agent_start": "агент запусти завдання фоново",
    "create_tool": "створи інструмент тул новий",
    "list_favorites": "улюблені збережені файли",
    "add_favorite": "додай в улюблені збережи файл",
    "show_profile": "профіль про мене хто я",
    "workflow": "кроки послідовність кілька дій",
    "create_archive": "архів запакуй стисни zip",
    "extract_archive": "розпакуй архів витягни zip",
}


@dataclass(frozen=True)
class IndexEntry:
    source: Source
    name: str
    description: str
    # Extra search-only text (e.g. Ukrainian synonyms). Indexed, never shown.
    keywords: str = ""
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
    def __init__(self, settings: object, mcp_dir: Path) -> None:
        self._settings = settings
        self._mcp_dir = Path(mcp_dir).expanduser().resolve()
        self._entries: list[IndexEntry] = []
        self._inverted: dict[str, set[int]] = {}

    @property
    def entries(self) -> list[IndexEntry]:
        return list(self._entries)

    def build(self) -> None:
        entries: list[IndexEntry] = []

        install_ok = bool(
            getattr(self._settings, "capability_install_enabled", False)
        )

        for tool in TOOLS.values():
            if not tool.enabled:
                continue
            if tool.name in INSTALL_TOOLS and not install_ok:
                continue
            entries.append(
                IndexEntry(
                    source="builtin",
                    name=tool.name,
                    description=tool.description,
                    keywords=_UK_KEYWORDS.get(tool.name, ""),
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
            servers = read_mcp_servers(self._mcp_dir)
            for name, entry in servers.items():
                desc = (
                    entry["description"]
                    if isinstance(entry, dict) and entry.get("description")
                    else f"MCP server: {name}"
                )
                kw = (
                    str(entry.get("keywords") or "")
                    if isinstance(entry, dict)
                    else ""
                )
                entries.append(
                    IndexEntry(
                        source="mcp",
                        name=name,
                        description=desc,
                        keywords=kw,
                    )
                )
        except FileNotFoundError as exc:
            logger.debug("no .mcp.json present: %s", exc)
        except Exception as exc:
            logger.debug("mcp read failed: %s", exc)

        inverted: dict[str, set[int]] = {}
        for idx, e in enumerate(entries):
            for tok in _tokenize(f"{e.name} {e.description} {e.keywords}"):
                inverted.setdefault(_stem(tok), set()).add(idx)

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

        tokens = {_stem(t) for t in _tokenize(intent)}
        scores: dict[int, int] = {}
        for tok in tokens:
            hit_ids = set(self._inverted.get(tok, ()))
            # Cyrillic stems still vary («вкладк» vs «вклад»); a shared
            # 4-letter prefix bridges what the one-suffix stemmer misses.
            # English keeps exact-match behaviour.
            if len(tok) >= 4 and _CYRILLIC_RE.search(tok):
                for key, ids in self._inverted.items():
                    if key != tok and len(key) >= 4 and (
                        key.startswith(tok) or tok.startswith(key)
                    ):
                        hit_ids |= ids
            for idx in hit_ids:
                scores[idx] = scores.get(idx, 0) + 1

        if not scores:
            needle = intent.lower().strip()
            if not needle:
                return []
            hits = [
                idx
                for idx, e in enumerate(self._entries)
                if needle in e.description.lower()
                or needle in e.name.lower()
                or (e.keywords and needle in e.keywords.lower())
            ]
            ranked = sorted(hits, key=lambda i: self._sort_key(i, 1))
            return [self._entries[i] for i in ranked[:top_k]]

        ranked = sorted(scores.keys(), key=lambda i: self._sort_key(i, scores[i]))
        return [self._entries[i] for i in ranked[:top_k]]

    def _sort_key(self, idx: int, score: int) -> tuple:
        e = self._entries[idx]
        pop = e.popularity_score if e.popularity_score is not None else float("-inf")
        return (-score, -pop, -_SOURCE_PRIORITY[e.source], idx)


def build_capability_index(settings: object, mcp_dir: Path) -> CapabilityIndex:
    idx = CapabilityIndex(settings, mcp_dir)
    idx.build()
    return idx


__all__ = ["IndexEntry", "CapabilityIndex", "build_capability_index"]
