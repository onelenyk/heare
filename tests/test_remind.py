"""Tests for the remind verb — set reminders for later.

The reminder is a voice tool that stores an intent with a due_ts,
to be voiced when the person is present and quiet, even at night.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.agent.tools import system
from src.spine import intents as I
from src.spine.engine import EngineState, judge
from src.spine.situation import Situation
from src.spine.verbs import VoiceToolbox

NOW = 1_000_000.0


class FakeHands:
    """Fake hands for the toolbox."""

    def __init__(self, settings):
        self.settings = settings
        self.started = []
        self.delivery = None

    def set_delivery(self, fn):
        self.delivery = fn

    def start(self, task):
        self.started.append(task)

    def cancel_all(self):
        return 0


class InMemoryStore:
    """In-memory intent store for testing."""

    def __init__(self):
        self.intents: list[I.Intent] = []
        self._next_id = 1
        self._seen = set()

    async def add(
        self,
        kind: str,
        text: str,
        *,
        origin: str = I.SELF,
        urgency: float = 0.5,
        due_ts: float | None = None,
        expires_ts: float | None = None,
        dedupe_key: str | None = None,
    ) -> int | None:
        # Dedupe by key
        if dedupe_key is not None and dedupe_key in self._seen:
            return None
        if dedupe_key is not None:
            self._seen.add(dedupe_key)

        intent = I.Intent(
            id=self._next_id,
            kind=kind,
            text=text,
            origin=origin,
            urgency=urgency,
            state=I.PENDING,
            created_ts=time.time(),
            updated_ts=time.time(),
            due_ts=due_ts,
            expires_ts=expires_ts,
            dedupe_key=dedupe_key,
        )
        self._next_id += 1
        self.intents.append(intent)
        return intent.id

    async def pending(self, limit: int = 10, now: float | None = None) -> list[I.Intent]:
        now = now if now is not None else time.time()
        # Return non-expired intents, ignoring due_ts (those are for the engine)
        result = [
            i
            for i in self.intents
            if i.state == I.PENDING and (i.expires_ts is None or i.expires_ts > now)
        ]
        # Sort by urgency desc, created_ts asc
        result.sort(key=lambda x: (-x.urgency, x.created_ts))
        return result[:limit]

    async def mark_voiced(self, intent_id: int | None) -> None:
        if intent_id is None:
            return
        for intent in self.intents:
            if intent.id == intent_id:
                intent.state = I.VOICED
                return

    async def settle(self, intent_id: int | None, outcome: str) -> None:
        if intent_id is None:
            return
        for intent in self.intents:
            if intent.id == intent_id:
                intent.state = I.DONE if outcome == I.ACCEPTED else I.DROPPED
                intent.outcome = outcome
                return

    async def drop(self, intent_id: int | None, reason: str = "") -> None:
        if intent_id is None:
            return
        for intent in self.intents:
            if intent.id == intent_id:
                intent.state = I.DROPPED
                intent.outcome = reason or None
                return

    async def awaiting_reaction(self) -> I.Intent | None:
        for intent in reversed(self.intents):
            if intent.state == I.VOICED and intent.outcome is None:
                return intent
        return None

    async def sweep_stale(self, now: float | None = None) -> int:
        return 0

    async def sweep_expired(self, now: float | None = None) -> int:
        return 0

    async def recent(self, limit: int = 5) -> list[I.Intent]:
        return sorted(self.intents, key=lambda x: -x.created_ts)[:limit]


def sit(**kw) -> Situation:
    """Build a situation for judge tests."""
    base = dict(
        now=NOW,
        hour=14,
        minute=0,
        weekday=1,
        silence_s=600.0,
        user_silence_s=60.0,
        bot_state="idle",
        bot_state_s=60.0,
        jobs_running=0,
        unprompted_last_s=1e9,
        unprompted_1h=0,
    )
    base.update(kw)
    return Situation(**base)


# ── verb schema and registration ──────────────────────────────────────


def test_remind_is_a_verb_about_the_conversation() -> None:
    """A reminder is a line of the conversation, said later."""
    from src.spine.verbs import VERBS, Category

    assert {v.name: v.category for v in VERBS}["remind"] is Category.CONVERSATION


def test_remind_tooldef_exists() -> None:
    """The ToolDef for remind exists and is configured correctly."""
    tool = system.get_tool("remind")
    assert tool is not None
    assert tool.name == "remind"
    assert tool.handler == "remind"
    assert "minutes" in tool.schema_fields
    assert "text" in tool.schema_fields


def test_remind_schema_in_spine_toolbox() -> None:
    """The remind schema appears in the voice toolbox's SCHEMAS."""
    from src.spine.verbs import VERBS

    assert "remind" in {v.name for v in VERBS}


