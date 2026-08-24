"""System prompt assembly for the spine.

Composes Ukrainian voice-first prompts with optional persona, memory, and
recent exchanges — the spine's three key ingredients, without daemon machinery.

DeepSeek's API caches prompt prefixes: repeated leading tokens across requests
are billed at roughly a tenth of the price and process faster. The system
prompt is messages[0] — the very start of that prefix — so the internal
section order matters. This builder puts the static block (voice rules, then
persona — unchanged across turns for a given persona) first, and the dynamic
block (memory, recent exchanges, then the date/time line last of all, since it
changes on every single turn) after it. A single changed byte anywhere in the
prefix invalidates the cache for everything that follows it, so ordering
static-before-dynamic, and putting the fastest-changing field deepest, is what
lets the cache actually hit turn over turn instead of paying full price every
time.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def build_system_prompt(
    *,
    persona: str = "",
    mcp_block: str = "",
    memory_block: str = "",
    exchanges: list[dict] | None = None,
    situation_block: str = "",
    now: datetime | None = None,
) -> str:
    """Compose the spine's system prompt.

    Structured as a STATIC block followed by a DYNAMIC block, in that order,
    so DeepSeek's prefix cache hits on the unchanging lead every turn:

    STATIC (identical across turns given the same persona):
      - voice rules (short Ukrainian spoken prose, no markup — keep the
        spirit of loop.py's DEFAULT_SYSTEM_PROMPT)
      - persona paragraph
      - mcp_block: what the worker can reach through MCP, if anything

    DYNAMIC (changes turn to turn; ordered slowest-changing to fastest):
      - 'Що ти пам'ятаєш:' + memory_block
      - 'Останні розмови:' + exchanges rendered as 'Користувач: .../Ти: ...'
        lines (cap each line at 200 chars)
      - situation + what is outstanding between you (from the engine)
      - current date/time line, LAST of all, since it changes most often

    Sections are added ONLY when non-empty. Same information, same headers,
    same truncation rules as before — only the order changed.

    Deterministic: same inputs -> same string.
    """
    voice_rules = (
        "Ти голосовий асистент на ім'я heare. Тебе слухають вухами, а не "
        "читають: відповідай коротко, українською, простою прозою — без "
        "розмітки, списків і коду. Одна-три фрази, як у живій розмові."
    )

    # STATIC block — stable across turns for a given persona.
    parts: list[str] = [voice_rules]

    if persona:
        parts.append(persona)

    # Still STATIC: the connected MCP servers are fixed for the life of
    # the process, so this text is identical turn over turn and the
    # prefix cache survives it. Passed in as a string — this module
    # knows nothing about bridges, sessions or subprocesses.
    if mcp_block:
        parts.append(mcp_block)

    # DYNAMIC block — ordered slowest-changing to fastest-changing.
    if memory_block:
        parts.append(f"Що ти пам'ятаєш:\n{memory_block}")

    if exchanges:
        rendered_lines: list[str] = []
        for exchange in exchanges:
            user_text = (exchange.get("user") or "").strip()
            agent_text = (exchange.get("agent") or "").strip()

            if user_text:
                truncated = user_text[:200]
                rendered_lines.append(f"Користувач: {truncated}")

            if agent_text:
                truncated = agent_text[:200]
                rendered_lines.append(f"Ти: {truncated}")

        if rendered_lines:
            parts.append("Останні розмови:\n" + "\n".join(rendered_lines))

    # The situation, then the clock — both last, both changing fastest.
    # This block is the half of the engine that never gets spoken: even
    # an intent it decides not to raise is here, so when the user opens
    # the conversation the assistant answers knowing what hangs between
    # them rather than from a blank page.
    if situation_block:
        parts.append(situation_block)

    # The raw stamp only when nothing better is on offer. Two lines both
    # opening "Зараз:" — one in words, one as 2026-08-18 01:08:00 — read
    # as two different claims about the present, and the machine-shaped
    # one is the weaker: it carries no weekday and nothing about whether
    # that hour is late.
    if now is not None and not situation_block:
        date_str = now.strftime("%Y-%m-%d %H:%M:%S")
        parts.append(f"Зараз: {date_str}")

    return "\n\n".join(parts)


# --- turning identity.json into a VOICE persona -------------------------
#
# identity.json is written by an LLM, and older generations wrote the
# `creature` field as an English capability advertisement ("an ambient AI
# that acts as your all-purpose terminal, browser and tool system —
# executing code ... on its own initiative"). Injected verbatim, that text
# tells the voice model it IS a terminal, so it narrates work it never did
# and promises initiative it structurally cannot take: the spine has three
# verbs (delegate / remember / recall) and never speaks unprompted.
#
# So a `creature` string is only trusted as CHARACTER. It is dropped when
# it looks like a capability blurb instead — see _is_character().
_CAPABILITY_STEMS = (
    # English
    "terminal", "browser", "shell", "bash", "code", "coding", "execut",
    "run", "automat", "search", "web", "internet", "api", "file", "files",
    "script", "tool", "tools", "toolkit", "multi-tool", "initiativ",
    "autonom", "proactiv", "agent", "assistant", "ai", "llm", "model",
    "system", "capab", "task", "tasks", "comput", "app", "apps", "device",
    # Ukrainian / Russian
    "термінал", "браузер", "консол", "команд", "код", "викону", "запуска",
    "автоматиз", "пошук", "шука", "інтернет", "файл", "скрипт",
    "інструмент", "ініціатив", "автоном", "проактив", "асистент",
    "помічник", "систем", "застосун", "пристр", "мереж", "завдан",
)

_CAPABILITY_RE = re.compile(
    r"\b(?:" + "|".join(_CAPABILITY_STEMS) + r")",
    re.IGNORECASE | re.UNICODE,
)

# English trait words the generator used to emit for `vibe`. Tone words are
# harmless claims (unlike capability claims) but they must not leave an
# English clause inside a Ukrainian spoken prompt, so known ones are
# translated and unknown Latin ones are dropped.
_TRAIT_UK = {
    "curious": "допитливий",
    "pragmatic": "прагматичний",
    "practical": "практичний",
    "efficient": "діловитий",
    "direct": "прямий",
    "concise": "стислий",
    "brief": "стислий",
    "warm": "теплий",
    "friendly": "дружній",
    "professional": "професійний",
    "engaged": "уважний",
    "attentive": "уважний",
    "precise": "точний",
    "calm": "спокійний",
    "playful": "грайливий",
    "patient": "терплячий",
    "witty": "дотепний",
    "dry": "стриманий",
    "focused": "зосереджений",
    "thoughtful": "вдумливий",
    "helpful": "готовий допомогти",
    "opinionated": "з власною думкою",
}

_MAX_CREATURE_CHARS = 60


def _cyrillic_share(text: str) -> float:
    """Share of the letters in *text* that are Cyrillic (0.0 for no letters)."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    cyrillic = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    return cyrillic / len(letters)


def _is_character(creature: str) -> bool:
    """True when *creature* reads as WHO the assistant is, not WHAT it can do.

    Three cheap gates, all of which a hand-written character phrase passes
    and the machine-written capability blurb fails:

      1. short — a character is a noun phrase ("спокійний голос у
         навушниках"), a capability list is a sentence;
      2. Ukrainian — the prompt around it is Ukrainian spoken prose;
      3. no capability/initiative vocabulary anywhere in it.
    """
    if not creature or len(creature) > _MAX_CREATURE_CHARS:
        return False
    if _cyrillic_share(creature) < 0.6:
        return False
    return _CAPABILITY_RE.search(creature) is None


def _render_vibe(vibe: str) -> str:
    """Render `vibe` as a Ukrainian trait list; drop what cannot be rendered."""
    traits: list[str] = []
    for raw in re.split(r"[,;/]|\bта\b|\bі\b|\band\b", vibe):
        token = raw.strip().strip(".").strip()
        if not token:
            continue
        if _cyrillic_share(token) >= 0.6:
            rendered = token
        else:
            rendered = _TRAIT_UK.get(token.lower(), "")
        if rendered and rendered not in traits:
            traits.append(rendered)
    return ", ".join(traits)


def load_persona(settings: object, *, speaks_first: bool = False) -> str:
    """Read identity.json and render it as a Ukrainian VOICE persona.

    Resolves the path from ``settings.identity_file`` when present, else
    ``~/.heare/identity.json``. Missing file, bad JSON, or any other error
    -> ``''`` (never raises).

    THE RULE. The persona says who the assistant is and how the work is
    split — never what it can do. The tool catalogue and the MCP block
    already state capabilities; a second, prose account of them in the
    static head is what made the model narrate a terminal it does not have.
    So of the identity fields:

      * ``name``  — always used; it is the one field that is pure identity.
                    Without it there is no persona at all, and '' is
                    returned.
      * ``emoji`` — always used, decorative only.
      * ``creature`` — used ONLY if it passes ``_is_character()`` (short,
                    Ukrainian, free of capability/initiative vocabulary).
                    A capability blurb is dropped entirely rather than
                    trimmed: half of "your all-purpose terminal, browser
                    and tool system" is still a false claim.
      * ``vibe``  — tone, rendered through ``_render_vibe()`` so an English
                    trait list does not survive into Ukrainian prose.
      * ``tagline`` — deliberately unused. It is a self-introduction meant
                    for the UI and typically promises doing ("Слухаю,
                    розумію, роблю"), which is the same claim in miniature.

    Everything else in the persona is fixed text, true of the engine as
    built: it speaks, it hands work to its worker, and it answers when
    addressed. Those sentences do not come from identity.json, so a
    regenerated identity cannot make them false.

    A feature switch could, and on 24 August one did. «Сам розмову не
    починаю» was written as a constant because no identity could falsify
    it — and then `repeats` and `watcher` were switched on, and the
    assistant spent an afternoon introducing itself as something that
    never speaks first while the engine was arranging to do exactly that.
    So `speaks_first` is an argument: the composition root knows which
    features are wired and is the only thing that does.

    The result is part of the STATIC head of the system prompt (see the
    module docstring), so it must stay byte-identical across turns for a
    given identity file — this function is pure given the file contents.
    """
    try:
        # Resolve identity file path
        identity_path: Path | None = None
        if hasattr(settings, "identity_file"):
            path_obj = getattr(settings, "identity_file")
            identity_path = Path(path_obj) if path_obj is not None else None

        if identity_path is None:
            # Fallback to default location
            identity_path = Path.home() / ".heare" / "identity.json"

        if not identity_path.exists():
            return ""

        # Read and parse identity JSON
        text = identity_path.read_text()
        data: dict[str, Any] = json.loads(text)

        name = (data.get("name") or "").strip()
        creature = (data.get("creature") or "").strip()
        vibe = (data.get("vibe") or "").strip()
        emoji = (data.get("emoji") or "").strip()

        # A persona without a name is not an identity.
        if not name:
            return ""

        head = f"Я на ім'я {name}"
        if emoji:
            head += f" {emoji}"
        if _is_character(creature):
            head += f", я {creature}"
        head += "."

        sentences = [
            head,
            "Я голос: розмовляю з тобою, а роботу віддаю своєму робітнику "
            "й переказую, що вийшло.",
            "Здебільшого озиваюсь, коли до мене звертаються; зрідка кажу "
            "щось сам, якщо є про що."
            if speaks_first
            else "Сам розмову не починаю — озиваюсь, коли до мене "
                 "звертаються.",
        ]

        traits = _render_vibe(vibe)
        if traits:
            sentences.append(f"Мій стиль — {traits}.")

        return " ".join(sentences)

    except Exception:
        # Silently catch all errors: missing file, JSON parse error, etc.
        return ""
