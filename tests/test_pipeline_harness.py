"""The text harness for the main pipeline.

Integration is not exercised here — a real run costs Groq, DeepSeek and
Edge calls. What is checked is the wiring that makes such a run possible
at all, since every one of these was got wrong on the first attempt.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from src.pipeline import harness
from src.pipeline.build import build_pipeline


def test_build_pipeline_can_leave_the_devices_shut() -> None:
    """``audio=False`` is what lets the daemon be driven from text."""
    params = inspect.signature(build_pipeline).parameters
    assert params["audio"].default is True
    assert "post_llm_stages" in params
    assert "pre_output_stages" in params


def test_env_loader_fills_missing_keys_without_overriding(tmp_path, monkeypatch) -> None:
    """The daemon is launched by the menubar app, which loads
    ~/.heare/.env first. Without this the harness dies inside the STT
    client with a message about OPENAI_API_KEY — a confusing way to say
    GROQ_API_KEY was never in the environment.
    """
    home = tmp_path / "heare-home"
    home.mkdir()
    (home / ".env").write_text(
        "# a comment\nGROQ_API_KEY=from-file\nDEEPSEEK_API_KEY='quoted'\n\nbroken\n"
    )
    monkeypatch.setenv("HEARE_HOME", str(home))
    monkeypatch.setenv("GROQ_API_KEY", "already-set")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    harness._load_env()

    import os

    assert os.environ["GROQ_API_KEY"] == "already-set", "must not override the shell"
    assert os.environ["DEEPSEEK_API_KEY"] == "quoted"


def test_probe_records_words_and_audio_without_altering_frames() -> None:
    """An observer that changes what passes through is a Heisenbug."""
    import asyncio

    from pipecat.frames.frames import LLMTextFrame, TTSAudioRawFrame

    turn = harness.Turn(prompt="p", started=0.0)
    turn_ref = {"turn": turn}
    probe = harness._make_probe(turn_ref)

    forwarded: list = []

    async def capture(frame, direction):
        forwarded.append(frame)

    probe.push_frame = capture  # type: ignore[method-assign]

    audio = np.zeros(160, dtype=np.int16).tobytes()

    async def drive():
        await probe.process_frame(LLMTextFrame(text="привіт"), None)
        await probe.process_frame(
            TTSAudioRawFrame(audio=audio, sample_rate=16000, num_channels=1), None
        )

    asyncio.run(drive())

    assert turn.text == "привіт"
    assert turn.audio_bytes == len(audio)
    assert turn.first_text_at is not None
    assert turn.first_audio_at is not None
    assert len(forwarded) == 2
    assert forwarded[1].audio == audio


def test_probe_is_idle_between_turns() -> None:
    """Audio that belongs to no turn must not be attributed to the last."""
    import asyncio

    from pipecat.frames.frames import LLMTextFrame

    turn_ref: dict = {"turn": None}
    probe = harness._make_probe(turn_ref)

    async def noop(frame, direction):
        return None

    probe.push_frame = noop  # type: ignore[method-assign]
    asyncio.run(probe.process_frame(LLMTextFrame(text="ignored"), None))  # no raise


@pytest.mark.parametrize(
    "gap_seconds, expected",
    [(0.1, 1), (harness.GAP + 0.5, 2)],
)
def test_a_pause_starts_a_new_utterance(gap_seconds: float, expected: int) -> None:
    """Two utterances with a silence between them is the shape delegated
    work produces: an acknowledgement now, the answer when it lands."""
    import asyncio
    import time

    from pipecat.frames.frames import TTSAudioRawFrame

    turn = harness.Turn(prompt="p", started=time.monotonic())
    probe = harness._make_probe({"turn": turn})

    async def noop(frame, direction):
        return None

    probe.push_frame = noop  # type: ignore[method-assign]
    frame = TTSAudioRawFrame(
        audio=np.zeros(160, dtype=np.int16).tobytes(),
        sample_rate=16000,
        num_channels=1,
    )

    async def drive():
        await probe.process_frame(frame, None)
        turn._last_audio -= gap_seconds  # simulate the pause
        await probe.process_frame(frame, None)

    asyncio.run(drive())
    assert len(turn.utterances) == expected
