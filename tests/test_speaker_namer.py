"""Unit tests for SpeakerNamer + OpenRouter predictor adapter."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.config import Settings
from src.voice.speaker.namer import (
    SpeakerNamer,
    TurnRecord,
    _parse_prediction_json,
    build_openrouter_predictor,
    maybe_build_namer,
)


# ---------- helpers ----------


def _settings(**overrides: Any) -> Settings:
    s = Settings()
    s.speaker_namer_enabled = True
    s.speaker_namer_min_turns = 3
    s.speaker_namer_debounce_seconds = 10.0
    s.speaker_namer_confidence_threshold = 0.8
    s.speaker_namer_confidence_hysteresis = 0.05
    s.speaker_namer_buffer_size = 5
    s.speaker_namer_queue_max = 4
    s.speaker_namer_timeout_seconds = 1.0
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _gallery() -> MagicMock:
    g = MagicMock()
    g.rename_speaker = MagicMock(return_value=True)
    return g


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def _rec(sid: str, text: str = "hi", ts: float = 0.0, turn_id: int | None = None) -> TurnRecord:
    return TurnRecord(speaker_id=sid, text=text, timestamp=ts, turn_id=turn_id)


# ---------- enqueue + buffer + trigger ----------


async def test_enqueue_owner_skipped() -> None:
    namer = SpeakerNamer(_gallery(), _settings(), AsyncMock(return_value=(None, 0.0)))
    namer.enqueue(_rec("owner", "I am owner"))
    assert namer._queue.qsize() == 0


async def test_enqueue_drops_on_full_queue(caplog) -> None:
    s = _settings(speaker_namer_queue_max=2)
    namer = SpeakerNamer(_gallery(), s, AsyncMock(return_value=(None, 0.0)))
    namer.enqueue(_rec("guest_01", "a"))
    namer.enqueue(_rec("guest_01", "b"))
    assert namer._queue.qsize() == 2
    # Third one should be dropped silently, not raise.
    namer.enqueue(_rec("guest_01", "c"))
    assert namer._queue.qsize() == 2


async def test_buffer_bounded_by_size() -> None:
    s = _settings(speaker_namer_buffer_size=3)
    namer = SpeakerNamer(_gallery(), s, AsyncMock(return_value=(None, 0.0)))
    for i in range(5):
        namer._ingest(_rec("guest_01", f"t{i}"))
    st = namer._state("guest_01")
    assert [txt for txt, _ in st.buffer] == ["t2", "t3", "t4"]


async def test_should_trigger_respects_min_turns() -> None:
    clock = _FakeClock()
    namer = SpeakerNamer(
        _gallery(), _settings(speaker_namer_min_turns=3), AsyncMock(), clock=clock
    )
    namer._ingest(_rec("guest_01", "a"))
    namer._ingest(_rec("guest_01", "b"))
    # Force debounce window clear
    namer._state("guest_01").last_check_at = -1000.0
    assert namer._should_trigger("guest_01") is False
    namer._ingest(_rec("guest_01", "c"))
    assert namer._should_trigger("guest_01") is True


async def test_should_trigger_respects_debounce() -> None:
    clock = _FakeClock()
    s = _settings(speaker_namer_min_turns=2, speaker_namer_debounce_seconds=10.0)
    namer = SpeakerNamer(_gallery(), s, AsyncMock(), clock=clock)
    namer._ingest(_rec("guest_01", "a"))
    namer._ingest(_rec("guest_01", "b"))
    # Immediately after a prior check at t=1000 → within debounce window.
    namer._state("guest_01").last_check_at = clock.now
    assert namer._should_trigger("guest_01") is False
    clock.advance(11.0)
    assert namer._should_trigger("guest_01") is True


async def test_should_trigger_skips_owner() -> None:
    namer = SpeakerNamer(_gallery(), _settings(), AsyncMock())
    namer._ingest(_rec("owner", "x"))  # (enqueue filters this; _ingest is internal)
    assert namer._should_trigger("owner") is False


# ---------- apply_prediction ----------


async def test_apply_prediction_first_assign() -> None:
    gal = _gallery()
    namer = SpeakerNamer(gal, _settings(), AsyncMock())
    assert namer._apply_prediction("guest_01", "Alice", 0.9) is True
    gal.rename_speaker.assert_called_once_with("guest_01", "Alice")
    assert namer._state("guest_01").current_confidence == 0.9


async def test_apply_prediction_higher_confidence_overwrites() -> None:
    gal = _gallery()
    namer = SpeakerNamer(gal, _settings(), AsyncMock())
    namer._apply_prediction("guest_01", "Alice", 0.82)
    namer._apply_prediction("guest_01", "Bob", 0.95)
    assert gal.rename_speaker.call_count == 2
    assert gal.rename_speaker.call_args_list[-1].args == ("guest_01", "Bob")


async def test_apply_prediction_lower_confidence_ignored() -> None:
    gal = _gallery()
    namer = SpeakerNamer(gal, _settings(), AsyncMock())
    namer._apply_prediction("guest_01", "Alice", 0.9)
    gal.rename_speaker.reset_mock()
    assert namer._apply_prediction("guest_01", "Bob", 0.82) is False
    gal.rename_speaker.assert_not_called()


async def test_apply_prediction_hysteresis_respected() -> None:
    gal = _gallery()
    s = _settings(speaker_namer_confidence_hysteresis=0.05)
    namer = SpeakerNamer(gal, s, AsyncMock())
    namer._apply_prediction("guest_01", "Alice", 0.85)
    gal.rename_speaker.reset_mock()
    # +0.04 is inside hysteresis — must NOT rename.
    assert namer._apply_prediction("guest_01", "Bob", 0.89) is False
    gal.rename_speaker.assert_not_called()
    # +0.06 crosses hysteresis — MUST rename.
    assert namer._apply_prediction("guest_01", "Bob", 0.91) is True


async def test_apply_prediction_threshold_respected() -> None:
    gal = _gallery()
    s = _settings(speaker_namer_confidence_threshold=0.8)
    namer = SpeakerNamer(gal, s, AsyncMock())
    assert namer._apply_prediction("guest_01", "Alice", 0.79) is False
    gal.rename_speaker.assert_not_called()


async def test_apply_prediction_owner_never_renamed() -> None:
    gal = _gallery()
    namer = SpeakerNamer(gal, _settings(), AsyncMock())
    assert namer._apply_prediction("owner", "Bob", 0.99) is False
    gal.rename_speaker.assert_not_called()


async def test_apply_prediction_empty_name_ignored() -> None:
    gal = _gallery()
    namer = SpeakerNamer(gal, _settings(), AsyncMock())
    assert namer._apply_prediction("guest_01", None, 0.99) is False
    assert namer._apply_prediction("guest_01", "", 0.99) is False
    gal.rename_speaker.assert_not_called()


async def test_apply_prediction_gallery_miss_returns_false() -> None:
    gal = _gallery()
    gal.rename_speaker.return_value = False
    namer = SpeakerNamer(gal, _settings(), AsyncMock())
    assert namer._apply_prediction("guest_99", "Zoe", 0.95) is False
    # current_confidence must NOT be bumped when rename failed.
    assert namer._state("guest_99").current_confidence == 0.0


# ---------- run loop ----------


async def test_run_triggers_predict_fn_after_min_turns() -> None:
    gal = _gallery()
    predict = AsyncMock(return_value=("Alice", 0.9))
    s = _settings(speaker_namer_min_turns=3, speaker_namer_debounce_seconds=0.0)
    clock = _FakeClock()
    namer = SpeakerNamer(gal, s, predict, clock=clock)

    namer.enqueue(_rec("guest_01", "hi"))
    namer.enqueue(_rec("guest_01", "my name is alice"))
    namer.enqueue(_rec("guest_01", "nice to meet you"))
    task = asyncio.create_task(namer.run())
    # Let the loop consume all three + run prediction.
    for _ in range(10):
        await asyncio.sleep(0)
        if predict.await_count >= 1:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert predict.await_count == 1
    args = predict.await_args.args
    assert args[0] == "guest_01"
    assert args[1] == ["hi", "my name is alice", "nice to meet you"]
    gal.rename_speaker.assert_called_once_with("guest_01", "Alice")


async def test_run_survives_predict_exception() -> None:
    gal = _gallery()
    predict = AsyncMock(side_effect=RuntimeError("boom"))
    s = _settings(speaker_namer_min_turns=2, speaker_namer_debounce_seconds=0.0)
    namer = SpeakerNamer(gal, s, predict)
    namer.enqueue(_rec("guest_01", "a"))
    namer.enqueue(_rec("guest_01", "b"))
    task = asyncio.create_task(namer.run())
    for _ in range(20):
        await asyncio.sleep(0)
        if predict.await_count >= 1:
            break
    # Loop must still be alive after the exception.
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gal.rename_speaker.assert_not_called()


async def test_run_survives_predict_timeout() -> None:
    gal = _gallery()

    async def _slow(*_a: Any, **_kw: Any) -> tuple[str | None, float]:
        await asyncio.sleep(5.0)
        return "Alice", 0.9

    s = _settings(
        speaker_namer_min_turns=2,
        speaker_namer_debounce_seconds=0.0,
        speaker_namer_timeout_seconds=0.05,
    )
    namer = SpeakerNamer(gal, s, _slow)
    namer.enqueue(_rec("guest_01", "a"))
    namer.enqueue(_rec("guest_01", "b"))
    task = asyncio.create_task(namer.run())
    # Give the timeout a chance to fire.
    await asyncio.sleep(0.15)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gal.rename_speaker.assert_not_called()


async def test_run_cancels_cleanly() -> None:
    namer = SpeakerNamer(_gallery(), _settings(), AsyncMock(return_value=(None, 0.0)))
    task = asyncio.create_task(namer.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_does_not_retrigger_without_new_turns() -> None:
    gal = _gallery()
    predict = AsyncMock(return_value=(None, 0.0))
    s = _settings(speaker_namer_min_turns=2, speaker_namer_debounce_seconds=0.0)
    namer = SpeakerNamer(gal, s, predict)
    namer.enqueue(_rec("guest_01", "a"))
    namer.enqueue(_rec("guest_01", "b"))
    task = asyncio.create_task(namer.run())
    for _ in range(20):
        await asyncio.sleep(0)
        if predict.await_count >= 1:
            break
    # Queue is now empty; loop is awaiting. No new enqueues -> no more calls.
    await asyncio.sleep(0.05)
    assert predict.await_count == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------- OpenRouter predictor adapter ----------


def _completion(content: str) -> bytes:
    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    return json.dumps(body).encode()


def _build_predict(handler):
    return build_openrouter_predictor(
        api_key="k",
        model="m",
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


async def test_predictor_valid_response() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_completion('{"name": "Alice", "confidence": 0.87}')
        )

    predict = _build_predict(handler)
    name, conf = await predict("guest_01", ["hi", "I'm Alice"])
    assert name == "Alice"
    assert conf == pytest.approx(0.87)


async def test_predictor_null_name() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_completion('{"name": null, "confidence": 0.3}')
        )

    predict = _build_predict(handler)
    name, conf = await predict("guest_01", ["hi"])
    assert name is None
    assert conf == 0.0


async def test_predictor_malformed_json() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_completion("not json at all"))

    predict = _build_predict(handler)
    assert await predict("guest_01", ["hi"]) == (None, 0.0)


async def test_predictor_http_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server oops")

    predict = _build_predict(handler)
    assert await predict("guest_01", ["hi"]) == (None, 0.0)


async def test_predictor_label_sanitization() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        # A newline (control char) should trip sanitize_label → (None, 0.0)
        return httpx.Response(
            200,
            content=_completion('{"name": "Alice\\nEvil", "confidence": 0.95}'),
        )

    predict = _build_predict(handler)
    assert await predict("guest_01", ["hi"]) == (None, 0.0)


async def test_predictor_confidence_clamped() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_completion('{"name": "Bob", "confidence": 2.5}')
        )

    predict = _build_predict(handler)
    name, conf = await predict("guest_01", ["hi"])
    assert name == "Bob"
    assert conf == 1.0


async def test_predictor_missing_field() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_completion('{"name": "Alice"}'))

    predict = _build_predict(handler)
    assert await predict("guest_01", ["hi"]) == (None, 0.0)


async def test_predictor_prose_wrapped_json() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_completion(
                'Based on the context, my answer is: '
                '{"name": "Charlie", "confidence": 0.82}'
                ". Done."
            ),
        )

    predict = _build_predict(handler)
    name, conf = await predict("guest_01", ["hi", "I'm Charlie"])
    assert name == "Charlie"
    assert conf == pytest.approx(0.82)


async def test_predictor_empty_utterances() -> None:
    # No HTTP call should happen; predictor short-circuits.
    def handler(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("predictor should not call OpenRouter for empty input")

    predict = _build_predict(handler)
    assert await predict("guest_01", []) == (None, 0.0)


# ---------- _parse_prediction_json direct tests ----------


def test_parse_prediction_null_confidence() -> None:
    assert _parse_prediction_json('{"name": "Alice"}') == (None, 0.0)


def test_parse_prediction_non_string_name() -> None:
    assert _parse_prediction_json('{"name": 123, "confidence": 0.9}') == (None, 0.0)


def test_parse_prediction_negative_confidence_clamped() -> None:
    name, conf = _parse_prediction_json('{"name": "Ann", "confidence": -0.4}')
    assert name == "Ann"
    assert conf == 0.0


def test_parse_prediction_unknown_token_rejected() -> None:
    assert _parse_prediction_json('{"name": "unknown", "confidence": 0.9}') == (
        None,
        0.0,
    )


# ---------- maybe_build_namer gating ----------


def test_maybe_build_namer_disabled_namer_flag() -> None:
    s = _settings(speaker_namer_enabled=False)
    s.speaker_id_enabled = True
    assert maybe_build_namer(s, MagicMock(), "sk-or-test") is None


def test_maybe_build_namer_disabled_speaker_id_flag() -> None:
    s = _settings(speaker_namer_enabled=True)
    s.speaker_id_enabled = False
    assert maybe_build_namer(s, MagicMock(), "sk-or-test") is None


def test_maybe_build_namer_missing_api_key() -> None:
    s = _settings(speaker_namer_enabled=True)
    s.speaker_id_enabled = True
    assert maybe_build_namer(s, MagicMock(), None) is None
    assert maybe_build_namer(s, MagicMock(), "") is None


def test_maybe_build_namer_missing_gallery() -> None:
    s = _settings(speaker_namer_enabled=True)
    s.speaker_id_enabled = True
    assert maybe_build_namer(s, None, "sk-or-test") is None


def test_maybe_build_namer_all_gates_pass() -> None:
    s = _settings(speaker_namer_enabled=True)
    s.speaker_id_enabled = True
    namer = maybe_build_namer(s, MagicMock(), "sk-or-test")
    assert isinstance(namer, SpeakerNamer)
