"""Tests for llm_context_injector — per-turn system prompt rebuild (PH2-07)."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat.frames.frames")
from pipecat.frames.frames import TextFrame, TranscriptionFrame  # noqa: E402

from src.pipeline.language_state import LanguageState  # noqa: E402
from src.agent.llm.context_injector import (  # noqa: E402
    _replace_system_message,
    create_system_prompt_injector,
    render_native_system_prompt,
)


# ---------------------------------------------------------------------------
# render_native_system_prompt — pure function


def test_render_handles_language_tag() -> None:
    out = render_native_system_prompt(persona="", context=None, language="uk")
    assert "Ukrainian" in out
    assert "12 words" in out


def test_render_handles_full_language_name() -> None:
    out = render_native_system_prompt(
        persona="", context=None, language="Ukrainian"
    )
    assert "Ukrainian" in out


def test_render_includes_persona() -> None:
    out = render_native_system_prompt(
        persona="I am Heare. Quick replies only.",
        context=None,
        language="en",
    )
    assert "I am Heare. Quick replies only." in out


def test_render_falls_back_when_persona_empty() -> None:
    out = render_native_system_prompt(
        persona="", context=None, language="en"
    )
    assert "voice companion" in out


def test_render_no_intent_grammar() -> None:
    out = render_native_system_prompt(
        persona="", context=None, language="en"
    )
    forbidden = ["<intent>", "</intent>", '"tool":', '"args":']
    for token in forbidden:
        assert token not in out, f"intent grammar leaked: {token!r}"


def test_render_includes_context_blocks() -> None:
    ctx = {
        "time": "12:00:00",
        "timezone": "UTC",
        "recent_transcripts": "  - [11:59:00] hello",
        "conversation_summary": "User asked about chili.",
        "active_topics": "chili, vegan",
        "entities": "Reykjavik",
        "recent_turns": "  - hi / hello",
        "recent_actions": "  - [11:59:30] web_search 'chili recipe' (2m ago)",
        "mcp_servers": "MCP servers: linear, github",
    }
    out = render_native_system_prompt(
        persona="P", context=ctx, language="en"
    )
    assert "Current time: 12:00:00 (UTC)" in out
    assert "User asked about chili" in out
    assert "chili, vegan" in out
    assert "Reykjavik" in out
    assert "web_search 'chili recipe'" in out
    assert "MCP servers: linear, github" in out


def test_render_includes_host_os_line() -> None:
    out = render_native_system_prompt(persona="", context=None, language="en")
    assert "Host OS:" in out


def test_render_prefers_bash_for_environment_questions() -> None:
    """Environment/system questions must route to bash first, not
    discover_capability — fixes the 'audio devices' regression where the
    model claimed it had no tools instead of running a shell command."""
    out = render_native_system_prompt(persona="", context=None, language="en")
    # The bash routing rule must appear before the constraint that
    # restricts discover_capability, so the model reads the positive
    # routing first.
    bash_idx = out.find("bash with OS-appropriate command")
    discover_idx = out.find(
        "discover_capability is ONLY for finding new things to install"
    )
    assert bash_idx != -1, "bash routing rule missing"
    assert discover_idx != -1, "discover_capability constraint missing"
    assert bash_idx < discover_idx, (
        "bash routing must precede discover_capability constraint"
    )
    # Audio-device example should be present so the model has a concrete
    # template for the failing case.
    assert "audio" in out.lower()


def test_render_skips_empty_context_blocks() -> None:
    ctx = {
        "time": "12:00:00",
        "recent_transcripts": "(none)",
        "recent_actions": "(none)",
    }
    out = render_native_system_prompt(
        persona="", context=ctx, language="en"
    )
    assert "Recent transcripts:" not in out
    assert "Recent actions:" not in out


# ---------------------------------------------------------------------------
# _replace_system_message — in-place mutation


class _FakeContext:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)

    def get_messages(self) -> list[dict]:
        return self._messages


def test_replace_system_message_updates_existing() -> None:
    ctx = _FakeContext(
        messages=[
            {"role": "system", "content": "OLD"},
            {"role": "user", "content": "Hi"},
        ]
    )
    _replace_system_message(ctx, "NEW")
    assert ctx.get_messages()[0] == {"role": "system", "content": "NEW"}
    assert ctx.get_messages()[1] == {"role": "user", "content": "Hi"}


def test_replace_system_message_inserts_when_missing() -> None:
    ctx = _FakeContext(messages=[{"role": "user", "content": "Hi"}])
    _replace_system_message(ctx, "NEW")
    msgs = ctx.get_messages()
    assert msgs[0] == {"role": "system", "content": "NEW"}
    assert msgs[1] == {"role": "user", "content": "Hi"}


# ---------------------------------------------------------------------------
# SystemPromptInjector — frame interception + context refresh


class _FakeContextBuilder:
    def __init__(self, ctx: dict) -> None:
        self._ctx = ctx
        self.calls: list[dict[str, Any]] = []

    async def build_for_generator(
        self,
        transcript: str,
        persona: str,
        conversation_id: int | None = None,
        user_language: str = "en",
    ) -> dict:
        self.calls.append(
            {
                "transcript": transcript,
                "persona": persona,
                "conversation_id": conversation_id,
                "user_language": user_language,
            }
        )
        return self._ctx


def _make_transcription(text: str):
    try:
        return TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        return TranscriptionFrame(user_id="u", timestamp="t", text=text)


@pytest.mark.asyncio
async def test_injector_rebuilds_system_prompt_on_transcription() -> None:
    cb = _FakeContextBuilder(
        ctx={
            "time": "12:00:00",
            "conversation_summary": "User asked about weather.",
        }
    )
    state = LanguageState(initial="uk")
    llm_ctx = _FakeContext(
        messages=[{"role": "system", "content": "STALE"}]
    )
    injector = create_system_prompt_injector(
        llm_context=llm_ctx,
        context_builder=cb,
        persona="I am Heare.",
        language_state=state,
    )
    injector.push_frame = AsyncMock()  # type: ignore[method-assign]

    frame = _make_transcription("Як сьогодні погода?")
    await injector._refresh_system_prompt(frame.text)

    # Builder was called with the transcript + current language.
    assert len(cb.calls) == 1
    assert cb.calls[0]["transcript"] == "Як сьогодні погода?"
    assert cb.calls[0]["user_language"] == "uk"
    assert cb.calls[0]["persona"] == "I am Heare."

    # System message was rewritten with the new context.
    new_system = llm_ctx.get_messages()[0]["content"]
    assert "User asked about weather." in new_system
    assert "Ukrainian" in new_system
    assert "I am Heare." in new_system


@pytest.mark.asyncio
async def test_injector_uses_conversation_manager_for_id() -> None:
    cb = _FakeContextBuilder(ctx={"time": "12:00:00"})
    state = LanguageState(initial="en")
    llm_ctx = _FakeContext(
        messages=[{"role": "system", "content": "STALE"}]
    )

    class _CM:
        async def get_or_create_active(self) -> int:
            return 42

    injector = create_system_prompt_injector(
        llm_context=llm_ctx,
        context_builder=cb,
        persona="P",
        language_state=state,
        conversation_manager=_CM(),
    )
    injector.push_frame = AsyncMock()  # type: ignore[method-assign]

    await injector._refresh_system_prompt("hello")

    assert cb.calls[0]["conversation_id"] == 42


@pytest.mark.asyncio
async def test_injector_swallows_context_builder_failure() -> None:
    """A broken ContextBuilder must not break the LLM turn — keep the
    prior system prompt and continue."""

    class _BoomBuilder:
        async def build_for_generator(self, *args, **kwargs) -> dict:
            raise RuntimeError("context builder exploded")

    state = LanguageState(initial="en")
    llm_ctx = _FakeContext(
        messages=[{"role": "system", "content": "PRIOR"}]
    )
    injector = create_system_prompt_injector(
        llm_context=llm_ctx,
        context_builder=_BoomBuilder(),
        persona="P",
        language_state=state,
    )
    injector.push_frame = AsyncMock()  # type: ignore[method-assign]

    await injector._refresh_system_prompt("hello")  # must not raise

    # System message was NOT clobbered by the failed rebuild.
    assert llm_ctx.get_messages()[0]["content"] == "PRIOR"


# ---------------------------------------------------------------------------
# T7: render_native_system_prompt — output_routing ordering via prompt_sections
# ---------------------------------------------------------------------------


def test_render_native_output_routing_is_last_section() -> None:
    """output_routing_block content MUST appear after all other sections
    in the rendered system prompt."""
    ctx = {
        "time": "2026-01-01 12:00:00",
        "timezone": "UTC",
        "mode": "ambient",
        "mode_block": "Mode: ambient",
        "recent_transcripts": "(none)",
        "conversation_summary": "No history.",
        "active_topics": "",
        "entities": "",
        "recent_turns": "(none)",
        "recent_actions": "(none)",
        "mcp_servers": "None connected.",
        "output_routing_block": "OUTPUT_ROUTING_LAST_MARKER",
    }
    out = render_native_system_prompt(
        persona="Heare persona", context=ctx, language="en"
    )

    assert "OUTPUT_ROUTING_LAST_MARKER" in out

    output_idx = out.rfind("OUTPUT_ROUTING_LAST_MARKER")
    assert output_idx != -1

    tail = out[output_idx + len("OUTPUT_ROUTING_LAST_MARKER"):]
    other_markers = [
        "Reply rules:", "Routing \u2014 pick by symptom:",
        "Tool-use loop:", "Speech style:", "### Capabilities",
        "Narration during tool use:", "Heare persona",
    ]
    for marker in other_markers:
        assert marker not in tail, (
            f"'{marker}' found AFTER output_routing in native prompt"
        )


@pytest.mark.asyncio
async def test_injector_forwards_non_transcription_frames_unchanged() -> None:
    cb = _FakeContextBuilder(ctx={"time": "12:00:00"})
    llm_ctx = _FakeContext(
        messages=[{"role": "system", "content": "INITIAL"}]
    )
    injector = create_system_prompt_injector(
        llm_context=llm_ctx,
        context_builder=cb,
        persona="P",
    )
    pushed: list[Any] = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    injector.push_frame = capture  # type: ignore[assignment]

    other = TextFrame("not a transcription")
    await injector.process_frame(other, None)

    # Builder was NOT called (only TranscriptionFrame triggers refresh).
    assert cb.calls == []
    # Frame was forwarded through unchanged.
    assert pushed == [other]
    # System prompt is untouched.
    assert llm_ctx.get_messages()[0]["content"] == "INITIAL"
