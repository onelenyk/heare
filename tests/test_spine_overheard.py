"""What is said near it, and how long that lasts.

The microphone already hears the room and Whisper already transcribes
all of it — the wake gate decides whether to *act*, not whether to
listen. So an unaddressed line was heard, paid for, and thrown away,
which is why "he heard everything" was never true of the database: an
hour of being spoken near left zero rows.

Keeping it is one decision and three conditions. It is marked, so a
reader that does not ask for it never sees it. It expires, so the room
is working memory rather than a recording nobody remembers starting. And
it is off unless switched on, because the other people in the room did
not choose this.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from src.spine.persist import ADDRESSED, OVERHEARD, SpinePersistence


def _persist(tmp_path) -> SpinePersistence:
    return SpinePersistence(tmp_path / "heare.db")


def _rows(tmp_path) -> list[tuple]:
    with sqlite3.connect(tmp_path / "heare.db") as db:
        return db.execute(
            "SELECT text, source, turn_id FROM transcripts ORDER BY id"
        ).fetchall()


# ── marked, not mixed in ──────────────────────────────────────────────


def test_the_room_and_the_conversation_are_told_apart(tmp_path) -> None:
    p = _persist(tmp_path)
    p.log_agent_reply("тридцять", p.log_user_turn("який таймаут"))
    p.log_overheard("…а потім він каже, що збірка впала")

    rows = _rows(tmp_path)
    assert [r[1] for r in rows] == [ADDRESSED, ADDRESSED, OVERHEARD]


def test_an_overheard_line_is_never_part_of_an_exchange(tmp_path) -> None:
    """Nothing answered it. Given a turn_id it would join a conversation
    it was never in, and be read back as something the person said to the
    assistant."""
    p = _persist(tmp_path)
    p.log_overheard("хтось у кімнаті щось сказав")

    text, source, turn_id = _rows(tmp_path)[0]
    assert source == OVERHEARD
    assert turn_id is None
    with sqlite3.connect(tmp_path / "heare.db") as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0


def test_nothing_is_written_for_nothing(tmp_path) -> None:
    p = _persist(tmp_path)
    p.log_overheard("   ")
    p.log_overheard("")
    assert _rows(tmp_path) == []


# ── it expires ────────────────────────────────────────────────────────


def test_the_room_is_working_memory_not_a_recording(tmp_path) -> None:
    p = _persist(tmp_path)
    p.log_overheard("щось давнє")
    with sqlite3.connect(tmp_path / "heare.db") as db:
        db.execute("UPDATE transcripts SET ts = ?", (time.time() - 30 * 86400,))
        db.commit()
    p.log_overheard("щось свіже")

    assert p.forget_overheard(before_ts=time.time() - 7 * 86400) == 1
    assert [r[0] for r in _rows(tmp_path)] == ["щось свіже"]


def test_forgetting_never_touches_a_conversation(tmp_path) -> None:
    """Erasing what someone said *to* the assistant would be erasing
    their own words, which is a different act entirely."""
    p = _persist(tmp_path)
    p.log_agent_reply("так", p.log_user_turn("це я тобі кажу"))
    p.log_overheard("а це просто в кімнаті")
    with sqlite3.connect(tmp_path / "heare.db") as db:
        db.execute("UPDATE transcripts SET ts = ?", (time.time() - 30 * 86400,))
        db.commit()

    p.forget_overheard(before_ts=time.time())

    assert [r[0] for r in _rows(tmp_path)] == ["це я тобі кажу", "так"]


def test_forget_the_last_hour_looks_the_other_way(tmp_path) -> None:
    """The sweep drops what is old; a person asking out loud means what
    has just been said, usually by someone who did not know."""
    p = _persist(tmp_path)
    p.log_overheard("сказане давно")
    with sqlite3.connect(tmp_path / "heare.db") as db:
        db.execute("UPDATE transcripts SET ts = ?", (time.time() - 7200,))
        db.commit()
    p.log_overheard("сказане щойно")

    assert p.forget_overheard_since(after_ts=time.time() - 3600) == 1
    assert [r[0] for r in _rows(tmp_path)] == ["сказане давно"]


# ── off unless switched on ────────────────────────────────────────────


def test_silence_is_the_default(tmp_path) -> None:
    """A room holds other people. Off is what a default may be."""
    from src.spine.features import resolve
    from types import SimpleNamespace

    assert resolve(SimpleNamespace())["hear_all"] is False


def test_the_gate_keeps_what_it_turns_away_only_when_asked(tmp_path) -> None:
    from src.spine.loop import SpineLoop

    kept: list[str] = []

    class _Persist:
        def log_overheard(self, text: str) -> None:
            kept.append(text)

    def _loop(hear_all: bool) -> SpineLoop:
        return SpineLoop(
            audio=None, vad=None, assembler=None,
            transcribe=None, stream_chat=None,
            split_sentences=None, synthesise=None,
            persist=_Persist(), hear_all=hear_all,
        )

    asyncio.run(_loop(False)._keep_overheard("щось у кімнаті"))
    assert kept == []

    asyncio.run(_loop(True)._keep_overheard("щось у кімнаті"))
    assert kept == ["щось у кімнаті"]


def test_a_store_that_falls_over_does_not_stop_it_listening(tmp_path) -> None:
    from src.spine.loop import SpineLoop

    class _Persist:
        def log_overheard(self, text: str) -> None:
            raise RuntimeError("disk full")

    loop = SpineLoop(
        audio=None, vad=None, assembler=None,
        transcribe=None, stream_chat=None,
        split_sentences=None, synthesise=None,
        persist=_Persist(), hear_all=True,
    )
    asyncio.run(loop._keep_overheard("щось"))  # must not raise


# ── saying "forget that" out loud ─────────────────────────────────────


@pytest.mark.asyncio
async def test_forget_erases_the_last_hour_of_the_room(tmp_path) -> None:
    from src.spine.tools import VoiceToolbox

    p = _persist(tmp_path)
    p.log_overheard("те, чого не мало лишитись")

    async def _deliver(_text: str) -> None:
        return None

    box = VoiceToolbox(
        object(), None, _deliver,
        hands_factory=lambda _s: _NoHands(), persist=p,
    )
    spoken = await box.execute("forget", {"minutes": 60})

    assert "Забула" in spoken
    assert _rows(tmp_path) == []


@pytest.mark.asyncio
async def test_forget_says_so_when_there_was_nothing(tmp_path) -> None:
    from src.spine.tools import VoiceToolbox

    async def _deliver(_text: str) -> None:
        return None

    box = VoiceToolbox(
        object(), None, _deliver,
        hands_factory=lambda _s: _NoHands(), persist=_persist(tmp_path),
    )
    assert "не було" in await box.execute("forget", {})


@pytest.mark.asyncio
async def test_forget_is_honest_when_nothing_is_written_down(tmp_path) -> None:
    from src.spine.tools import VoiceToolbox

    async def _deliver(_text: str) -> None:
        return None

    box = VoiceToolbox(
        object(), None, _deliver, hands_factory=lambda _s: _NoHands(), persist=None
    )
    assert "нічого не записую" in await box.execute("forget", {})


class _NoHands:
    def set_delivery(self, _cb) -> None: ...
    def start(self, _task) -> None: ...
    def cancel_all(self) -> None: ...
