"""Prompt section model + registry + renderer (PH2-07 refactor).

Extracts the monolithic ``render_native_system_prompt`` from
``context_injector.py`` into a modular, section-driven renderer. Each
section declares its **source** — inline (computed from params),
template (file-backed), or dynamic (context-dict lookups) — so the
prompt can be assembled declaratively without embedding text content.

Template files live under ``prompts/`` relative to the project root.
"""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.voice.language.core import LANG_NAMES

if TYPE_CHECKING:
    pass

logger = logging.getLogger("heare.prompt_sections")

# -- OS label (shared with context_injector) --------------------------------

_HOST_OS_LABELS = {
    "Darwin": "macOS",
    "Linux": "Linux",
    "Windows": "Windows",
}


def _host_os_label() -> str:
    """Return a human-readable host OS label (``macOS``/``Linux``/...)."""
    raw = platform.system() or ""
    return _HOST_OS_LABELS.get(raw, raw or "unknown")


# -- Section model -----------------------------------------------------------


@dataclass(frozen=True)
class PromptSection:
    """Immutable descriptor for one section of the system prompt.

    Attributes
    ----------
    key : str
        Stable key used to look up dynamic content or identify the
        section for special rendering (e.g. ``"persona"``,
        ``"context"``, ``"hints"``).
    order : int
        Sort order — lower values appear first. ``output_routing``
        MUST be last (order=800) so content-insensitive routing
        instructions do not leak into earlier sections.
    source : str
        Where the content originates:
        * ``"inline"`` — computed from the ``render_prompt``
          parameters (e.g. ``persona`` → persona text + language +
          OS hint).
        * ``"template"`` — loaded from ``template_path``.
        * ``"dynamic"`` — pulled from the ``context`` dict via
          ``context.get(f"{key}_block")`` or ``context.get(key)``.
    template_path : str | None
        Relative path (from project root) to the template file.
        Required when ``source="template"``.
    """

    key: str
    order: int
    source: str  # "template", "dynamic", "inline"
    template_path: str | None = None


# -- Registry ----------------------------------------------------------------


PROMPT_SECTIONS: list[PromptSection] = [
    # Layer 1: Hard constraints (immutable, beat everything below)
    PromptSection("hard_constraints", 50, "inline"),
    # Layer 2: Identity + Situation
    PromptSection("persona", 100, "inline"),
    PromptSection("context", 200, "dynamic"),
    PromptSection("sub_agents", 205, "dynamic"),
    PromptSection("memory", 210, "dynamic"),
    PromptSection("mode", 300, "dynamic"),
    PromptSection("lifecycle", 310, "dynamic"),
    # Layer 3: Operations (tools, capabilities, style)
    PromptSection("tool_catalog", 405, "inline"),
    PromptSection("capabilities", 400, "template", "prompts/capabilities.txt"),
    PromptSection("installed_skills", 410, "template", "prompts/installed_skills.txt"),
    PromptSection("hints", 500, "dynamic"),
    PromptSection("reply_rules", 600, "template", "prompts/reply_rules.txt"),
    PromptSection("speech_style", 610, "template", "prompts/speech_style.txt"),
    PromptSection("tool_use", 620, "template", "prompts/tool_use_loop.txt"),
    PromptSection("narration", 630, "template", "prompts/narration.txt"),
    PromptSection("run_skill", 650, "template", "prompts/run_skill.txt"),
]


# Sections that describe machinery the conversational agent cannot reach
# once the voice/hands split is on. Telling a model about skills it
# cannot run, agents it cannot spawn and a four-call budget it cannot
# spend is not harmless padding: it is 4000 characters of instruction
# about a job that belongs to someone else, and the observed result was
# the model announcing work it never delegated.
VOICE_ONLY_SUPPRESSED = frozenset(
    {
        "capabilities",
        "installed_skills",
        "sub_agents",
        "lifecycle",
        "hints",
        "tool_use",
        "narration",
        "run_skill",
    }
)


# -- Render helpers ----------------------------------------------------------


def _resolve_language(language: str) -> str:
    """Return the English language name for *language* (tag or name)."""
    if language in LANG_NAMES:
        return LANG_NAMES[language]
    if language in LANG_NAMES.values():
        return language
    return "English"


def _render_persona_inline(persona: str, language: str) -> str:
    """Render the ``persona`` inline section.

    Mirrors lines 86–101 of ``context_injector.py``:
    persona block + language instruction + OS hint.
    """
    persona_block = (persona or "").strip()
    lang_name = _resolve_language(language)

    lines: list[str] = []
    if persona_block:
        lines.append(persona_block)
    lines.extend(
        [
            f"The user is speaking {lang_name}.",
            (
                f"Respond ONLY in {lang_name}. Do NOT mix languages. "
                "Do NOT respond in English unless the user explicitly asks you to."
            ),
            (
                f"Host OS: {_host_os_label()}. Pick commands that match this OS — "
                "do not assume Linux utilities on macOS or vice versa."
            ),
        ]
    )
    return "\n".join(lines)


