"""Acoustic adversity battery for src/spine.

Regression tests for three historical defects, all found by ear on real
recordings rather than in a unit test:

  (a) a film playing in the room kept the old wake window open for four
      and a half minutes — because pipecat's wake strategy refreshed its
      timeout on ANY transcription, the room's noise as much as speech
      addressed to the assistant (see src/spine/wake.py, tests/test_wake_window.py).
  (b) Whisper hallucinating a caption on silence/noise
      («Дякую за перегляд!») — the reason src/spine/vad.py's `loud_ms`
      pre-STT gate exists: main.py refuses to transcribe an utterance
      whose genuinely loud content is under 240ms.
  (c) the assistant hearing itself — echo of its own TTS output feeding
      back through the microphone, which src/spine/aec.py's SpineAEC
      exists to cancel.

Two tiers:

  OFFLINE (no marker): synthetic audio only, runs anywhere, no network.
  LIVE (@pytest.mark.spine_live): uses real EdgeTTS to synthesise actual
      speech. skipif no ffmpeg / no network reachability to the EdgeTTS
      endpoint.
"""

from __future__ import annotations

import shutil
import socket
import struct
import subprocess

import numpy as np
import pytest

from src.spine.aec import SpineAEC
from src.spine.tts import synthesise
from src.spine.turn import TurnAssembler
from src.spine.vad import EnergyVAD, EventKind, SpeechEvent, loud_ms
from src.spine.wake import WakeGate

# -- shared constants / helpers ------------------------------------------

