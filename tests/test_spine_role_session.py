"""Tests for src/spine/role_session.py — the bounded role session that
turns a Role's exchanges into a saved artifact plus a spoken summary.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.spine.role_session import Artifact, RoleManager


def make_role(
    name: str = "мітинг",
    channel: str = "log",
    artifact: str = "Зроби підсумок наради з рішеннями та завданнями.",
    prompt: str = "Ти секретар наради.",
) -> Any:
    return SimpleNamespace(name=name, channel=channel, artifact=artifact, prompt=prompt)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeCompleter:
    """Records the messages it was called with; returns a canned response
    or raises, as configured by the test.
    """

    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[list[dict]] = []

    async def __call__(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


# 1) start/ack voice vs log wording differs
def test_start_ack_wording_differs_by_channel() -> None:
    mgr = RoleManager(clock=FakeClock())
    log_role = make_role(name="мітинг", channel="log")
    ack = mgr.start(log_role)
    assert "мітинг" in ack
    assert "Записую все" in ack
    mgr.cancel()

    voice_role = make_role(name="вчитель", channel="voice")
    ack2 = mgr.start(voice_role)
    assert "вчитель" in ack2
    assert "Записую все" not in ack2


# 2) second start refused, active unchanged
def test_second_start_is_refused_and_active_unchanged() -> None:
    mgr = RoleManager(clock=FakeClock())
    first = make_role(name="мітинг")
    second = make_role(name="вчитель")

    mgr.start(first)
    refusal = mgr.start(second)

    assert "мітинг" in refusal
    assert mgr.active is first


# 3) note_turn collects, ignores None
def test_note_turn_collects_and_ignores_none() -> None:
    mgr = RoleManager(clock=FakeClock())
    mgr.start(make_role())

    mgr.note_turn(1)
    mgr.note_turn(None)
    mgr.note_turn(2)

    assert mgr.session_turns == [1, 2]


# 4) finish with artifact: assert Artifact fields, active becomes None,
# request messages contain role.artifact text and rendered transcript
# lines in order.
async def test_finish_builds_artifact_from_one_llm_call() -> None:
    mgr = RoleManager(clock=FakeClock())
    role = make_role(artifact="Зроби підсумок наради з рішеннями та завданнями.")
    mgr.start(role)

    completer = FakeCompleter(response="MD...\n===SPOKEN===\nОзвучка.")
    exchanges = [
        {"user": "Почнімо нараду", "agent": "Добре, починаємо."},
        {"user": "Рішення номер один", "agent": None},
    ]

    result = await mgr.finish(exchanges=exchanges, complete=completer)

    assert isinstance(result, Artifact)
    assert result.full_md == "MD..."
    assert result.spoken == "Озвучка."
    assert mgr.active is None

    assert len(completer.calls) == 1
    messages = completer.calls[0]
    system_content = messages[0]["content"]
    user_content = messages[1]["content"]

    assert role.artifact in system_content

    idx_user1 = user_content.index("Користувач: Почнімо нараду")
    idx_agent1 = user_content.index("Асистент: Добре, починаємо.")
    idx_user2 = user_content.index("Користувач: Рішення номер один")
    assert idx_user1 < idx_agent1 < idx_user2
    # second exchange has agent=None, must not appear at all
    assert "Асистент: None" not in user_content


# 5) finish with empty artifact instruction -> None but session still closed
async def test_finish_with_empty_artifact_instruction_returns_none_but_closes() -> None:
    mgr = RoleManager(clock=FakeClock())
    role = make_role(artifact="")
    mgr.start(role)

    completer = FakeCompleter(response="unused")
    result = await mgr.finish(exchanges=[{"user": "hi", "agent": "hey"}], complete=completer)

    assert result is None
    assert mgr.active is None
    assert completer.calls == []  # no LLM call needed when there is nothing to build


# 6) marker missing -> fallback spoken
async def test_finish_missing_marker_uses_fallback_spoken() -> None:
    mgr = RoleManager(clock=FakeClock())
    mgr.start(make_role())

    completer = FakeCompleter(response="Just a plain response with no marker.")
    result = await mgr.finish(exchanges=[{"user": "hi", "agent": "hey"}], complete=completer)

    assert result is not None
    assert result.full_md == "Just a plain response with no marker."
    assert result.spoken == "Готово, підсумок збережено."
    assert mgr.active is None


# 7) complete raising -> transcript-dump artifact, spoken fallback, session closed
async def test_finish_llm_error_dumps_transcript_and_closes_session() -> None:
    mgr = RoleManager(clock=FakeClock())
    mgr.start(make_role())

    completer = FakeCompleter(error=RuntimeError("boom"))
    exchanges = [{"user": "Привіт", "agent": "Привіт!"}]
    result = await mgr.finish(exchanges=exchanges, complete=completer)

    assert result is not None
    assert "Користувач: Привіт" in result.full_md
    assert "Асистент: Привіт!" in result.full_md
    assert result.spoken == "Не вдалося зібрати підсумок, але запис збережено."
    assert mgr.active is None


# 8) cancel mid-session
def test_cancel_drops_active_session_without_artifact() -> None:
    mgr = RoleManager(clock=FakeClock())
    role = make_role(name="мітинг")
    mgr.start(role)
    mgr.note_turn(1)

    ack = mgr.cancel()

    assert ack is not None
    assert "мітинг" in ack
    assert mgr.active is None
    assert mgr.session_turns == []


# 9) finish with no active -> None
async def test_finish_with_no_active_role_returns_none() -> None:
    mgr = RoleManager(clock=FakeClock())
    completer = FakeCompleter(response="unused")

    result = await mgr.finish(exchanges=[], complete=completer)

    assert result is None
    assert completer.calls == []


# bonus: cancel with nothing active returns None
def test_cancel_with_nothing_active_returns_none() -> None:
    mgr = RoleManager(clock=FakeClock())
    assert mgr.cancel() is None


# bonus: minutes() is clock-based
def test_minutes_is_clock_based() -> None:
    clock = FakeClock(start=100.0)
    mgr = RoleManager(clock=clock)

    assert mgr.minutes() == 0

    mgr.start(make_role())
    clock.advance(150.0)  # 2.5 minutes

    assert mgr.minutes() == 2