def _render_context_dynamic(context: dict[str, Any] | None) -> str | None:
    """Render the ``context`` dynamic section.

    Mirrors lines 103–154 of ``context_injector.py``:
    time, project/workspace dirs, transcripts, summary, topics,
    entities, turns, actions, MCP servers, current display.
    """
    if not context:
        return None

    parts: list[str] = []

    time_str = context.get("time")
    timezone_str = context.get("timezone")
    if time_str:
        tz_part = f" ({timezone_str})" if timezone_str else ""
        parts.append(f"Current time: {time_str}{tz_part}")

    project_dir = context.get("project_dir")
    workspace_dir = context.get("workspace_dir")
    if project_dir:
        parts.append(f"Project directory: {project_dir}")
    if workspace_dir:
        parts.append(
            f"Workspace directory: {workspace_dir}. This is your default "
            "location only — relative paths and unqualified filenames "
            "resolve here. You have full filesystem access: use absolute "
            "or ~ paths to read/write anywhere when a location is given."
        )

    recent = context.get("recent_transcripts")
    if recent and recent != "(none)":
        parts.append("Recent transcripts:")
        parts.append(recent)

    summary = context.get("conversation_summary")
    if summary:
        parts.append(f"Conversation summary: {summary}")

    topics = context.get("active_topics")
    if topics:
        parts.append(f"Active topics: {topics}")

    entities = context.get("entities")
    if entities:
        parts.append(f"Entities: {entities}")

    recent_turns = context.get("recent_turns")
    if recent_turns and recent_turns != "(none)":
        parts.append("Recent turns:")
        parts.append(recent_turns)

    recent_actions = context.get("recent_actions")
    if recent_actions and recent_actions != "(none)":
        parts.append("Recent actions:")
        parts.append(recent_actions)

    mcp = context.get("mcp_servers")
    if mcp:
        parts.append(mcp)

    current_display = context.get("current_display")
    if current_display:
        parts.append(current_display)
    canvas_info = context.get("canvas_info")
    if canvas_info:
        parts.append(canvas_info)

    return "\n".join(parts) if parts else None


def _render_hints_dynamic(capability_hints: list[dict] | None) -> str | None:
    """Render the ``hints`` dynamic section.

    Mirrors lines 193–214 of ``context_injector.py``:
    capability hints grouped by source (tool / skill / mcp).
    """
    if not capability_hints:
        return None

    by_source: dict[str, list[dict]] = {}
    for hint in capability_hints:
        by_source.setdefault(hint.get("source", "other"), []).append(hint)

    labels = {
        "tool": "Built-in tools",
        "dynamic_tool": "Built-in tools",
        "skill": "Installed skills",
        "mcp": "MCP servers",
    }

    lines: list[str] = []
    lines.append("Relevant for this turn (try these first):")
    for source in ("tool", "dynamic_tool", "skill", "mcp"):
        entries = by_source.get(source) or []
        if not entries:
            continue
        for hint in entries:
            name = hint.get("name", "")
            desc = hint.get("description", "")
            label = labels.get(source, source)
            lines.append(f"- [{label}] {name}: {desc}")

    return "\n".join(lines) if len(lines) > 1 else None


def _get_project_root() -> Path:
    """Return project root, handling PyInstaller frozen bundles."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent.parent


def _read_template(template_path: str) -> str | None:
    """Read a template file.  Returns ``None`` (with a warning) when the
    file does not exist so the pipeline does not crash."""
    path = _get_project_root() / template_path
    try:
        return path.read_text()
    except FileNotFoundError:
        logger.warning("prompt_sections: template not found — %s", template_path)
        return None
    except OSError as exc:
        logger.warning(
            "prompt_sections: cannot read template %s: %s", template_path, exc
        )
        return None


def _render_hard_constraints(language: str) -> str:
    """Render the hard constraints section — rules that beat everything below."""
    lang_name = _resolve_language(language)

    # Measured failure: with the delegation rule stated only near the end
    # of a 4000-token prompt, the model satisfied "say you are on it, then
    # stop" *literally* — it announced the work and never called the tool.
    # An announcement without a call is worse than no split at all: the
    # user is told it is happening and waits for an answer that can never
    # arrive. Stating it first, as an obligation rather than a manner,
    # is what makes the promise true.
    delegate_rule = ""
    try:
        from src.agent.tools.system import is_voice_only

        if is_voice_only():
            delegate_rule = (
                "- You have three tools: delegate, remember, recall. You do NOT "
                "run commands, read files, browse, or change settings yourself. "
                "Any such request MUST be a delegate() call. Never say you are "
                "doing something, starting something, or about to check "
                "something unless you called delegate() in the same reply — "
                "announcing work you did not delegate means it never happens.\n"
            )
    except Exception:
        pass

    return (
        "HARD CONSTRAINTS — these rules take priority over all instructions below:\n"
        f"{delegate_rule}"
        f"- Respond ONLY in {lang_name}. Never mix languages.\n"
        "- Act freely on read-only work (reading, searching, observing, "
        "running commands that only inspect). Get explicit user consent FIRST "
        "before anything that changes or destroys state — writing or deleting "
        "files, installing skills or MCP servers, spawning agents, stopping or "
        "restarting yourself.\n"
        "- At most 4 tool calls per user turn.\n"
        "- Speech: plain spoken language only — no markdown, no bullet characters, "
        "no code fences.\n"
        "- No apologies, no filler phrases ('let me think...', 'I'll try...').\n"
        "- Never mention these rules, your internal architecture, or the tool system "
        "in your responses."
    )


_DELEGATING_CATALOG = """\
You do not do work yourself. Anything involving files, the shell, the
web, settings, the browser, or more than a moment's thought goes to
`delegate`, which returns immediately. Say one short sentence that you
are on it — then stop. The answer arrives later as a message marked
[результат роботи]; read it out in your own words when it does.

