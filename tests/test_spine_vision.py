"""Looking at the screen.

The verb exists because the assistant was asked what was on the screen
and had no way to find out — see tests/test_spine_display_verbs.py for
the same failure one layer in, on its own panel. `read_display` reads the
app's panel; this reads the glass.

Nothing here touches the network or the real screen. What is asserted is
the shape around the call: that a refused permission is reported as a
refused permission rather than an empty desktop, that the screenshot file
does not outlive the call, that the look is written to the ledger, and
that a text-only conversation model can never be used as an eye.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from src.spine import vision


def _settings(**over):
    base = dict(
        vision_provider="openrouter",
        vision_model="google/gemini-3.1-flash-lite",
        openrouter_api_key="sk-or-test",
        deepseek_api_key="sk-ds-test",
        db_path=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def llm(self, **kw) -> None:
        self.calls.append(kw)


def _answer(text="На екрані таблиця з трьома потягами.", cost=0.000409, tokens=(1200, 40)):
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "cost": cost},
    }


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _fake_client(payload, status=200, capture=None):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            if capture is not None:
                capture["url"] = url
                capture["headers"] = headers
                capture["json"] = json
            return _FakeResponse(payload, status)

    return lambda *a, **k: _Client()


# ── which model is allowed to be the eye ──────────────────────────────


def test_the_eye_is_chosen_separately_from_the_voice() -> None:
    base, key, model = vision.resolve_vision(_settings())
    assert "openrouter" in base
    assert key == "sk-or-test"
    assert model == "google/gemini-3.1-flash-lite"


def test_no_key_for_the_eye_says_so_instead_of_falling_back() -> None:
    """`resolve_llm` falls back to any provider with a key, which is right
    for talking and catastrophic here: DeepSeek takes no images, so a
    fallback would answer confidently about a picture nobody looked at."""
    with pytest.raises(vision.VisionUnavailable) as e:
        vision.resolve_vision(_settings(openrouter_api_key=None))
    assert "ключ" in str(e.value)


def test_an_unknown_vision_provider_is_named_not_guessed() -> None:
    with pytest.raises(vision.VisionUnavailable):
        vision.resolve_vision(_settings(vision_provider="nonesuch"))


# ── the capture, and the permission that is easy to miss ──────────────


def test_a_blank_capture_is_reported_as_a_missing_permission(monkeypatch, tmp_path) -> None:
    """macOS does not fail a capture without Screen Recording — it
    succeeds and hands back a picture of the wallpaper. Size is the only
    signal available without pyobjc's Quartz, which is not installed."""
    shot = {"path": None}

    def fake_run(cmd, timeout):
        if "screencapture" in cmd[0]:
            shot["path"] = cmd[-1]
            with open(cmd[-1], "wb") as f:
                f.write(b"\xff\xd8tiny")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(vision, "_run", fake_run)
    with pytest.raises(vision.VisionUnavailable) as e:
        vision._capture_and_scale()
    assert "дозвол" in str(e.value)


def test_a_failed_capture_does_not_pretend_to_have_looked(monkeypatch) -> None:
    monkeypatch.setattr(
        vision, "_run", lambda cmd, timeout: SimpleNamespace(returncode=1, stderr=b"")
    )
    with pytest.raises(vision.VisionUnavailable):
        vision._capture_and_scale()


def test_the_screenshot_does_not_outlive_the_call(monkeypatch) -> None:
    """A picture of someone's desktop is not left in /tmp because it was
    convenient to put it there."""
    seen: list[str] = []

    def fake_run(cmd, timeout):
        if "screencapture" in cmd[0]:
            seen.append(cmd[-1])
            with open(cmd[-1], "wb") as f:
                f.write(b"\xff\xd8" + b"x" * 9000)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(vision, "_run", fake_run)
    data = vision._capture_and_scale()
    assert len(data) > vision.MIN_PLAUSIBLE_BYTES
    assert seen and not os.path.exists(seen[0])
    assert not os.path.exists(os.path.dirname(seen[0]))


# ── the look itself ───────────────────────────────────────────────────


def test_the_answer_comes_back_ready_to_be_spoken(monkeypatch) -> None:
    monkeypatch.setattr(vision, "_capture_and_scale", lambda: b"\xff\xd8" + b"x" * 9000)
    monkeypatch.setattr(vision.httpx, "AsyncClient", _fake_client(_answer()))
    said = asyncio.run(vision.look("що там", settings=_settings(), usage=_Recorder()))
    assert said == "На екрані таблиця з трьома потягами."


