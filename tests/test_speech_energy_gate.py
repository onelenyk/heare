"""Do not answer things nobody said.

Every case here was observed live, in ninety seconds of a real session:
eight "Дякую." from silence, an "І серпу.", and a sentence of invented
Ukrainian. Each became a full turn — model call, synthesis, utterance —
and the user's actual questions waited behind them.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pipecat.frames.frames")

from pipecat.frames.frames import (  # noqa: E402
    InputAudioRawFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
)

from src.pipeline.stages.speech_energy_gate import (  # noqa: E402
    create_speech_energy_gate,
)

pytestmark = pytest.mark.asyncio

SAMPLE_RATE = 16000


def _audio(rms: float, seconds: float = 1.0) -> InputAudioRawFrame:
    n = int(SAMPLE_RATE * seconds)
    rng = np.random.default_rng(1)
    pcm = (rng.normal(0, rms, n)).astype(np.int16)
    return InputAudioRawFrame(
        audio=pcm.tobytes(), sample_rate=SAMPLE_RATE, num_channels=1
    )


def _gate():
    gate = create_speech_energy_gate(min_rms=180.0, min_seconds=0.30)
    passed: list = []

    async def capture(frame, direction):
        passed.append(frame)

    gate.push_frame = capture  # type: ignore[method-assign]
    return gate, passed


async def _turn(gate, *, rms: float, seconds: float, text: str) -> None:
    await gate.process_frame(UserStartedSpeakingFrame(), None)
    if seconds > 0:
        await gate.process_frame(_audio(rms, seconds), None)
    await gate.process_frame(
        TranscriptionFrame(text=text, user_id="u", timestamp="t"), None
    )


async def test_real_speech_passes() -> None:
    gate, passed = _gate()
    await _turn(gate, rms=1200, seconds=1.5, text="Привіт, ти мене чуєш?")
    assert [f for f in passed if isinstance(f, TranscriptionFrame)]


async def test_silence_transcribed_as_thanks_is_dropped() -> None:
    """The exact failure: eight of these in ninety seconds."""
    gate, passed = _gate()
    await _turn(gate, rms=30, seconds=1.0, text="Дякую.")
    assert not [f for f in passed if isinstance(f, TranscriptionFrame)]


async def test_quiet_nonsense_is_dropped_whatever_it_says() -> None:
    """"І серпу." and a sentence of invented Ukrainian both came from
    segments no louder than the room."""
    gate, passed = _gate()
    await _turn(gate, rms=40, seconds=2.0, text="Я вона хожу в мене, там, останніся")
    assert not [f for f in passed if isinstance(f, TranscriptionFrame)]


async def test_a_loud_thank_you_is_kept() -> None:
    """The user is allowed to thank it."""
    gate, passed = _gate()
    await _turn(gate, rms=1500, seconds=0.8, text="Дякую!")
    assert not [f for f in passed if isinstance(f, TranscriptionFrame)], (
        "short and filler is still a hallucination"
    )

    gate, passed = _gate()
    await _turn(gate, rms=1500, seconds=2.0, text="Дякую!")
    assert [f for f in passed if isinstance(f, TranscriptionFrame)], (
        "two seconds of loud speech is a person saying thank you"
    )


async def test_a_clipped_fragment_is_dropped() -> None:
    gate, passed = _gate()
    await _turn(gate, rms=2000, seconds=0.1, text="а")
    assert not [f for f in passed if isinstance(f, TranscriptionFrame)]


async def test_injected_text_is_never_judged_on_audio() -> None:
    """Delegated results and typed input arrive with no microphone audio
    behind them; measuring their loudness would drop every one."""
    gate, passed = _gate()
    await gate.process_frame(
        TranscriptionFrame(text="[результат роботи] готово", user_id="u", timestamp="t"),
        None,
    )
    assert [f for f in passed if isinstance(f, TranscriptionFrame)]


async def test_audio_and_other_frames_always_pass_through() -> None:
    """The gate judges transcripts; it must not disturb the audio path."""
    gate, passed = _gate()
    await gate.process_frame(UserStartedSpeakingFrame(), None)
    frame = _audio(30, 0.5)
    await gate.process_frame(frame, None)
    assert frame in passed
    assert any(isinstance(f, UserStartedSpeakingFrame) for f in passed)


async def test_each_turn_is_judged_on_its_own_audio() -> None:
    """A loud turn must not vouch for the silence that follows it."""
    gate, passed = _gate()
    await _turn(gate, rms=1500, seconds=1.5, text="Скажи щось цікаве")
    await _turn(gate, rms=25, seconds=1.0, text="Дякую.")

    kept = [f.text for f in passed if isinstance(f, TranscriptionFrame)]
    assert kept == ["Скажи щось цікаве"]