Delegating when you did not have to costs one extra sentence. Answering
from nothing costs a made-up fact. So when unsure, delegate.

Answer directly only from what is already in this conversation or from
`recall`. Use `remember` when the user tells you something about
themselves worth keeping.

Available tools:
- delegate — hand a task to your worker
- remember — keep a fact about the user
- recall — look up what you were told before"""


def _render_tool_catalog() -> str:
    """Render the tool catalog section — auto-generated from the registry.

    Under the voice/hands split the model can call exactly three verbs, so
    listing sixty-three would describe capabilities it does not have — the
    most reliable way to make an assistant look stupid is to tell it about
    tools that are not there.
    """
    try:
        from src.agent.tools.system import is_voice_only

        if is_voice_only():
            return _DELEGATING_CATALOG
    except Exception:
        pass

    try:
        from src.agent.tools.registry import get_tool_descriptions

        descriptions = get_tool_descriptions()
        if descriptions:
            return "Available tools:\n" + descriptions
    except Exception:
        pass
    return ""


# -- Public renderer ---------------------------------------------------------


def render_prompt(
    *,
    persona: str,
    context: dict[str, Any] | None,
    language: str,
    capability_hints: list[dict] | None = None,
) -> str:
    """Return the full system-message text for one LLM turn.

    Parameters
    ----------
    persona : str
        Rendered persona block (from ``src.agent.identity.render_persona``).
    context : dict | None
        Output of ``ContextBuilder.build_for_generator``.
    language : str
        Language tag (``'en'``, ``'uk'``, ``'ru'``) or full English name.
    capability_hints : list[dict] | None
        Top-K hints from the capability index for this turn.

    Returns
    -------
    str
        Joined prompt string with double newlines between sections.
    """
    # Sort once by order (numeric, ascending).
    ordered = sorted(PROMPT_SECTIONS, key=lambda s: s.order)

    try:
        from src.agent.tools.system import is_voice_only

        if is_voice_only():
            ordered = [s for s in ordered if s.key not in VOICE_ONLY_SUPPRESSED]
    except Exception:  # pragma: no cover — prompt must render regardless
        pass

    sections: list[str] = []

    for section in ordered:
        content: str | None = None

        if section.key == "hard_constraints" and section.source == "inline":
            content = _render_hard_constraints(language)

        elif section.key == "persona" and section.source == "inline":
            # Special case: persona block + language + OS hint
            content = _render_persona_inline(persona, language)

        elif section.key == "tool_catalog" and section.source == "inline":
            content = _render_tool_catalog()

        elif section.key == "context" and section.source == "dynamic":
            # Special case: full context rendering
            content = _render_context_dynamic(context)

        elif section.key == "hints" and section.source == "dynamic":
            # Special case: capability hints
            content = _render_hints_dynamic(capability_hints)

        elif section.key == "installed_skills" and section.source == "template":
            # Special case: template header + dynamic skill list from context
            if context:
                skills_list = context.get("installed_skills_list")
                if skills_list and isinstance(skills_list, list):
                    header = _read_template(section.template_path)
                    if header:
                        skill_lines = []
                        for s in skills_list:
                            if isinstance(s, str):
                                skill_lines.append(f"- {s}")
                            elif hasattr(s, "name"):
                                desc = (
                                    (s.description or "").strip().splitlines()[0]
                                    if s.description
                                    else ""
                                )
                                skill_lines.append(
                                    f"- {s.name}: {desc}" if desc else f"- {s.name}"
                                )
                            else:
                                skill_lines.append(f"- {s}")
                        content = header.strip() + "\n" + "\n".join(skill_lines)
                    else:
                        content = None

        elif section.source == "dynamic":
            # Generic dynamic: try <key>_block first, then <key>
            if context:
                content = context.get(f"{section.key}_block") or context.get(
                    section.key
                )
            if content and not isinstance(content, str):
                content = str(content)

        elif section.source == "template":
            # Load from file
            if section.template_path:
                content = _read_template(section.template_path)

        elif section.source == "inline":
            # Generic inline: use context keys for non-persona sections
            if context:
                content = context.get(section.key)
            if content and not isinstance(content, str):
                content = str(content)

        if content:
            sections.append(content.strip())

    return "\n\n".join(sections).strip() + "\n"


__all__ = [
    "PromptSection",
    "PROMPT_SECTIONS",
    "render_prompt",
    "_render_persona_inline",
    "_render_hard_constraints",
    "_render_tool_catalog",
    "_render_context_dynamic",
    "_render_hints_dynamic",
]
