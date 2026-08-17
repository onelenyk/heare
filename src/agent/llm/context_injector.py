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
    pass


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






_injector_cls: type | None = None








__all__ = [
    "render_native_system_prompt",
]
