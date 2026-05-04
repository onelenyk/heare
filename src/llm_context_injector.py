"""Per-turn system prompt injector for the Pipecat-native pipeline (PH2-07).

The Pipecat-native pipeline shares a single ``LLMContext`` across the
session. To keep the LLM grounded in fresh conversation state, the
system message at ``messages[0]`` must be regenerated for each user
turn — pulling current persona text, recent transcripts, conversation
summary, topics, entities, and the action log from
``ContextBuilder.build_for_generator``.

This module provides:

* :func:`render_native_system_prompt` — a pure function that turns
  the context dict + persona + language into a system message string.
  Tool-grammar / intent-tag rules from the legacy generator prompt
  are intentionally excluded — tools are called natively via
  ``register_function`` in PH2-03.
* :func:`create_system_prompt_injector` — a Pipecat ``FrameProcessor``
  that intercepts ``TranscriptionFrame``, builds the fresh context
  (awaiting ``ContextBuilder.build_for_generator``), mutates the
  shared ``LLMContext`` system message in-place, then forwards the
  frame so the user_aggregator can append the user turn and trigger
  the LLM run.

Pipecat imports are deferred so admin CLI paths import this module
without portaudio.
"""
from __future__ import annotations

import logging
import platform
from typing import TYPE_CHECKING, Any

from .language import LANG_NAMES


_HOST_OS_LABELS = {
    "Darwin": "macOS",
    "Linux": "Linux",
    "Windows": "Windows",
}


def _host_os_label() -> str:
    """Return a human-readable host OS label (``macOS``/``Linux``/...)."""
    raw = platform.system() or ""
    return _HOST_OS_LABELS.get(raw, raw or "unknown")

if TYPE_CHECKING:
    from .context import ContextBuilder
    from .language_state import LanguageState


logger = logging.getLogger("heare.llm_context_injector")