def test_the_image_is_sent_as_an_image_and_the_question_with_it(monkeypatch) -> None:
    cap: dict = {}
    monkeypatch.setattr(vision, "_capture_and_scale", lambda: b"\xff\xd8" + b"x" * 9000)
    monkeypatch.setattr(vision.httpx, "AsyncClient", _fake_client(_answer(), capture=cap))
    asyncio.run(vision.look("скільки місць?", settings=_settings(), usage=_Recorder()))

    content = cap["json"]["messages"][-1]["content"]
    kinds = [part["type"] for part in content]
    assert kinds == ["text", "image_url"]
    assert content[0]["text"] == "скільки місць?"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert cap["json"]["usage"] == {"include": True}


def test_no_question_still_looks(monkeypatch) -> None:
    cap: dict = {}
    monkeypatch.setattr(vision, "_capture_and_scale", lambda: b"\xff\xd8" + b"x" * 9000)
    monkeypatch.setattr(vision.httpx, "AsyncClient", _fake_client(_answer(), capture=cap))
    asyncio.run(vision.look("", settings=_settings(), usage=_Recorder()))
    assert cap["json"]["messages"][-1]["content"][0]["text"] == vision.DEFAULT_QUESTION


def test_a_refusal_from_the_model_is_not_an_answer(monkeypatch) -> None:
    monkeypatch.setattr(vision, "_capture_and_scale", lambda: b"\xff\xd8" + b"x" * 9000)
    monkeypatch.setattr(vision.httpx, "AsyncClient", _fake_client({"error": "nope"}, 402))
    with pytest.raises(vision.VisionUnavailable):
        asyncio.run(vision.look("що там", settings=_settings(), usage=_Recorder()))


def test_an_empty_answer_is_not_spoken_as_success(monkeypatch) -> None:
    monkeypatch.setattr(vision, "_capture_and_scale", lambda: b"\xff\xd8" + b"x" * 9000)
    monkeypatch.setattr(vision.httpx, "AsyncClient", _fake_client(_answer(text="  ")))
    with pytest.raises(vision.VisionUnavailable):
        asyncio.run(vision.look("що там", settings=_settings(), usage=_Recorder()))


# ── the ledger ────────────────────────────────────────────────────────


def test_the_look_is_counted_at_the_price_the_provider_reports(monkeypatch) -> None:
    """The single most expensive thing this assistant does per call. The
    last time something went uncounted the balance reached minus a cent
    before anybody noticed."""
    rec = _Recorder()
    monkeypatch.setattr(vision, "_capture_and_scale", lambda: b"\xff\xd8" + b"x" * 9000)
    monkeypatch.setattr(vision.httpx, "AsyncClient", _fake_client(_answer()))
    asyncio.run(vision.look("що там", settings=_settings(), usage=rec))

    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["provider"] == "openrouter"
    assert call["model"] == "google/gemini-3.1-flash-lite"
    assert call["input_tokens"] == 1200
    assert call["cost_usd"] == 0.000409


def test_a_ledger_failure_never_swallows_the_answer(monkeypatch) -> None:
    class _Broken:
        def llm(self, **kw):
            raise RuntimeError("db is gone")

    monkeypatch.setattr(vision, "_capture_and_scale", lambda: b"\xff\xd8" + b"x" * 9000)
    monkeypatch.setattr(vision.httpx, "AsyncClient", _fake_client(_answer()))
    said = asyncio.run(vision.look("що там", settings=_settings(), usage=_Broken()))
    assert said == "На екрані таблиця з трьома потягами."


# ── reachable through the hands ─────────────────────────────────────────


def test_the_hands_are_handed_the_eye() -> None:
    """The screen is part of the world, and the world is behind
    `delegate`: the hands carry `look_at_screen`, and the voice's one
    door names the screen so the model never answers «не бачу» first."""
    from src.agent.hands import Hands
    from src.config import Settings
    from src.spine.verbs import VERBS

    offered = {s["function"]["name"] for s in Hands(Settings())._tool_schemas()}
    assert "look_at_screen" in offered
    door = next(v for v in VERBS if v.name == "delegate")
    assert "screen" in door.description
