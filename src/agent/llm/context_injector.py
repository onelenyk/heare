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

from src.agent.llm.prompt_sections import render_prompt


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
    ctx = dict(context or {})

    try:
        from src.skills.agent_skills import get_skills_loader

        skills = get_skills_loader(None).discover()
        if skills:
            ctx["installed_skills_list"] = list(skills)
    except Exception:
        pass

    return render_prompt(
        persona=persona,
        context=ctx if ctx else None,
        language=language,
        capability_hints=capability_hints,
    )


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
                )

            await self.push_frame(frame, direction)

        async def _refresh_system_prompt(
            self,
            transcript: str,
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