def render_native_system_prompt(
    *,
    persona: str,
    context: dict[str, Any] | None,
    language: str,
    capability_hints: list[dict] | None = None,
) -> str:
    """Return the system-message text for one LLM turn.

    Parameters
    ----------
    persona : str
        Rendered persona block (from src.identity.render_persona). When
        empty, a minimal default is substituted so the model still has
        a role.
    context : dict | None
        Output of ``ContextBuilder.build_for_generator`` (recent
        transcripts, conversation summary, topics, entities, recent
        actions, etc.). When ``None``, the prompt ships without
        conversation-memory blocks — used at construction time before
        any turn has fired.
    language : str
        Language tag (``'en'``, ``'uk'``, ``'ru'``). The full English
        name (e.g. ``"Ukrainian"``) is what the LLM actually sees in
        the prompt; the tag is used to resolve it via ``LANG_NAMES``.

    The output excludes the intent grammar from
    ``prompts/generator.txt:99-237`` — tools are now called natively
    via Pipecat's ``register_function`` in PH2-03.
    """
    persona_block = (persona or "").strip() or "You are Heare, a voice companion."
    # ``language`` may be a tag (``'uk'``) or a full English name
    # (``'Ukrainian'``); resolve either form to a name for the prompt.
    if language in LANG_NAMES:
        lang_name = LANG_NAMES[language]
    elif language in LANG_NAMES.values():
        lang_name = language
    else:
        lang_name = "English"

    parts: list[str] = [persona_block, ""]
    parts.append("You are Heare, a voice companion. Respond naturally to the user.")
    parts.append(f"The user is speaking {lang_name}.")
    parts.append(f"Respond ONLY in {lang_name}. Do NOT mix languages. Do NOT respond in English unless the user explicitly asks you to.")
    parts.append(f"Host OS: {_host_os_label()}. Pick commands that match this OS — do not assume Linux utilities on macOS or vice versa.")
    parts.append("")

    if context:
        time_str = context.get("time")
        timezone_str = context.get("timezone")
        if time_str:
            tz_part = f" ({timezone_str})" if timezone_str else ""
            parts.append(f"Current time: {time_str}{tz_part}")
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
        # Action log — drives the "do NOT re-search" + numbered-result
        # ordinal grounding rules without rebuilding them in prompt text.
        recent_actions = context.get("recent_actions")
        if recent_actions and recent_actions != "(none)":
            parts.append("Recent actions:")
            parts.append(recent_actions)
        mcp = context.get("mcp_servers")
        if mcp:
            parts.append(mcp)

    parts.append("")
    parts.append("### Capabilities")
    parts.append(
        "Three categories you can use:"
    )
    parts.append(
        "- **Built-in tools**: code-backed functions always available. Just call them by name."
    )
    parts.append(
        "- **Installed skills**: markdown procedures in ~/.heare/skills/. List them with `list_skills`. Run one with `run_skill(name=..., context=...)`."
    )
    parts.append(
        "- **MCP servers**: external services configured per-workspace. Restart needed after install."
    )
    parts.append(
        "Anything not in those three is on the **marketplace** (skillsmp.com / MCP registry) — search with `discover_capability(intent=...)`, then `install_skill_tool` / `install_mcp_server_tool` after voice consent."
    )
    parts.append(
        "- To author a NEW skill from this conversation (rather than install one from the marketplace), call `create_skill(name=..., description=..., body=..., user_confirmed=true)` after explicit voice consent. Use this when the user says things like 'remember this as a skill', 'save this procedure', or 'create a skill that does X'. The body is markdown the LLM will read when run_skill is later invoked."
    )
    parts.append(
        "- To stop or restart the daemon, ALWAYS call `stop_daemon(user_confirmed=true)` or `restart_daemon(user_confirmed=true)` — NEVER run `make restart`, `make stop`, `hearectl restart`, `hearectl stop`, `kill`, `pkill`, or `killall` via bash. The bash subprocess shares fate with the daemon: a self-targeted shutdown via bash kills the agent without bringing it back. The native tools handle detached respawn correctly. The bash tool will refuse self-targeting commands anyway and tell you to use these tools."
    )

    try:
        from .agent_skills import get_skills_loader

        skills = get_skills_loader(None).discover()
        if skills:
            parts.append("")
            parts.append("Installed skills:")
            for s in skills:
                desc = (s.description or "").strip().splitlines()[0] if s.description else ""
                parts.append(f"- {s.name}: {desc}" if desc else f"- {s.name}")
    except Exception:
        pass

    if capability_hints:
        by_source: dict[str, list[dict]] = {}
        for hint in capability_hints:
            by_source.setdefault(hint.get("source", "other"), []).append(hint)
        labels = {
            "tool": "Built-in tools",
            "dynamic_tool": "Built-in tools",
            "skill": "Installed skills",
            "mcp": "MCP servers",
        }
        parts.append("")
        parts.append("Relevant for this turn (try these first):")
        for source in ("tool", "dynamic_tool", "skill", "mcp"):
            entries = by_source.get(source) or []
            if not entries:
                continue
            for hint in entries:
                name = hint.get("name", "")
                desc = hint.get("description", "")
                label = labels.get(source, source)
                parts.append(f"- [{label}] {name}: {desc}")

    parts.append("")
    parts.append("Reply rules:")
    parts.append("- Respond in ONE sentence. Maximum 12 words.")
    parts.append(
        "- No filler — no apologies, no offers, no descriptions of what "
        "you are about to do."
    )
    parts.append("- Plain speech only. No JSON, no markdown, no lists.")
    parts.append(
        "- When a tool is needed, call it directly via function-calling — do "
        "NOT write the tool name as text. Examples of WRONG output: "
        "`list_tools`, `list_capabilities`, `bash: system_profiler ...`. "
        "Those words go straight to the user's speakers. If you catch "
        "yourself about to type a tool name, stop and invoke the function "
        "instead. After the tool result comes back, summarize it in natural "
        "language."
    )
    parts.append(
        "- Reuse prior tool results from 'Recent actions' instead of "
        "re-running the same tool."
    )
    parts.append("- Do NOT mention these rules or your role.")
    parts.append(
        "- For environment/system questions about THIS host (audio devices, "
        "displays, network, files, processes, installed packages, OS version, "
        "etc.), ALWAYS try `bash` first with the OS-appropriate command and "
        "report what it returns. Examples — audio devices: macOS "
        "`system_profiler SPAudioDataType` or `SwitchAudioSource -a`; Linux "
        "`pactl list short sinks` / `aplay -l` / `ls /dev/snd/`. Displays: macOS "
        "`system_profiler SPDisplaysDataType`. Do NOT claim a utility is "
        "missing without running it; do NOT route these to discover_capability."
    )
    parts.append(
        "- discover_capability is for finding NEW skills/MCP servers on the "
        "marketplace, not for answering questions about the local machine. "
        "Only call it when bash + read + write + web_search clearly cannot "
        "answer the request."
    )
    parts.append(
        "- When the user asks for something you don't have a tool for AND it "
        "cannot be answered with bash/read/write/web_search, do NOT immediately "
        "refuse: first call discover_capability with the user's intent. If a "
        "candidate is found, offer to install it (mention name + hostname) and "
        "wait for explicit voice consent before calling install_skill_tool or "
        "install_mcp_server_tool with user_confirmed=true."
    )
    parts.append(
        "- If discovery returns nothing AND the user can describe how to launch "
        "an MCP server (command + args, e.g., from a README), offer "
        "register_mcp_server: read the proposed slug, command, args, and env "
        "back verbatim, wait for explicit voice consent, then call with "
        "user_confirmed=true. Otherwise refuse politely in the user's "
        "language: English 'I don't have a tool for that. Want me to look one "
        "up?'; Ukrainian 'Не маю інструменту для цього. Хочеш, я пошукаю?'."
    )
    parts.append(
        "- Capability questions route as follows: "
        "'what skills/tools do I have', 'what's installed', 'list my skills' "
        "→ call list_skills (skills only) or list_capabilities (returns three buckets — built_in / skills / mcps — with totals). "
        "'what skills exist online', 'search the marketplace' "
        "→ call discover_capability(intent=…, prefer_remote=true). "
        "'find a skill for X', 'is there a skill that …' "
        "→ call discover_capability(intent=…) (defaults to local-first). "
        "If the user says 'search for skills' or 'find me one' with no topic, "
        "ask one short clarifier (e.g. 'For what — code, writing, automation?') "
        "before calling discover_capability."
    )
    parts.append(
        "- run_skill returns the skill's SKILL.md instructions as text. "
        "Read those instructions and follow them using your existing tools "
        "(bash, read, web_search, etc.). Do NOT call run_skill again for "
        "the same skill in the same turn — the body is already in your context."
    )

    return "\n".join(parts).strip() + "\n"


