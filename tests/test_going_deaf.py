"""Going deaf mid-conversation, and the two things that must then happen.

Measured live, 9 September 2026. Five lines of one log:

    heard: 'Привіт, Дока!'
    say: 'Привіт, Назаре!'
    ||PaMacCore (AUHAL)|| Error on line 2523: err='-50'
    ERROR spine.hearing: deaf — stream_open=True silent_for=22s
    engine: judged not worth saying — я перестала чути 21 секунд тому…

CoreAudio killed the input stream while the assistant was speaking. The
watchdog in `hearing.py` caught it inside twenty seconds and did the one
thing it could — raise an intent at urgency 0.9. Two layers then failed
it in opposite directions:

* **Nothing tried to fix it.** No layer reopened the stream, so a fault
  that lasts twenty seconds lasted the rest of the session.
* **The veto refused to mention it.** The model was asked whether the
  remark was worth making and said no, so the person went on talking to a
  dead microphone while every surface — dashboard, menu bar, log — looked
  normal.

That is precisely the class `hearing.py`'s docstring says it exists to
close ("visible from the inside and invisible from the outside"),
reopened at the last step by the gate the report was routed through.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.spine import hearing
from src.spine.engine import UNVETOABLE


# ── the report is not the model's to refuse ───────────────────────────


def test_deafness_is_declared_unvetoable() -> None:
    assert "deaf" in UNVETOABLE


def test_the_bar_for_unvetoable_stays_high() -> None:
    """Not a general escape hatch. If this set grows, the engine's whole
    "the model may always refuse" property is being traded away, and that
    should be a decision someone makes on purpose."""
    assert UNVETOABLE == {"deaf"}


# ── the watchdog itself ───────────────────────────────────────────────


def test_a_stream_that_stopped_calling_back_is_deaf() -> None:
    h = hearing.Hearing(
        wired=True, stream_open=True, silent_for=hearing.SILENT_AFTER_S + 2, muted=False
    )
    assert h.deaf
    assert "перестала чути" in h.describe()


def test_a_stream_that_never_opened_is_deaf_and_says_so_differently() -> None:
    h = hearing.Hearing(wired=True, stream_open=False, silent_for=0.0, muted=False)
    assert h.deaf
    assert "не відкрився" in h.describe()


def test_a_mute_is_never_a_fault() -> None:
    """Silence you asked for is not a failure, and an assistant that
    announces its own mute is worse than one that says nothing."""
    h = hearing.Hearing(
        wired=True, stream_open=True, silent_for=10_000.0, muted=True
    )
    assert not h.deaf


def test_one_outage_is_one_remark() -> None:
    watch = hearing.EarWatch()
    deaf = hearing.Hearing(
        wired=True, stream_open=True, silent_for=100.0, muted=False
    )
    assert watch.feed(deaf, now=1000.0) is not None
    assert watch.feed(deaf, now=1001.0) is None
    assert watch.feed(deaf, now=2000.0) is None


def test_the_ear_coming_back_arms_the_next_report() -> None:
    watch = hearing.EarWatch()
    deaf = hearing.Hearing(wired=True, stream_open=True, silent_for=100.0, muted=False)
    ok = hearing.Hearing(wired=True, stream_open=True, silent_for=0.1, muted=False)
    assert watch.feed(deaf, now=1000.0) is not None
    assert watch.feed(ok, now=1010.0) is None
    assert watch.feed(deaf, now=2000.0) is not None


def test_reading_a_broken_ear_never_raises() -> None:
    """A watchdog that can raise is one more thing that can take the
    conversation down."""

    class _Exploding:
        input_open = True

        def silent_for(self):
            raise RuntimeError("the device is gone")

    h = hearing.read(_Exploding())
    assert not h.deaf  # unwired, so it cannot claim a fault it cannot see


# ── reopening the microphone ──────────────────────────────────────────


def _audio(fail: bool = False):
    """An AudioIO stand-in that records what was done to its streams."""
    from src.spine.audio_io import AudioIO

    a = AudioIO.__new__(AudioIO)
    a.input_rate = 16000
    a.output_rate = 24000
    a.frame_ms = 20
    a.input_device = None
    a.output_device = None
    a._loop = asyncio.new_event_loop()
    a._last_frame_ts = 0.0
    a.closed = []
    old = SimpleNamespace(
        stop=lambda: a.closed.append("stop"), close=lambda: a.closed.append("close")
    )
    a._input_stream = old
    return a


def test_the_dead_stream_is_closed_before_a_new_one_opens(monkeypatch) -> None:
    a = _audio()
    started: list[str] = []

    class _Stream:
        def __init__(self, **kw):
            self.kw = kw

        def start(self):
            started.append("start")

    monkeypatch.setattr(
        "sounddevice.RawInputStream", _Stream, raising=False
    )
    assert asyncio.run(a.restart_input()) is True
    assert a.closed == ["stop", "close"]
    assert started == ["start"]
    assert a._input_stream is not None


def test_the_clock_restarts_with_the_stream(monkeypatch) -> None:
    """Otherwise the fresh stream is instantly judged silent for as long
    as the dead one was, and the watchdog fires again at once."""
    a = _audio()
    a._last_frame_ts = 0.0

    class _Stream:
        def __init__(self, **kw): ...
        def start(self): ...

    monkeypatch.setattr("sounddevice.RawInputStream", _Stream, raising=False)
    asyncio.run(a.restart_input())
    assert a._last_frame_ts > 0.0


def test_a_device_that_will_not_reopen_says_so_instead_of_raising(
    monkeypatch,
) -> None:
    a = _audio()

    def _boom(**kw):
        raise OSError("device unavailable")

    monkeypatch.setattr("sounddevice.RawInputStream", _boom, raising=False)
    assert asyncio.run(a.restart_input()) is False
    assert a._input_stream is None


def test_a_stream_that_will_not_close_does_not_stop_the_reopen(
    monkeypatch,
) -> None:
    """The old stream is already broken; refusing to give up on it is how
    a recoverable outage becomes a permanent one."""
    a = _audio()
    a._input_stream = SimpleNamespace(
        stop=lambda: (_ for _ in ()).throw(RuntimeError("wedged")),
        close=lambda: None,
    )

    class _Stream:
        def __init__(self, **kw): ...
        def start(self): ...

    monkeypatch.setattr("sounddevice.RawInputStream", _Stream, raising=False)
    assert asyncio.run(a.restart_input()) is True


def test_the_speaker_is_left_alone(monkeypatch) -> None:
    """It is how the assistant says it has gone deaf. Closing a stream
    mid-utterance turns one fault into two."""
    a = _audio()
    speaker = SimpleNamespace(stop=lambda: pytest.fail("the speaker was touched"))
    a._output_stream = speaker

    class _Stream:
        def __init__(self, **kw): ...
        def start(self): ...

    monkeypatch.setattr("sounddevice.RawInputStream", _Stream, raising=False)
    asyncio.run(a.restart_input())
    assert a._output_stream is speaker
