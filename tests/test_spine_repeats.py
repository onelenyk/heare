"""Saying you mean to, over and over, and being told about it once.

This is the feature this project has already deleted once, so the cases
that matter here are the ones where it stays quiet. The model, asked
which intentions repeat, will always produce a list — that is what it is
for — and every threshold below exists to throw most of that list away
without asking it to be careful.

Three mentions in one afternoon is not a pattern. Twice is a
coincidence. What a colleague said, faithfully recorded in a summary, is
not something you keep meaning to do. And anything noticed once is never
noticed again, whatever words the next pass puts it in.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime

import pytest

from src.spine.repeats import (
    Candidate,
    ENOUGH_SUMMARIES,
    ObservationStore,
    Repeats,
    Summary,
    believable,
    detector,
    is_mine,
    key,
    parse,
    render,
    same_thing,
    sift,
)

DAY = 24 * 3600.0


def _at(day: str, hour: int = 12) -> float:
    """A timestamp on a named local day, so "different days" is a fact
    about the calendar and not about how many seconds apart two rows are.
    """
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").timestamp()


def _summaries(*rows: tuple[int, str, str]) -> list[Summary]:
    return [Summary(id=i, ts=_at(day), text=text) for i, day, text in rows]


THREE_DAYS = _summaries(
    (1, "2026-08-18", "Говорили про збірку. Сказав, що треба переписати збірку."),
    (2, "2026-08-19", "Знову про збірку: треба переписати збірку, руки не доходять."),
    (3, "2026-08-20", "Коротко про збірку — треба переписати збірку."),
)


# ── the threshold: when it stays quiet ────────────────────────────────


def test_three_mentions_on_one_day_is_silence() -> None:
    """A talkative afternoon about one thing is a mood, not a pattern."""
    one_day = _summaries(
        (1, "2026-08-20", "треба переписати збірку"),
        (2, "2026-08-20", "знову про збірку"),
        (3, "2026-08-20", "і ще раз про збірку"),
    )
    assert believable(Candidate("треба переписати збірку", (1, 2, 3)), one_day) is False


def test_twice_across_two_days_is_silence() -> None:
    """Twice is a coincidence. The third mention is what makes it a
    thing you keep meaning to do."""
    assert believable(Candidate("треба переписати збірку", (1, 2)), THREE_DAYS) is False


def test_a_wish_someone_else_expressed_is_silence() -> None:
    """A summary records what a colleague said as faithfully as what you
    said, and it clears every count-based threshold there is."""
    assert believable(
        Candidate("колезі треба оновити сертифікат", (1, 2, 3)), THREE_DAYS
    ) is False
    assert believable(
        Candidate("їй треба перенести дедлайн", (1, 2, 3)), THREE_DAYS
    ) is False
    assert believable(
        Candidate("він хоче переписати збірку", (1, 2, 3)), THREE_DAYS
    ) is False


def test_something_that_is_not_an_intention_at_all_is_silence() -> None:
    """"The build is broken" repeats just as reliably and is not
    something anyone said they would do."""
    assert believable(Candidate("збірка падає на тестах", (1, 2, 3)), THREE_DAYS) is False


def test_evidence_the_model_invented_does_not_count() -> None:
    """Asked which conversations something appeared in, a model that has
    already decided on an intention will supply numbers to go with it.
    Only ids that were in the prompt count."""
    assert believable(
        Candidate("треба переписати збірку", (1, 2, 3, 44, 45, 46)), THREE_DAYS[:1]
    ) is False


def test_the_same_conversation_cited_three_times_is_one_mention() -> None:
    assert believable(Candidate("треба переписати збірку", (2, 2, 2)), THREE_DAYS) is False


# ── the threshold: when it speaks ─────────────────────────────────────


def test_three_times_across_two_days_is_worth_a_word() -> None:
    two_days = _summaries(
        (1, "2026-08-19", "треба переписати збірку"),
        (2, "2026-08-19", "знову про збірку"),
        (3, "2026-08-20", "і ще раз"),
    )
    assert believable(Candidate("треба переписати збірку", (1, 2, 3)), two_days) is True


def test_the_three_words_the_plan_names_all_count() -> None:
    for phrasing in ("треба полагодити збірку", "хочу полагодити збірку",
                     "маю полагодити збірку"):
        assert is_mine(phrasing) is True, phrasing


def test_sift_keeps_only_what_survives() -> None:
    kept = sift(
        [
            Candidate("треба переписати збірку", (1, 2, 3)),
            Candidate("колезі треба оновити сертифікат", (1, 2, 3)),
            Candidate("хочу розібратись з тестами", (1,)),
        ],
        THREE_DAYS,
    )
    assert [c.text for c in kept] == ["треба переписати збірку"]


# ── reading what the model said ───────────────────────────────────────


def test_nothing_is_a_valid_and_expected_answer() -> None:
    assert parse("НІЧОГО") == []
    assert parse("") == []


def test_a_claim_with_no_evidence_is_not_a_claim() -> None:
    """A line without conversation numbers is a guess, and a guess is
    what the whole threshold exists to refuse."""
    assert parse("треба переписати збірку") == []


def test_bullets_and_stray_spacing_survive() -> None:
    assert parse("  - треба переписати збірку |  12, 15 , 19  \n\n") == [
        Candidate("треба переписати збірку", (12, 15, 19))
    ]


def test_a_conversation_cited_twice_on_one_line_counts_once() -> None:
    assert parse("треба | 12, 12, 15")[0].evidence_ids == (12, 15)


def test_the_summaries_reach_the_model_numbered_and_dated() -> None:
    body = render(THREE_DAYS)
    assert body.splitlines()[0].startswith("1 (18.08): Говорили про збірку")
    assert "3 (20.08)" in body


@pytest.mark.asyncio
async def test_the_model_is_asked_exactly_one_question() -> None:
    calls: list[str] = []

    async def stream(messages, _cfg, temperature=0.0):
        calls.append(messages[0]["content"])
        yield "треба переписати збірку | 1, 2, 3"

    found = await detector(lambda: object(), stream)(THREE_DAYS)

    assert len(calls) == 1
    assert "1 (18.08)" in calls[0]
    assert found == [Candidate("треба переписати збірку", (1, 2, 3))]


# ── telling one intention from another ────────────────────────────────


def test_the_same_intention_in_other_words_is_the_same_intention() -> None:
    """Two passes a week apart will not phrase it identically, and a
    unique index would let both through."""
    assert same_thing("треба переписати збірку", "хочу нарешті переписати збірку")


def test_two_intentions_that_share_a_verb_are_not_the_same() -> None:
    assert not same_thing("треба полагодити збірку", "треба полагодити тести")


# ── the table ─────────────────────────────────────────────────────────


class _DB:
    """An aiosqlite-shaped door onto a real file, so the SQL is real."""

    def __init__(self, path) -> None:
        self._conn = sqlite3.connect(str(path))

    async def executescript(self, script):
        self._conn.executescript(script)

    async def execute(self, sql, params=()):
        return _Cursor(self._conn.execute(sql, params))

    async def commit(self):
        self._conn.commit()


class _Cursor:
    def __init__(self, cursor) -> None:
        self._cursor = cursor
        self.lastrowid = cursor.lastrowid

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()


async def _store(tmp_path) -> ObservationStore:
    store = ObservationStore(_DB(tmp_path / "heare.db"))
    await store.init()
    return store


@pytest.mark.asyncio
async def test_two_in_one_day_is_one(tmp_path) -> None:
    """The ceiling is the whole reason this is tolerable to live with."""
    store = await _store(tmp_path)
    now = time.time()
    assert await store.record("треба переписати збірку", (1, 2, 3), now=now)

    assert await store.too_soon(now + 3600) is True
    assert await store.too_soon(now + DAY + 60) is False


@pytest.mark.asyncio
async def test_the_same_thing_noticed_again_is_not_recorded_twice(tmp_path) -> None:
    store = await _store(tmp_path)
    now = time.time()
    await store.record("треба переписати збірку", (1, 2, 3), now=now)

    again = await store.record(
        "хочу нарешті переписати збірку", (7, 8, 9), now=now + 8 * DAY
    )
    assert again is None


@pytest.mark.asyncio
async def test_waved_away_stays_waved_away(tmp_path) -> None:
    """`dismissed` is permanent, and the row stays in the table
    precisely so that it keeps blocking."""
    store = await _store(tmp_path)
    now = time.time()
    observation_id = await store.record("треба переписати збірку", (1, 2, 3), now=now)
    await store.dismiss(observation_id)

    assert (await store.recent())[0].dismissed is True
    assert await store.record("треба переписати збірку", (4, 5, 6), now=now + 30 * DAY) is None


@pytest.mark.asyncio
async def test_noticed_is_not_the_same_as_said(tmp_path) -> None:
    """Most of what gets this far is still refused by the model's veto;
    without the difference there is no way to tell, after a week,
    whether this earned its place."""
    store = await _store(tmp_path)
    observation_id = await store.record("треба переписати збірку", (1, 2, 3))

    assert (await store.recent())[0].said_ts is None
    await store.mark_said(observation_id, now=1_800_000_000.0)
    assert (await store.recent())[0].said_ts == 1_800_000_000.0


@pytest.mark.asyncio
async def test_a_broken_table_means_silence_not_an_exception(tmp_path) -> None:
    """Every failure here resolves to silence, which is the safe
    direction: a remark not made is never noticed by anyone."""

    class _Broken:
        async def executescript(self, _script):
            raise RuntimeError("disk is gone")

        async def execute(self, *_args):
            raise RuntimeError("disk is gone")

        async def commit(self):
            raise RuntimeError("disk is gone")

    store = ObservationStore(_Broken())
    await store.init()

    assert await store.too_soon() is True, "a fault must not lift the ceiling"
    assert await store.record("треба", (1, 2, 3)) is None
    assert await store.recent() == []


# ── the pass ──────────────────────────────────────────────────────────


def _rows(summaries: list[Summary]) -> list[tuple[int, float, str]]:
    return [(s.id, s.ts, s.text) for s in summaries]


async def _repeats(tmp_path, answer: str, summaries=None, asked=None):
    store = await _store(tmp_path)

    async def read(_since_ts):
        return _rows(THREE_DAYS if summaries is None else summaries)

    async def detect(seen):
        if asked is not None:
            asked.append(seen)
        return parse(answer)

    return Repeats(store=store, summaries=read, detect=detect)


@pytest.mark.asyncio
async def test_a_repeat_becomes_one_sentence_and_a_key(tmp_path) -> None:
    """The sentence carries its own evidence.

    Handed over as «треба переписати збірку» alone it is your own words
    read back at you, and the model's veto — the last gate before it is
    said — was refusing it for exactly that reason: nothing in front of
    it said this had ever happened before. Live on 24 August.
    """
    repeats = await _repeats(tmp_path, "треба переписати збірку | 1, 2, 3")

    found = await repeats.look(now=_at("2026-08-20", 20))

    assert found == (key(1), "Втретє за три дні чую від тебе: "
                             "треба переписати збірку")


def test_the_counting_is_in_the_sentence_or_it_is_nowhere() -> None:
    """Three mentions over two days is the whole restraint of this
    feature. Until 24 August it went into a log line and nowhere else."""
    from src.spine.repeats import Candidate, Summary, phrase

    summaries = [
        Summary(1, _at("2026-08-18", 10), "про збірку"),
        Summary(2, _at("2026-08-18", 16), "знову про збірку"),
        Summary(3, _at("2026-08-19", 11), "втретє про збірку"),
    ]
    said = phrase(Candidate("треба переписати збірку", (1, 2, 3)), summaries)

    assert said == "Втретє за два дні чую від тебе: треба переписати збірку"


def test_the_intention_is_quoted_not_reported() -> None:
    """«хочу перейти на інший тариф» cannot go after «ти кажеш, що»
    without changing who wants it. The pass writes in the first person
    because that is how the person said it."""
    from src.spine.repeats import Candidate, Summary, phrase

    summaries = [
        Summary(1, _at("2026-08-18", 10), "a"),
        Summary(2, _at("2026-08-19", 10), "b"),
        Summary(3, _at("2026-08-20", 10), "c"),
        Summary(4, _at("2026-08-21", 10), "d"),
    ]
    said = phrase(Candidate("хочу перейти на інший тариф", (1, 2, 3, 4)),
                  summaries)

    assert said.endswith(": хочу перейти на інший тариф")
    assert said.startswith("Вчетверте за чотири дні")


@pytest.mark.asyncio
async def test_the_second_conversation_of_the_day_asks_nothing(tmp_path) -> None:
    """Once there is an observation, the rest of the day costs one
    indexed query and no model call at all."""
    asked: list = []
    repeats = await _repeats(tmp_path, "треба переписати збірку | 1, 2, 3", asked=asked)
    now = _at("2026-08-20", 20)

    assert await repeats.look(now=now) is not None
    assert await repeats.look(now=now + 3600) is None
    assert len(asked) == 1


@pytest.mark.asyncio
async def test_too_few_summaries_and_the_model_is_never_asked(tmp_path) -> None:
    asked: list = []
    repeats = await _repeats(
        tmp_path, "треба переписати збірку | 1, 2",
        summaries=THREE_DAYS[: ENOUGH_SUMMARIES - 1], asked=asked,
    )

    assert await repeats.look(now=_at("2026-08-20", 20)) is None
    assert asked == []


@pytest.mark.asyncio
async def test_nothing_is_the_ordinary_answer(tmp_path) -> None:
    repeats = await _repeats(tmp_path, "НІЧОГО")
    assert await repeats.look(now=_at("2026-08-20", 20)) is None


@pytest.mark.asyncio
async def test_a_key_that_is_not_its_own_is_ignored(tmp_path) -> None:
    """The engine offers every intent's fate to every source."""
    repeats = await _repeats(tmp_path, "НІЧОГО")
    await repeats.mark_said("job:27")  # must not raise
    await repeats.dismiss(None)


