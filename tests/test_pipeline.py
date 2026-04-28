"""Tests for the Pipecat-native pipeline assembly.

Tests target the pure helpers (``_assemble_native_stages``,
``_build_system_prompt``, ``_wire_language_state``) so they run
without portaudio.
"""
from __future__ import annotations

from src.language_state import LanguageState
from src.pipeline import (
    _assemble_native_stages,
    _build_system_prompt,
    _wire_language_state,
)


class _FakeContext:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)

    def get_messages(self) -> list[dict]:
        return self._messages


def test_assemble_native_stages_minimal_order() -> None:
    stages = _assemble_native_stages(
        transport_input="INPUT",
        transport_output="OUTPUT",
        stt="STT",
        stt_error_observer="STT_ERR",
        transcription_gate="GATE",
        user_aggregator="USER_AGG",
        llm_service="LLM",
        tts="TTS",
        assistant_aggregator="ASSIST_AGG",
    )
    assert stages == [
        "INPUT",
        "STT",
        "STT_ERR",
        "GATE",
        "USER_AGG",
        "LLM",
        "TTS",
        "OUTPUT",
        "ASSIST_AGG",
    ]


def test_assemble_native_stages_with_speaker_chain() -> None:
    stages = _assemble_native_stages(
        transport_input="INPUT",
        transport_output="OUTPUT",
        stt="STT",
        stt_error_observer="STT_ERR",
        transcription_gate="GATE",
        user_aggregator="USER_AGG",
        llm_service="LLM",
        tts="TTS",
        assistant_aggregator="ASSIST_AGG",
        speaker_buffer="SPK_BUF",
        speaker_tagger="SPK_TAG",
    )
    assert stages == [
        "INPUT",
        "SPK_BUF",
        "STT",
        "STT_ERR",
        "SPK_TAG",
        "GATE",
        "USER_AGG",
        "LLM",
        "TTS",
        "OUTPUT",
        "ASSIST_AGG",
    ]


def test_assemble_native_stages_with_sound_cue() -> None:
    stages = _assemble_native_stages(
        transport_input="INPUT",
        transport_output="OUTPUT",
        stt="STT",
        stt_error_observer="STT_ERR",
        transcription_gate="GATE",
        user_aggregator="USER_AGG",
        llm_service="LLM",
        tts="TTS",
        assistant_aggregator="ASSIST_AGG",
        sound_cue_processor="SOUND",
    )
    assert stages == [
        "INPUT",
        "STT",
        "STT_ERR",
        "GATE",
        "USER_AGG",
        "LLM",
        "TTS",
        "SOUND",
        "OUTPUT",
        "ASSIST_AGG",
    ]


def test_assemble_native_stages_with_injector_and_tts_fade() -> None:
    stages = _assemble_native_stages(
        transport_input="INPUT",
        transport_output="OUTPUT",
        stt="STT",
        stt_error_observer="STT_ERR",
        transcription_gate="GATE",
        system_prompt_injector="INJECTOR",
        user_aggregator="USER_AGG",
        llm_service="LLM",
        tts="TTS",
        tts_fade_observer="TTS_FADE",
        assistant_aggregator="ASSIST_AGG",
    )
    assert stages == [
        "INPUT",
        "STT",
        "STT_ERR",
        "GATE",
        "INJECTOR",
        "USER_AGG",
        "LLM",
        "TTS",
        "TTS_FADE",
        "OUTPUT",
        "ASSIST_AGG",
    ]


def test_assemble_native_stages_no_generator_processor_marker() -> None:
    stages = _assemble_native_stages(
        transport_input="INPUT",
        transport_output="OUTPUT",
        stt="STT",
        stt_error_observer="STT_ERR",
        transcription_gate="GATE",
        user_aggregator="USER_AGG",
        llm_service="LLM",
        tts="TTS",
        assistant_aggregator="ASSIST_AGG",
    )
    assert all(stage != "GENERATOR" for stage in stages)
    assert "GATE" in stages
    assert "LLM" in stages


def test_build_system_prompt_includes_persona_and_language() -> None:
    prompt = _build_system_prompt(
        persona="I am Heare. I love quick answers.", language="Ukrainian"
    )
    assert "Heare" in prompt
    assert "Ukrainian" in prompt
    assert "12 words" in prompt


def test_build_system_prompt_handles_empty_persona() -> None:
    prompt = _build_system_prompt(persona="", language="English")
    assert "voice companion" in prompt
    assert "English" in prompt


def test_build_system_prompt_no_intent_grammar() -> None:
    prompt = _build_system_prompt(persona="", language="English")
    forbidden = ["<intent>", "</intent>", '"tool":', "intent grammar"]
    for token in forbidden:
        assert token not in prompt, (
            f"system prompt leaks intent grammar token {token!r}"
        )


def test_wire_language_state_rewrites_system_message_on_change() -> None:
    state = LanguageState(initial="en")
    ctx = _FakeContext(
        messages=[
            {"role": "system", "content": "stale"},
            {"role": "user", "content": "Hi"},
        ]
    )
    _wire_language_state(state, ctx, persona="I am Heare.")

    state.set_language("uk")

    msgs = ctx.get_messages()
    assert msgs[0]["role"] == "system"
    assert "uk" in msgs[0]["content"].lower() or "Ukrainian" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "Hi"}


def test_wire_language_state_inserts_when_no_system_message() -> None:
    state = LanguageState(initial="en")
    ctx = _FakeContext(messages=[{"role": "user", "content": "Hi"}])
    _wire_language_state(state, ctx, persona="I am Heare.")

    state.set_language("ru")

    msgs = ctx.get_messages()
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "Hi"}


def test_wire_language_state_no_op_on_same_language() -> None:
    state = LanguageState(initial="en")
    ctx = _FakeContext(
        messages=[{"role": "system", "content": "INITIAL"}]
    )
    _wire_language_state(state, ctx, persona="")

    state.set_language("en")
    assert ctx.get_messages()[0]["content"] == "INITIAL"


def test_pipeline_module_imports_and_exports() -> None:
    """Smoke test: module imports cleanly without portaudio."""
    from src import pipeline  # noqa: F401

    assert hasattr(pipeline, "build_pipeline")
    assert hasattr(pipeline, "_assemble_native_stages")
    assert hasattr(pipeline, "_build_system_prompt")
    assert hasattr(pipeline, "_wire_language_state")
