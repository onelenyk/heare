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
from typing import TYPE_CHECKING, Any

from .language import LANG_NAMES

if TYPE_CHECKING:
    from .context import ContextBuilder
    from .language_state import LanguageState


logger = logging.getLogger("heare.llm_context_injector")


def render_native_system_prompt(
    *,
    persona: str,
    context: dict[str, Any] | None,
    language: str,
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
    parts.append(f"**IMPORTANT: The user is speaking {lang_name}. You MUST respond ONLY in {lang_name}. Do NOT mix languages. Do NOT respond in English.**")
    parts.append(f"Every word of your response must be in {lang_name}. No English words unless they are proper nouns.")
    parts.append("If you don't know how to say something in that language, use a similar word or phrase in that language.")
    parts.append("")

    if context:
        # Time + transcripts block.
        time_str = context.get("time")
        timezone_str = context.get("timezone")
        if time_str:
            tz_part = f" ({timezone_str})" if timezone_str else ""
            parts.append(f"Current time: {time_str}{tz_part}")
        recent = context.get("recent_transcripts")
        if recent and recent != "(none)":
            parts.append("Recent transcripts:")
            parts.append(recent)
        # Conversation memory block.
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
        # MCP descriptions — injected if available.
        mcp = context.get("mcp_servers")
        if mcp:
            parts.append(mcp)

    # Agent Skills — inject brief skill names if available
    try:
        from .agent_skills import get_skills_loader

        loader = get_skills_loader(None)  # Use default settings
        skills = loader.discover()
        if skills:
            skill_names = ", ".join([s.name for s in skills])
            parts.append("")
            parts.append("### Available Skills")
            parts.append(skill_names)
            parts.append("(Use `run_skill(name=..., context=...)` to execute. Call `list_skills` for descriptions.)")
    except Exception:
        pass  # Skills loading failed; continue without skill injection

    parts.append("")
    parts.append("Reply rules:")
    parts.append("- Respond in ONE sentence. Maximum 12 words.")
    parts.append(
        "- No filler — no apologies, no offers, no descriptions of what "
        "you are about to do."
    )
    parts.append("- Plain speech only. No JSON, no markdown, no lists.")
    parts.append(
        "- When a tool is needed, call it directly — do not narrate the call."
    )
    parts.append(
        "- Reuse prior tool results from 'Recent actions' instead of "
        "re-running the same tool."
    )
    parts.append("- Do NOT mention these rules or your role.")

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
        ) -> None:
            super().__init__()
            self._llm_context = llm_context
            self._context_builder = context_builder
            self._persona = persona
            self._language_state = language_state
            self._conversation_manager = conversation_manager

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
            new_prompt = render_native_system_prompt(
                persona=self._persona, context=ctx, language=language
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
):
    """Factory returning a SystemPromptInjector instance."""
    cls = _build_injector_class()
    return cls(
        llm_context=llm_context,
        context_builder=context_builder,
        persona=persona,
        language_state=language_state,
        conversation_manager=conversation_manager,
    )


__all__ = [
    "render_native_system_prompt",
    "create_system_prompt_injector",
    "_replace_system_message",
]