# ── through the engine ────────────────────────────────────────────────


class _Store:
    """Enough of IntentStore to watch what the engine does with it."""

    def __init__(self) -> None:
        self.added: list[dict] = []

    async def add(self, kind, text, **kwargs):
        self.added.append({"kind": kind, "text": text, **kwargs})
        return len(self.added)

    async def pending(self, limit=10, now=None):
        return []


def _persist(tmp_path):
    from src.spine.persist import SpinePersistence

    return SpinePersistence(tmp_path / "heare.db")


@pytest.mark.asyncio
async def test_a_conversation_ending_is_the_trigger_not_a_timer(tmp_path) -> None:
    """The last thing here that spoke unbidden ran on a clock and had to
    be deleted. A tick that closes nothing must look at nothing."""
    from src.spine.engine import Engine

    looks: list[float] = []

    class _Repeats:
        async def look(self, *, now=None):
            looks.append(now)
            return None

    p = _persist(tmp_path)
    engine = Engine(store=_Store(), say=_silent, persist=p, repeats=_Repeats())

    for tick in range(12):
        await engine._close_conversation(now=1000.0 + tick * 60)
    assert looks == [], "nothing closed, so there was nothing new to read"

    p.log_agent_reply("так", p.log_user_turn("треба переписати збірку"))
    await engine._close_conversation(now=_now(p) + 3600)
    assert len(looks) == 1