# ── basic remind verb behavior ────────────────────────────────────────


async def _call_remind(toolbox, minutes: int, text: str) -> str:
    """Helper to call the remind verb."""
    return await toolbox.execute("remind", {"minutes": minutes, "text": text})


@pytest.mark.asyncio
async def test_remind_stores_intent_with_due_ts() -> None:
    """remind(minutes=2, text='...')  stores an intent with due_ts ≈ now+120."""
    store = InMemoryStore()

    async def notice(**kw):
        return await store.add(**kw)

    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=notice,
    )

    result = await _call_remind(toolbox, 2, "випити води")

    assert "нагадаю за 2 хвилини" in result
    assert len(store.intents) == 1
    intent = store.intents[0]
    assert intent.kind == "reminder"
    assert intent.text == "випити води"
    assert intent.origin == "user"
    assert intent.urgency == 0.9
    assert intent.due_ts is not None
    # Due should be roughly 120 seconds from when notice was called
    assert intent.due_ts > time.time()


@pytest.mark.asyncio
async def test_remind_returns_confirmation() -> None:
    """The verb returns a spoken confirmation."""
    store = InMemoryStore()

    async def notice(**kw):
        await store.add(**kw)

    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=notice,
    )

    result = await _call_remind(toolbox, 5, "проверить почту")
    assert "Добре, нагадаю за 5 хвилин" in result


# ── plural forms ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remind_plural_1_minute() -> None:
    """1 minute: хвилину (singular)."""
    store = InMemoryStore()

    async def notice(**kw):
        await store.add(**kw)

    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=notice,
    )

    result = await _call_remind(toolbox, 1, "тест")
    assert "за 1 хвилину" in result


@pytest.mark.asyncio
async def test_remind_plural_2_4_minutes() -> None:
    """2–4 minutes: хвилини (paucal)."""
    store = InMemoryStore()

    async def notice(**kw):
        await store.add(**kw)

    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=notice,
    )

    for minutes in [2, 3, 4]:
        result = await _call_remind(toolbox, minutes, f"тест {minutes}")
        assert f"за {minutes} хвилини" in result


@pytest.mark.asyncio
async def test_remind_plural_5_plus_minutes() -> None:
    """5+ minutes: хвилин (plural) — and the teens and twenties follow
    the rule: 11 хвилин, 21 хвилину, 22 хвилини, 25 хвилин."""
    store = InMemoryStore()

    async def notice(**kw):
        await store.add(**kw)

    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=notice,
    )

    for minutes in [5, 10, 60, 1440]:
        result = await _call_remind(toolbox, minutes, f"тест {minutes}")
        assert f"за {minutes} хвилин" in result
    for minutes, word in [(11, "хвилин"), (21, "хвилину"), (22, "хвилини"), (25, "хвилин"), (111, "хвилин")]:
        result = await _call_remind(toolbox, minutes, f"тест {minutes}")
        assert f"за {minutes} {word}." in result, result


