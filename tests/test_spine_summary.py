"""What a conversation was, once it is over.

Two things had to be true before `summary` could ever be written, and
neither was: something has to decide that a conversation ended, and
something has to be willing to say nothing when there is nothing to say.

The first had been broken since 13 August — the code that closed a
conversation went with the engine deleted that day, so one row stayed
open for nine days and held every turn since. The second is the whole
risk of the feature: a model asked to summarise two lines about the
weather will produce a sentence rather than admit there is nothing
there, and an invented summary poisons every search that reads it later.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.spine.summary import (
    ENOUGH_LINES,
    summariser,
    transcript,
    worth_summarising,
)

def said(n: int) -> list[tuple[int, str]]:
    return [(i % 2, f"репліка {i}") for i in range(n)]


# ── knowing when to say nothing ───────────────────────────────────────


def test_two_lines_are_not_a_conversation() -> None:
    """Asked anyway, the model writes a sentence rather than admit the
    conversation had nothing in it. An empty field is honest."""
    assert worth_summarising(said(ENOUGH_LINES - 1)) is False
    assert worth_summarising(said(ENOUGH_LINES)) is True


def test_blank_lines_do_not_count_toward_being_worth_it() -> None:
    assert worth_summarising([(0, "  "), (1, ""), (0, "так"), (1, "ага")]) is False


@pytest.mark.asyncio
async def test_the_model_is_not_called_for_a_conversation_of_two_lines() -> None:
    calls: list[list[dict]] = []

    async def stream(messages, _cfg, temperature=0.0):
        calls.append(messages)
        yield "щось вигадане"

    summarise = summariser(lambda: object(), stream)
    assert await summarise(said(2)) is None
    assert calls == [], "nothing should reach the model"


@pytest.mark.asyncio
async def test_a_real_conversation_becomes_two_sentences() -> None:
    async def stream(messages, _cfg, temperature=0.0):
        assert "Людина: репліка 0" in messages[0]["content"]
        assert "Асистент: репліка 1" in messages[0]["content"]
        for part in ("Говорили про таймаути. ", "Вирішили підняти до тридцяти."):
            yield part

    summarise = summariser(lambda: object(), stream)
    assert await summarise(said(8)) == (
        "Говорили про таймаути. Вирішили підняти до тридцяти."
    )


@pytest.mark.asyncio
async def test_a_model_that_answers_with_nothing_writes_nothing() -> None:
    async def stream(_messages, _cfg, temperature=0.0):
        yield "   "

    assert await summariser(lambda: object(), stream)(said(8)) is None


# ── the transcript it reads ───────────────────────────────────────────


def test_who_said_what_survives_into_the_prompt() -> None:
    text = transcript([(0, "яка погода"), (1, "сонячно")])
    assert text == "Людина: яка погода\nАсистент: сонячно"


def test_a_very_long_conversation_is_trimmed_from_the_middle() -> None:
    """What a conversation turned out to be about is usually decided at
    its start and its end; the middle is where it wandered."""
    long = [(0, "початок " * 10)] + [(1, "середина " * 200)] * 40 + [(0, "кінець")]
    text = transcript(long)

    assert "початок" in text
    assert "кінець" in text
    assert "…середина розмови пропущена…" in text
    assert len(text) < 9000


# ── the boundary itself ───────────────────────────────────────────────


def _persist(tmp_path):
    from src.spine.persist import SpinePersistence

    return SpinePersistence(tmp_path / "heare.db")


def test_a_conversation_that_is_still_warm_does_not_end(tmp_path) -> None:
    p = _persist(tmp_path)
    p.log_agent_reply("привіт", p.log_user_turn("привіт"))

    assert p.close_idle_conversation(now=_now(p) + 60, after_s=1800) is None


def test_silence_is_the_only_boundary_there_is(tmp_path) -> None:
    """A voice assistant has no hang-up."""
    p = _persist(tmp_path)
    p.log_agent_reply("тридцять", p.log_user_turn("який таймаут"))

    closed = p.close_idle_conversation(now=_now(p) + 3600, after_s=1800)

    assert closed is not None
    conversation_id, lines = closed
    assert [text for _, text in lines] == ["який таймаут", "тридцять"]
    assert [agent for agent, _ in lines] == [0, 1]


def test_the_end_is_the_last_thing_said_not_the_moment_it_was_noticed(
    tmp_path,
) -> None:
    """Stamping "now" would put the end of the conversation wherever a
    tick happened to run, and make every gap look like part of it."""
    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("ще там?"))
    last = _now(p)

    conversation_id, _ = p.close_idle_conversation(now=last + 9999, after_s=1800)

    with sqlite3.connect(tmp_path / "heare.db") as db:
        end_ts = db.execute(
            "SELECT end_ts FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0]
    assert abs(end_ts - last) < 1.0


def test_an_empty_conversation_is_unused_not_stale(tmp_path) -> None:
    """Closing it would leave a row with no turns behind and open another
    exactly like it on the next word."""
    p = _persist(tmp_path)
    p._active_conversation_id()

    assert p.close_idle_conversation(now=1e10, after_s=1800) is None


def test_the_next_word_starts_a_new_conversation(tmp_path) -> None:
    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("перше"))
    first, _ = p.close_idle_conversation(now=_now(p) + 3600, after_s=1800)

    p.log_agent_reply("ага", p.log_user_turn("друге"))
    second, lines = p.close_idle_conversation(now=_now(p) + 3600, after_s=1800)

    assert second != first
    assert [text for _, text in lines] == ["друге", "ага"]


def test_a_summary_is_written_once(tmp_path) -> None:
    """Two passes over the same conversation must not overwrite what the
    first one concluded."""
    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("привіт"))
    conversation_id, _ = p.close_idle_conversation(now=_now(p) + 3600, after_s=1800)

    p.save_summary(conversation_id, "перший підсумок")
    p.save_summary(conversation_id, "другий підсумок")

    with sqlite3.connect(tmp_path / "heare.db") as db:
        stored = db.execute(
            "SELECT summary FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()[0]
    assert stored == "перший підсумок"


def _now(p) -> float:
    return float(p.last_turn_times()["any"])


# ── through the engine ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_engine_closes_and_writes_without_being_asked(tmp_path) -> None:
    """Both halves live in the engine because both are about what
    outlives a turn."""
    from src.spine.engine import Engine

    p = _persist(tmp_path)
    p.log_agent_reply("тридцять", p.log_user_turn("який таймаут"))
    p.log_agent_reply("тимчасово", p.log_user_turn("надовго?"))

    async def summarise(lines):
        assert len(lines) == 4
        return "Говорили про таймаут."

    class _Store:
        async def pending(self, limit=10, now=None):
            return []

    engine = Engine(store=_Store(), say=_silent, persist=p, summarise=summarise)
    await engine._close_conversation(now=_now(p) + 3600)

    with sqlite3.connect(tmp_path / "heare.db") as db:
        assert db.execute(
            "SELECT summary FROM conversations WHERE end_ts IS NOT NULL"
        ).fetchone()[0] == "Говорили про таймаут."


@pytest.mark.asyncio
async def test_a_summariser_that_falls_over_still_ends_the_conversation(
    tmp_path,
) -> None:
    """A summary that cannot be written is a summary missing, not a
    conversation that could not end."""
    from src.spine.engine import Engine

    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("привіт"))

    async def summarise(_lines):
        raise RuntimeError("the model is down")

    class _Store:
        async def pending(self, limit=10, now=None):
            return []

    engine = Engine(store=_Store(), say=_silent, persist=p, summarise=summarise)
    await engine._close_conversation(now=_now(p) + 3600)  # must not raise

    with sqlite3.connect(tmp_path / "heare.db") as db:
        row = db.execute(
            "SELECT end_ts, summary FROM conversations"
        ).fetchone()
    assert row[0] is not None, "the conversation still ended"
    assert row[1] is None


@pytest.mark.asyncio
async def test_it_does_not_ask_the_database_on_every_tick(tmp_path) -> None:
    """The tick runs every five seconds; this is a query, not a thought."""
    from src.spine.engine import Engine

    calls = {"n": 0}

    class _Persist:
        def close_idle_conversation(self, *, now, after_s):
            calls["n"] += 1
            return None

    class _Store:
        async def pending(self, limit=10, now=None):
            return []

    engine = Engine(store=_Store(), say=_silent, persist=_Persist())
    for tick in range(12):
        await engine._close_conversation(now=1000.0 + tick * 5)

    assert calls["n"] == 1, "once a minute, not once a tick"


async def _silent(_text: str) -> None:
    return None