@pytest.mark.asyncio
async def test_it_arrives_as_an_intent_and_inherits_every_guard(tmp_path) -> None:
    """Not a parallel machine: `judge`, `ask` and `trust` all sit
    downstream of this row existing."""
    from src.spine.engine import Engine
    from src.spine.intents import SELF

    class _Repeats:
        async def look(self, *, now=None):
            return "repeat:1", "треба переписати збірку"

    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("треба переписати збірку"))
    store = _Store()
    engine = Engine(store=store, say=_silent, persist=p, repeats=_Repeats())

    await engine._close_conversation(now=_now(p) + 3600)

    assert store.added == [
        {
            "kind": "repeat",
            "text": "треба переписати збірку",
            "origin": SELF,
            "urgency": pytest.approx(0.3),
            "dedupe_key": "repeat:1",
            "expires_ts": None,
        }
    ]


@pytest.mark.asyncio
async def test_being_told_to_leave_it_dismisses_the_observation() -> None:
    """`reaction_to` already knows what being waved away sounds like;
    this is the narrower, permanent half of the same signal."""
    from src.spine import intents as I
    from src.spine.engine import Engine

    dismissed: list[str] = []

    class _Repeats:
        async def mark_said(self, dedupe_key):
            return None

        async def dismiss(self, dedupe_key):
            dismissed.append(dedupe_key)

    class _Settling(_Store):
        async def settle(self, intent_id, outcome):
            return None

    engine = Engine(store=_Settling(), say=_silent, repeats=_Repeats())
    engine._awaiting = I.Intent(
        id=1, kind="repeat", text="треба переписати збірку", origin=I.SELF,
        urgency=0.3, state=I.VOICED, created_ts=0.0, updated_ts=0.0,
        dedupe_key="repeat:7",
    )

    await engine.observe_reply("не треба, дай спокій")

    assert dismissed == ["repeat:7"]