RATE = 16000
FRAME_MS = 20
FRAME_BYTES = (RATE * FRAME_MS // 1000) * 2  # 640 bytes = 320 int16 samples
SILENCE_FRAME = b"\x00" * FRAME_BYTES

# main.py's pre-STT loudness gate (src/spine/main.py: min_speech_ms = 240)
# — "shorter than a spoken word: don't pay Groq to hallucinate on it."
STT_GATE_MS = 240


def _tone_frame(amplitude: int, samples: int = 320) -> bytes:
    """One 20ms/320-sample frame of a full-scale alternating square wave.

    RMS of an alternating +A/-A wave is exactly A, which makes the dBFS
    of a given amplitude easy to reason about against EnergyVAD's default
    threshold_db=-38.0.
    """
    return b"".join(
        struct.pack("<h", amplitude if i % 2 == 0 else -amplitude)
        for i in range(samples)
    )


def _rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def _voice_like(
    duration_s: float,
    rate: int,
    freqs: tuple[int, ...],
    amp: float = 6000,
) -> np.ndarray:
    """A synthetic "assistant voice": the sum of a few sine tones.

    Rendered independently at whatever `rate` is asked for (not resampled
    from another rate) — the caller renders the same freqs/duration at
    16 kHz for the mic path and at 24 kHz for the far-end/push_far path,
    mirroring how a real TTS stream and its acoustic echo relate.
    """
    t = np.arange(int(duration_s * rate)) / rate
    signal = sum(np.sin(2 * np.pi * f * t) for f in freqs)
    signal = signal / len(freqs) * amp
    return signal.astype(np.int16)


def _feed_echo_through_aec(
    aec: SpineAEC,
    far_pcm: bytes,
    near_pcm: bytes,
    *,
    delay_ms: int = 100,
    atten: float = 0.5,
    push_chunk: int = 4096,
) -> list[SpeechEvent]:
    """Push `far_pcm` (whatever rate the far side is at) through push_far
    in realistic streaming-sized chunks, then run an attenuated + delayed
    copy of `near_pcm` (16 kHz) through aec.process() -> a fresh
    EnergyVAD. Returns the END events observed after cancellation.
    """
    for i in range(0, len(far_pcm), push_chunk):
        aec.push_far(far_pcm[i : i + push_chunk])

    delay_samples = int(delay_ms * RATE / 1000)
    near = np.frombuffer(near_pcm, dtype=np.int16)
    attenuated = (near.astype(np.float64) * atten).astype(np.int16)
    mic = np.concatenate([np.zeros(delay_samples, dtype=np.int16), attenuated])
    mic_bytes = mic.tobytes()

    vad = EnergyVAD()
    ends: list[SpeechEvent] = []
    for off in range(0, len(mic_bytes) - FRAME_BYTES + 1, FRAME_BYTES):
        cleaned = aec.process(mic_bytes[off : off + FRAME_BYTES])
        ev = vad.feed(cleaned)
        if ev is not None and ev.kind == EventKind.END:
            ends.append(ev)
    return ends


class FakeClock:
    """Injectable clock for WakeGate/TurnAssembler, advanced by hand."""

    def __init__(self, t: float = 0.0) -> None:
        self._t = t

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


def _resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """s16le mono PCM resampled via an ffmpeg subprocess (same pattern
    used live in the daemon's own selftalk-style probes)."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(src_rate), "-ac", "1", "-i", "pipe:0",
            "-f", "s16le", "-ar", str(dst_rate), "-ac", "1", "pipe:1",
        ],
        input=pcm,
        capture_output=True,
    )
    return proc.stdout


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _edge_tts_reachable() -> bool:
    """Best-effort reachability check for the EdgeTTS websocket endpoint
    (wss://speech.platform.bing.com/...), so LIVE tests skip cleanly in
    a sandboxed/offline CI run instead of hanging or erroring."""
    try:
        with socket.create_connection(("speech.platform.bing.com", 443), timeout=3):
            return True
    except OSError:
        return False


_live = pytest.mark.spine_live
_needs_live_deps = pytest.mark.skipif(
    not _ffmpeg_available(),
    reason="ffmpeg not on PATH — required to transcode EdgeTTS output",
)
_needs_network = pytest.mark.skipif(
    not _edge_tts_reachable(),
    reason="EdgeTTS endpoint (speech.platform.bing.com:443) unreachable",
)


# =========================================================================
# OFFLINE TIER
# =========================================================================


def test_noise_battery_never_reaches_stt() -> None:
    """A battery of synthetic non-speech sounds must never produce a VAD
    utterance loud enough to reach the STT gate (main.py's 240ms floor).

    Zero events is also a pass: several of these deliberately stay under
    EnergyVAD's onset threshold (start_ms=120 => 6 consecutive loud
    frames) and never even reach START — which is itself the point, one
    layer earlier than the loud_ms gate.
    """
    loud = _tone_frame(10000)  # well above threshold_db=-38 (~-10.3 dBFS)
    hum = _tone_frame(400)  # ~-38.27 dBFS: just under the -38 threshold

    battery: dict[str, list[bytes]] = {
        "single_click": [loud] * 2 + [SILENCE_FRAME] * 60,
        "door_slam": [loud] * 5 + [SILENCE_FRAME] * 60,
        "keyboard_ticks": ([loud] + [SILENCE_FRAME] * 3) * 25,  # 100 frames = 2s
        "steady_quiet_hum": [hum] * 150,  # 3s, always under threshold
    }

    for name, frames in battery.items():
        vad = EnergyVAD()
        end_count = 0
        for frame in frames:
            ev = vad.feed(frame)
            if ev is None or ev.kind != EventKind.END:
                continue
            end_count += 1
            measured = loud_ms(ev.utterance)
            assert measured < STT_GATE_MS, (
                f"{name}: END utterance had loud_ms={measured} >= "
                f"{STT_GATE_MS}; main.py's gate would NOT drop it"
            )
        # No assertion on end_count itself — zero is an explicit pass.


def test_wake_window_not_held_by_rejected_turns() -> None:
    """The 4.5-minute-film regression, at the gate level.

    Wake once with the phrase, let the window expire, then feed ten
    phrase-less "noise transcript" turns 30s apart — the same cadence a
    film's dialogue would produce. None may be accepted, and the gate
    must never report awake again once every one of them was rejected.
    """
    clock = FakeClock()
    gate = WakeGate(["дока"], window_s=45.0, required=True, clock=clock)

    assert gate.accepts("Дока, привіт, як справи?") is True
    assert gate.awake is True

    clock.advance(46.0)  # past the 45s window

    for i in range(10):
        clock.advance(30.0)
        accepted = gate.accepts(f"якийсь фоновий шум номер {i}, без фрази")
        assert accepted is False, f"noise turn {i} must be rejected"
        assert gate.awake is False, f"gate must not be awake after noise turn {i}"


def test_echo_of_own_voice_is_cancelled() -> None:
    """A synthetic assistant voice, echoed back into the mic (attenuated,
    delayed), must not survive SpineAEC + EnergyVAD as a real utterance.
    """
    aec = SpineAEC()
    if not aec.active:
        pytest.skip("pywebrtc_audio unavailable in this environment")

    freqs = (300, 450, 550)
    far24 = _voice_like(2.0, 24000, freqs)
    near16 = _voice_like(2.0, 16000, freqs)

    ends = _feed_echo_through_aec(
        aec, far24.tobytes(), near16.tobytes(), delay_ms=100, atten=0.5
    )

    for ev in ends:
        measured = loud_ms(ev.utterance)
        assert measured < STT_GATE_MS, (
            f"echoed utterance survived cancellation: loud_ms={measured}"
        )
    # An empty `ends` (no utterance at all) is the stronger outcome and
    # also a pass — see module docstring / task spec.


def test_barge_in_clears_far_end() -> None:
    """clear() must drop the queued far-end reference (so AEC3 does not
    spend the next seconds cancelling an echo that was never played —
    e.g. after the user barges in and cancelled TTS is discarded), and
    processing clean speech afterwards must be essentially unchanged.
    """
    aec = SpineAEC()
    if not aec.active:
        pytest.skip("pywebrtc_audio unavailable in this environment")

    far_pcm = _voice_like(2.0, 24000, (300, 450, 550)).tobytes()
    aec.push_far(far_pcm)
    assert aec._far.pending > 0, "sanity: push_far should have queued something"

    aec.clear()
    assert aec._far.pending == 0

    near = _voice_like(1.0, 16000, (300, 450, 550))
    near_bytes = near.tobytes()
    cleaned = bytearray()
    for off in range(0, len(near_bytes) - FRAME_BYTES + 1, FRAME_BYTES):
        cleaned.extend(aec.process(near_bytes[off : off + FRAME_BYTES]))

    raw = np.frombuffer(near_bytes[: len(cleaned)], dtype=np.int16)
    clean = np.frombuffer(bytes(cleaned), dtype=np.int16)
    raw_rms = _rms(raw)
    clean_rms = _rms(clean)
    assert abs(clean_rms - raw_rms) <= 0.2 * raw_rms, (
        f"clean_rms={clean_rms:.1f} vs raw_rms={raw_rms:.1f} diverged more "
        "than 20% after clearing the far-end reference"
    )


# =========================================================================
# LIVE TIER (@pytest.mark.spine_live) — real EdgeTTS, real ffmpeg
# =========================================================================

# Continuous "film dialogue": long Ukrainian/Russian sentences, two
# voices, no wake phrase anywhere in the text. ~65-70s once synthesised.
_FILM_LINES: list[tuple[str, str]] = [
    (
        "ru-RU-DmitryNeural",
        "Сегодня утром на набережной было холодно и пасмурно, но мы всё "
        "равно пошли гулять вдоль реки, разговаривая обо всём на свете, "
        "вспоминая старые времена и строя планы на будущее лето.",
    ),
    (
        "uk-UA-OstapNeural",
        "Я довго думав про те, що сталося того вечора, і досі не можу "
        "знайти пояснення цій дивній історії, яка змінила все моє "
        "уявлення про людей, яких я вважав найближчими друзями.",
    ),
    (
        "ru-RU-DmitryNeural",
        "Через час пришёл дождь, и мы спрятались под старым мостом, "
        "ожидая, пока стихнет буря, а вода в реке поднималась всё выше "
        "и выше, заливая прибрежные камни.",
    ),
    (
        "uk-UA-OstapNeural",
        "Наступного дня сонце знову світило, і здавалося, що вчорашній "
        "сум залишився далеко позаду, а попереду на нас чекала довга "
        "дорога додому через ліс і старі поля.",
    ),
    (
        "ru-RU-DmitryNeural",
        "Вечером в старом доме зажглись окна, и кто-то тихо играл на "
        "пианино знакомую мелодию, которую я слышал ещё в детстве, "
        "гуляя по этим же улицам вместе с отцом.",
    ),
    (
        "uk-UA-OstapNeural",
        "А коли настала ніч, у небі засяяли зорі, і ми довго сиділи на "
        "ґанку, слухаючи цвіркунів і згадуючи усіх, кого давно не "
        "бачили, але хто назавжди залишився в наших серцях.",
    ),
]


@_live
@_needs_live_deps
@_needs_network
async def test_film_audio_produces_zero_accepted_turns() -> None:
    """Real, continuous film dialogue: the VAD/loud_ms layer WOULD send
    every line to STT (it is real speech), but the wake gate must reject
    every one of them and never wake — transcription happens, acting on
    it doesn't. No Groq call is made: the film's known sentence text
    stands in for what STT would have produced, so the assertion is
    entirely at the gate/turn level.
    """
    for _, text in _FILM_LINES:
        assert "дока" not in text.lower(), "fixture must not contain the wake phrase"

    silence_gap_24k = b"\x00" * (24000 * 2 * 300 // 1000)  # 300ms of silence
    pcm24_all = b""
    for voice, text in _FILM_LINES:
        pcm = b""
        async for chunk in synthesise(text, voice=voice):
            pcm += chunk
        pcm24_all += pcm + silence_gap_24k

    total_s = len(pcm24_all) / 2 / 24000
    assert total_s > 30.0, f"film audio suspiciously short: {total_s:.1f}s"

    pcm16 = _resample(pcm24_all, 24000, 16000)
    assert pcm16, "ffmpeg resample produced no output"

    vad = EnergyVAD()
    utterances: list[bytes] = []
    for off in range(0, len(pcm16) - FRAME_BYTES + 1, FRAME_BYTES):
        ev = vad.feed(pcm16[off : off + FRAME_BYTES])
        if ev is not None and ev.kind == EventKind.END:
            utterances.append(ev.utterance)

    assert utterances, "real film speech should have produced VAD utterances"
    for u in utterances:
        measured = loud_ms(u)
        assert measured >= STT_GATE_MS, (
            f"real speech utterance had loud_ms={measured} < {STT_GATE_MS}; "
            "it would NOT have reached STT, which defeats the point of "
            "this scenario (transcription should happen; only acting on "
            "it should not)"
        )

    # Gate-level check: the film says no wake word, ever.
    clock = FakeClock()
    gate = WakeGate(["дока"], window_s=45.0, required=True, clock=clock)
    assembler = TurnAssembler(hold_s=1.0, clock=clock)
    for _, text in _FILM_LINES:
        assembler.speech_started()
        clock.advance(0.1)
        assembler.transcript(text)
        clock.advance(1.2)  # > hold_s: the turn closes
        turn = assembler.poll()
        assert turn == text
        accepted = gate.accepts(turn)
        assert accepted is False, f"film line accepted: {text!r}"
        assert gate.awake is False
        clock.advance(2.0)  # pause before the next line, like a film cut


@_live
@_needs_live_deps
@_needs_network
async def test_assistant_speech_feeding_back_does_not_wake_itself() -> None:
    """The self-wake scenario: the assistant's OWN reply contains the
    wake phrase. Looped back through the AEC echo path (attenuated,
    delayed, like the room's own acoustic echo), it must not survive as
    an utterance that could reach the gate.
    """
    aec = SpineAEC()
    if not aec.active:
        pytest.skip("pywebrtc_audio unavailable in this environment")

    text = "Дока слухає, чим допомогти?"
    assert "дока" in text.lower(), "sanity: fixture must contain the wake phrase"

    pcm24 = b""
    async for chunk in synthesise(text, voice="uk-UA-PolinaNeural"):
        pcm24 += chunk
    assert pcm24, "EdgeTTS produced no audio"

    pcm16 = _resample(pcm24, 24000, 16000)
    assert pcm16, "ffmpeg resample produced no output"

    ends = _feed_echo_through_aec(aec, pcm24, pcm16, delay_ms=100, atten=0.5)

    for ev in ends:
        measured = loud_ms(ev.utterance)
        assert measured < STT_GATE_MS, (
            f"the assistant's own echoed reply survived cancellation: "
            f"loud_ms={measured} — it could reach the gate and see its "
            "own wake phrase"
        )
    # An empty `ends` (no utterance at all) is the stronger outcome and
    # also a pass.