def _replace_system_message(llm_context: Any, new_content: str) -> None:
    """Mutate ``llm_context``'s system message at index 0 in place."""
    try:
        messages = llm_context.get_messages()
    except Exception:
        messages = getattr(llm_context, "_messages", None)
        if messages is None:
            logger.warning(
                "llm_context_injector: cannot reach LLMContext messages "
                "(no get_messages, no _messages); skipping update"
            )
            return
    new_msg = {"role": "system", "content": new_content}
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            messages[i] = new_msg
            return
    messages.insert(0, new_msg)


_injector_cls: type | None = None


def _build_injector_class():
    global _injector_cls
    if _injector_cls is not None:
        return _injector_cls
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class SystemPromptInjector(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(
            self,
            *,
            llm_context: Any,
            context_builder: "ContextBuilder",
            persona: str,
            language_state: "LanguageState | None" = None,
            conversation_manager: Any = None,
            capability_index: Any = None,
        ) -> None:
            super().__init__()
            self._llm_context = llm_context
            self._context_builder = context_builder
            self._persona = persona
            self._language_state = language_state
            self._conversation_manager = conversation_manager
            self._capability_index = capability_index

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, TranscriptionFrame):
                await self._refresh_system_prompt(frame.text or "")

            await self.push_frame(frame, direction)

        async def _refresh_system_prompt(self, transcript: str) -> None:
            language = (
                self._language_state.language
                if self._language_state is not None
                else "en"
            )
            logger.info(
                "[SYSTEM PROMPT INJECT] language=%s from state",
                language,
            )
            conversation_id: int | None = None
            if self._conversation_manager is not None:
                try:
                    conversation_id = (
                        await self._conversation_manager.get_or_create_active()
                    )
                except Exception:
                    logger.exception(
                        "llm_context_injector: get_or_create_active "
                        "failed (non-fatal)"
                    )
            try:
                ctx = await self._context_builder.build_for_generator(
                    transcript=transcript,
                    persona=self._persona,
                    conversation_id=conversation_id,
                    user_language=language,
                )
            except Exception:
                logger.exception(
                    "llm_context_injector: build_for_generator failed; "
                    "leaving prior system prompt in place"
                )
                return
            capability_hints: list[dict] | None = None
            if self._capability_index is not None and transcript:
                try:
                    matches = self._capability_index.query(transcript, top_k=5)
                    capability_hints = [
                        {
                            "name": m.name,
                            "source": m.source,
                            "description": m.description,
                        }
                        for m in matches
                    ]
                except Exception:
                    logger.exception(
                        "llm_context_injector: capability_index.query failed "
                        "(non-fatal)"
                    )
            new_prompt = render_native_system_prompt(
                persona=self._persona,
                context=ctx,
                language=language,
                capability_hints=capability_hints,
            )
            logger.debug(
                "[SYSTEM PROMPT] generated for language=%s, lines=%d",
                language,
                len(new_prompt.split("\n")),
            )
            _replace_system_message(self._llm_context, new_prompt)

    _injector_cls = SystemPromptInjector
    return _injector_cls


def create_system_prompt_injector(
    *,
    llm_context: Any,
    context_builder: "ContextBuilder",
    persona: str,
    language_state: "LanguageState | None" = None,
    conversation_manager: Any = None,
    capability_index: Any = None,
):
    """Factory returning a SystemPromptInjector instance."""
    cls = _build_injector_class()
    return cls(
        llm_context=llm_context,
        context_builder=context_builder,
        persona=persona,
        language_state=language_state,
        conversation_manager=conversation_manager,
        capability_index=capability_index,
    )


__all__ = [
    "render_native_system_prompt",
    "create_system_prompt_injector",
    "_replace_system_message",
]