@pytest.mark.asyncio
async def test_a_repeats_pass_that_falls_over_still_closes_the_conversation(
    tmp_path,
) -> None:
    from src.spine.engine import Engine

    class _Repeats:
        async def look(self, *, now=None):
            raise RuntimeError("the model is down")

    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("привіт"))
    engine = Engine(store=_Store(), say=_silent, persist=p, repeats=_Repeats())

    await engine._close_conversation(now=_now(p) + 3600)  # must not raise

    with sqlite3.connect(tmp_path / "heare.db") as db:
        assert db.execute(
            "SELECT end_ts FROM conversations"
        ).fetchone()[0] is not None


# ── what the pass reads ───────────────────────────────────────────────


def test_only_conversations_that_got_a_summary_are_read(tmp_path) -> None:
    """`summariser` deliberately writes nothing when there was nothing
    to compress, and a blank line among the numbered ones is a row the
    model can still cite as evidence."""
    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("треба переписати збірку"))
    first, _ = p.close_idle_conversation(now=_now(p) + 3600, after_s=1800)
    p.save_summary(first, "Про збірку: треба переписати.")

    p.log_agent_reply("ага", p.log_user_turn("і ще раз"))
    p.close_idle_conversation(now=_now(p) + 3600, after_s=1800)  # no summary

    read = p.recent_summaries(0.0)
    assert [row[0] for row in read] == [first]


def test_a_week_ago_is_out_of_the_window(tmp_path) -> None:
    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("треба переписати збірку"))
    conversation_id, _ = p.close_idle_conversation(now=_now(p) + 3600, after_s=1800)
    p.save_summary(conversation_id, "Про збірку.")

    assert p.recent_summaries(time.time() + 60) == []
    assert len(p.recent_summaries(time.time() - 5 * DAY)) == 1


def _now(p) -> float:
    return float(p.last_turn_times()["any"])


async def _silent(_text: str) -> None:
    return None
