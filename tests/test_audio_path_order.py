"""The order of the audio stages, and why each position matters.

Every assertion here encodes a failure that was observed live and cost an
afternoon to find. Positions in this chain are not stylistic — three of
them decide whether the user can interrupt at all.

See docs/findings/echo-cancellation.md.
"""

from __future__ import annotations

from src.config import Settings
from src.pipeline.build import _assemble_native_stages


def _stages(**extra) -> list:
    return _assemble_native_stages(
        transport_input="INPUT",
        transport_output="OUTPUT",
        stt="STT",
        stt_error_observer="STT_ERR",
        transcription_gate="GATE",
        user_aggregator="USER_AGG",
        llm_service="LLM",
        tts="TTS",
        assistant_aggregator="ASSIST_AGG",
        **extra,
    )


def test_aec_runs_before_the_correlation_gate() -> None:
    """The gate must judge the residual, not the raw microphone.

    Run on raw mic audio during playback, correlation measured 0.55-0.78
    against a 0.15 threshold and the gate dropped 100% of frames — so
    nothing said over the bot ever reached STT.
    """
    stages = _stages(aec_filter="AEC", echo_gate="GATE_ECHO", sidetone="SIDETONE")

    assert stages.index("AEC") < stages.index("GATE_ECHO")
    assert stages.index("GATE_ECHO") < stages.index("SIDETONE")
    assert stages.index("SIDETONE") < stages.index("STT")


def test_far_end_reference_is_tapped_after_the_output_transport() -> None:
    """Tapped upstream, the reference arrives at TTS generation speed.

    Measured that way: the queue ran 4.6 s deep mid-utterance and emptied
    between sentences, so the canceller was handed silence exactly while
    the speaker was still playing.
    """
    stages = _stages(far_collector="FAR", echo_collector="ECHO_COLLECTOR")

    assert stages.index("FAR") > stages.index("OUTPUT")
    assert stages.index("ECHO_COLLECTOR") < stages.index("OUTPUT")


def test_far_collector_precedes_the_assistant_aggregator() -> None:
    """It observes audio; it must not displace the end of the chain."""
    stages = _stages(far_collector="FAR")
    assert stages[-1] == "ASSIST_AGG"
    assert stages[-2] == "FAR"


def test_level_taps_straddle_the_canceller() -> None:
    """A tap either side is what makes a deleting stage visible at all."""
    stages = _stages(aec_filter="AEC", audio_probes=["P_RAW", "P_AEC"])

    assert stages.index("P_RAW") < stages.index("AEC") < stages.index("P_AEC")
    assert stages.index("P_AEC") < stages.index("STT")


def test_observation_hooks_straddle_the_speaking_path() -> None:
    """What makes the daemon testable without a microphone.

    LLMTextFrame is consumed by TTS, so the model's words have to be
    observed before it and the resulting audio after it. Without both,
    ``src/pipeline/harness.py`` cannot tell "the model said nothing" from
    "TTS produced nothing".
    """
    stages = _stages(post_llm_stages=["P_TEXT"], pre_output_stages=["P_AUDIO"])

    assert stages.index("LLM") < stages.index("P_TEXT") < stages.index("TTS")
    assert stages.index("TTS") < stages.index("P_AUDIO") < stages.index("OUTPUT")


def test_chain_is_unchanged_when_no_audio_stages_are_supplied() -> None:
    assert _stages() == [
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


# ── the settings that decide whether barge-in is possible ─────────────


def test_correlation_gate_is_off_by_default() -> None:
    """It is a microphone mute during playback, not a filter."""
    assert Settings().echo_gate_enabled is False


def test_echo_classifier_is_off_by_default() -> None:
    """It awaited an LLM call inside process_frame, and only while the
    user was interrupting — a network round trip in the one path that
    must never wait."""
    assert Settings().echo_classifier_enabled is False


def test_aec_is_on_and_points_at_the_measured_delay() -> None:
    settings = Settings()
    assert settings.aec_enabled is True
    assert settings.aec_stream_delay_ms == 120, (
        "measured ~125 ms speaker-to-mic; the old 30 ms was a guess and "
        "capped suppression at a noisy 10-20 dB"
    )


def test_the_speech_gate_sits_where_it_can_see_both() -> None:
    """It needs the audio to know how loud the segment was and the
    transcript to know what was claimed — so it goes after STT, and
    before anything acts on the words."""
    stages = _stages(speech_energy_gate="ENERGY")

    assert stages.index("STT") < stages.index("ENERGY")
    assert stages.index("ENERGY") < stages.index("GATE")