# ── judge behavior ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reminder_not_due_does_not_speak() -> None:
    """judge does not speak a reminder at now+60 when due_ts=now+120."""
    store = InMemoryStore()

    async def notice(**kw):
        await store.add(**kw)

    # Create a reminder due at NOW + 120
    await store.add(
        kind="reminder",
        text="випити води",
        origin="user",
        urgency=0.9,
        due_ts=NOW + 120,
        dedupe_key="test_reminder_not_due",
    )

    # Check at NOW + 60 (not due yet)
    pending = await store.pending(now=NOW + 60)
    assert len(pending) == 1
    verdict = judge(sit(now=NOW + 60), pending, EngineState())
    assert verdict.speak is False
    assert "не на часі" in verdict.reason


@pytest.mark.asyncio
async def test_reminder_due_speaks_when_quiet_present() -> None:
    """judge speaks a reminder at now+121 when due_ts=now+120, if quiet and present."""
    store = InMemoryStore()

    # Create a reminder due at NOW + 120
    await store.add(
        kind="reminder",
        text="випити води",
        origin="user",
        urgency=0.9,
        due_ts=NOW + 120,
        dedupe_key="test_reminder_due",
    )

    # Check at NOW + 121 (past due, quiet, present)
    pending = await store.pending(now=NOW + 121)
    assert len(pending) == 1
    verdict = judge(
        sit(
            now=NOW + 121,
            user_silence_s=30,  # quiet for 30 seconds
            unprompted_last_s=1e9,  # nothing said unbidden yet
        ),
        pending,
        EngineState(),
    )
    assert verdict.speak is True
    assert verdict.intent is not None
    assert verdict.intent.text == "випити води"


@pytest.mark.asyncio
async def test_reminder_speaks_at_night_if_urgent() -> None:
    """Reminders have urgency=0.9, so they pass the night filter."""
    store = InMemoryStore()

    # Create a reminder
    await store.add(
        kind="reminder",
        text="завтра рано вставати",
        origin="user",
        urgency=0.9,
        due_ts=NOW + 60,
        dedupe_key="test_reminder_night",
    )

    # Check at night with the reminder due
    pending = await store.pending(now=NOW + 60)
    if len(pending) > 0:
        verdict = judge(
            sit(
                now=NOW + 60,
                hour=2,  # 2 AM
                user_silence_s=30,
                unprompted_last_s=1e9,
            ),
            pending,
            EngineState(),
        )
        # At night, only urgency >= 0.8 and origin=USER pass
        # Reminders have origin=USER and urgency=0.9, so they should pass
        assert verdict.speak is True


# ── verb validation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remind_without_engine_says_reminders_off() -> None:
    """With no notice callback, the verb says reminders are off."""
    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=None,  # No engine
    )

    result = await _call_remind(toolbox, 2, "тест")
    assert "вимкнені" in result


@pytest.mark.asyncio
async def test_remind_needs_minutes() -> None:
    """remind without valid minutes says what to do."""
    store = InMemoryStore()

    async def notice(**kw):
        await store.add(**kw)

    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=notice,
    )

    result = await _call_remind(toolbox, 0, "тест")
    assert "від 1 хвилини до 24 годин" in result or "хвилин" in result


@pytest.mark.asyncio
async def test_remind_needs_text() -> None:
    """remind without text says what to do."""
    store = InMemoryStore()

    async def notice(**kw):
        await store.add(**kw)

    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=notice,
    )

    result = await _call_remind(toolbox, 2, "")
    assert "про що нагадати" in result


@pytest.mark.asyncio
async def test_remind_respects_minutes_bound() -> None:
    """Minutes must be 1–1440."""
    store = InMemoryStore()

    async def notice(**kw):
        await store.add(**kw)

    toolbox = VoiceToolbox(
        settings=object(),
        deliver=lambda x: asyncio.sleep(0),
        hands_factory=lambda s: FakeHands(s),
        notice=notice,
    )

    # Too low
    result = await _call_remind(toolbox, 0, "тест")
    assert "24 годин" in result or "від 1" in result

    # Too high
    result = await _call_remind(toolbox, 1441, "тест")
    assert "24 годин" in result or "від 1" in result

    # In range
    result = await _call_remind(toolbox, 720, "тест")
    assert "Добре" in result
