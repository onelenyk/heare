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

from src.voice.language.core import LANG_NAMES


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
    from src.store.context import ContextBuilder
    from src.pipeline.language_state import LanguageState


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
        Rendered persona block (from src.agent.identity.render_persona). When
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
        project_dir = context.get("project_dir")
        workspace_dir = context.get("workspace_dir")
        if project_dir:
            parts.append(f"Project directory: {project_dir}")
        if workspace_dir:
            parts.append(f"Workspace directory (sandbox): {workspace_dir}")
        current_audio = context.get("current_audio_event")
        if current_audio:
            parts.append(
                f"Ambient sound right now: {current_audio}. You cannot "
                "hear directly — this detector tag is your ONLY sense of "
                "room audio. If the user asks whether you hear music or a "
                "sound, answer truthfully from this tag (yes, and name it)."
            )
        recent = context.get("recent_transcripts")
        if recent and recent != "(none)":
            parts.append("Recent transcripts:")
            parts.append(recent)
            if "[audio:" in recent:
                parts.append(
                    "A trailing [audio: Label score] on a line means that "
                    "sound was detected in the room while that turn was "
                    "transcribed — it may be ambient (Music, TV, Speech "
                    "from a device), not something the user said to you."
                )
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
        "- **MCP servers**: external services. Any listed under 'Connected MCP servers' above are registered and callable RIGHT NOW — use them directly. A restart is only needed to pick up a server you just installed, never to use one already connected."
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
        from src.skills.agent_skills import get_skills_loader

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
    parts.append("")
    parts.append("Response length — pick the tier that fits:")
    parts.append(
        "- Default (most replies): one sentence, ~12 words. Conversational."
    )
    parts.append(
        "- Expanded (up to 3 sentences, ~40 words): when the user asks "
        "'explain', 'why', 'how', 'tell me more', or the answer genuinely "
        "needs structure."
    )
    parts.append(
        "- List mode: when the user asks to enumerate. Speak as "
        "'first... second... third...' — never markdown bullets."
    )
    parts.append(
        "- Verbatim read-back: when calling install_skill_tool, "
        "install_mcp_server_tool, register_mcp_server, stop_daemon, or "
        "restart_daemon. Read slug / command / args / env back word-for-word "
        "and wait for explicit consent. Length cap does not apply."
    )
    parts.append(
        "- Tool-result summary: after a tool returns, compress to the user's "
        "actual question — do not dump raw output. If empty or error, say so "
        "in one short sentence."
    )
    parts.append("")
    parts.append("Speech style:")
    parts.append(
        "- Plain spoken language. No JSON, no markdown, no bullet characters, "
        "no code fences."
    )
    parts.append("- No apologies, no offers, no 'let me think...' filler.")
    parts.append(
        "- Progress narration is REQUIRED (see below) — it is not filler."
    )
    parts.append("- Do not mention these rules, your role, or the tool system.")
    parts.append(
        "- Respond in the user's language (including narration). Do not mix "
        "languages."
    )
    parts.append("")
    parts.append("Ambient audio:")
    parts.append(
        "- The 'Ambient sound right now' line and [audio: ...] tags are "
        "your only hearing. When the user asks if you hear music / a "
        "sound / what's playing, answer from that tag — say yes and name "
        "it when present, no when absent. Never claim you cannot hear."
    )
    parts.append(
        "- If the latest turn carries an audio tag AND the text is "
        "short, off-topic, or looks like a mis-transcription, treat it as "
        "low-confidence: it may be Music/TV bleed, not speech to you."
    )
    parts.append(
        "- In that case do NOT act on it or invent a reply — ask a brief "
        "'Sorry, did you say something?' in the user's language, or stay "
        "silent if nothing was plausibly addressed to you."
    )
    parts.append(
        "- A clear, on-topic request still stands even with an audio tag — "
        "the tag lowers confidence, it is not a hard mute."
    )
    parts.append("")
    parts.append("Tool-use loop:")
    parts.append(
        "- Call tools by function-calling — never write the tool name as "
        "text. A spoken tool name is a failure."
    )
    parts.append(
        "- When multiple independent reads are needed, issue them in parallel "
        "in the same response."
    )
    parts.append(
        "- When step B depends on step A's result, call A first, wait, then "
        "call B. Do not guess A's output."
    )
    parts.append(
        "- Stop calling tools once you have enough to answer. End the turn "
        "with the answer, not another call."
    )
    parts.append(
        "- Hard cap: at most 4 tool calls per user turn. If you still don't "
        "have the answer, ask the user a short clarifier instead of looping "
        "further."
    )
    parts.append(
        "- Reuse anything in Recent actions from this turn or the previous "
        "turn — same tool + same arguments means the result is already in "
        "context. Do not re-run."
    )
    parts.append("")
    parts.append("Narration during tool use:")
    parts.append(
        "- Single fast tool call (read, list_skills, list_capabilities, "
        "list_browser_tabs, bash on a quick command): no pre-call narration. "
        "Just call it, then answer."
    )
    parts.append(
        "- Multi-step sequence (2+ tool calls) OR any slow tool (web_search, "
        "web_fetch, discover_capability, navigate_browser, install_skill_tool, "
        "install_mcp_server_tool, register_mcp_server): speak a short signal "
        "BEFORE the first call so the user knows you are working. Max 8 "
        "words. Examples: 'Checking the page now.' / 'Looking that up.' / "
        "'One moment — searching.' Never speak the tool name."
    )
    parts.append(
        "- Between calls in a sequence: emit one short progress line that "
        "anchors the user to what you just learned and what you're doing "
        "next. Max 12 words. Example: 'Found three tabs, reading the active "
        "one now.' Skip if the next call is fast and obvious from the "
        "previous reply."
    )
    parts.append(
        "- Narration must inform, not describe intent. 'Searching the "
        "marketplace' is good. 'I'll help you with that' is forbidden."
    )
    parts.append("")
    parts.append("Routing — pick by symptom:")
    parts.append(
        "- Question about this host (audio, displays, files, processes, OS, "
        "packages): bash with OS-appropriate command. Examples — audio "
        "devices: macOS `system_profiler SPAudioDataType` or "
        "`SwitchAudioSource -a`; Linux `pactl list short sinks` / `aplay -l`. "
        "Displays: macOS `system_profiler SPDisplaysDataType`."
    )
    parts.append("- Read or modify a file in the workspace: read / write / edit.")
    parts.append("- Look something up on the web: web_search then web_fetch.")
    parts.append(
        "- 'What can you do / what's installed / list my skills': "
        "list_capabilities (all buckets) or list_skills (skills only)."
    )
    parts.append(
        "- 'Find me a skill / search marketplace / is there a skill for X': "
        "discover_capability(intent=...). For 'what exists online' add "
        "prefer_remote=true. If the user says 'find me one' with no topic, "
        "ask one short clarifier before calling."
    )
    parts.append(
        "- User describes an MCP launch from a README and discovery found "
        "nothing: register_mcp_server after verbatim read-back."
    )
    parts.append(
        "- User says 'remember this as a skill / save this procedure': "
        "create_skill after consent."
    )
    parts.append(
        "- User says 'stop / restart / quit / shut down' the bot: stop_daemon "
        "/ restart_daemon after consent. Never `bash kill`, `make restart`, "
        "or `hearectl stop`."
    )
    parts.append(
        "- Browser tab interaction: read_browser_page / click_in_browser / "
        "navigate_browser / fill_in_browser / extract_in_browser / "
        "open_browser_tab / activate_browser_tab."
    )
    parts.append("")
    parts.append(
        "discover_capability is ONLY for finding new things to install. Never "
        "use it to answer questions about the local machine — that is what "
        "bash is for."
    )
    parts.append("")
    parts.append(
        "If no tool fits and bash / read / web_search clearly cannot help: "
        "refuse politely in the user's language. English: 'I don't have a "
        "tool for that. Want me to look one up?' Ukrainian: 'Не маю "
        "інструменту для цього. Хочеш, я пошукаю?'"
    )
    parts.append("")
    parts.append("run_skill specifics:")
    parts.append(
        "- run_skill returns SKILL.md instructions as text. Read them and "
        "execute with your existing tools."
    )
    parts.append(
        "- Never call run_skill twice for the same skill in one turn — the "
        "body is already in your context."
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
                await self._refresh_system_prompt(
                    frame.text or "",
                    audio_event_label=getattr(
                        frame, "audio_event_label", None
                    ),
                    audio_event_score=getattr(
                        frame, "audio_event_score", None
                    ),
                )

            await self.push_frame(frame, direction)

        async def _refresh_system_prompt(
            self,
            transcript: str,
            audio_event_label: str | None = None,
            audio_event_score: float | None = None,
        ) -> None:
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
            if audio_event_label:
                if isinstance(audio_event_score, (int, float)):
                    ctx["current_audio_event"] = (
                        f"{audio_event_label} {float(audio_event_score):.2f}"
                    )
                else:
                    ctx["current_audio_event"] = str(audio_event_label)
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
