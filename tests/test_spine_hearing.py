"""It has to be able to notice that it stopped hearing.

Four times in two days this project produced something that looked alive
and was not: a build with no PortAudio, a process whose device went away
when the machine slept, a bundle with no roles, an engine whose guard
against interrupting could never fire. Different bugs, one shape — the
failure was plain from the inside and invisible from the outside.

These are the cases that shape. Two of them are transcribed from real
logs rather than imagined.
"""

from __future__ import annotations

import asyncio

import pytest

from src.spine.hearing import SILENT_AFTER_S, EarWatch, Hearing, read

NOW = 1_000_000.0


def ear(**kw) -> Hearing:
    base = dict(wired=True, stream_open=True, silent_for=0.05, muted=False)
    base.update(kw)
    return Hearing(**base)


# ── what counts as deaf ───────────────────────────────────────────────


def test_a_quiet_room_is_not_deafness() -> None:
    """Frames keep arriving from a silent room — that is the whole
    reason this measures callbacks and not sound."""
    assert ear(silent_for=3600.0 * 0).deaf is False


def test_a_device_that_stopped_calling_is(tmp_path) -> None:
    assert ear(silent_for=SILENT_AFTER_S + 1).deaf is True


def test_a_stream_that_never_opened_is_deaf_immediately() -> None:
    """Transcribed from a real boot: PortAudio was missing from the
    bundle, the daemon came up, served its dashboard and answered /state
    with 200. Waiting twenty seconds to admit it would be twenty seconds
    of pretending."""
    assert ear(stream_open=False, silent_for=0.0).deaf is True


def test_a_mute_is_not_a_fault() -> None:
    """Silence you asked for is not a failure, and an assistant that
    announces its own mute is worse than one that says nothing."""
    assert ear(muted=True, silent_for=9999.0).deaf is False
    assert ear(muted=True, stream_open=False).deaf is False


def test_a_run_with_no_audio_at_all_is_not_deaf() -> None:
    """`--text` has no ear to lose."""
    assert Hearing(wired=False, stream_open=False, silent_for=0.0, muted=False).deaf is False


# ── saying it, once ───────────────────────────────────────────────────


def test_it_says_so_the_first_time() -> None:
    watch = EarWatch()
    found = watch.feed(ear(silent_for=SILENT_AFTER_S + 5), now=NOW)

    assert found is not None
    text, key = found
    assert "не чую" in text or "перестала чути" in text
    assert key.startswith("deaf:")


def test_an_outage_of_an_hour_is_still_one_remark() -> None:
    """Thirty-five identical retries went into a log overnight. Thirty
    five remarks would be worse than none."""
    watch = EarWatch()
    assert watch.feed(ear(silent_for=SILENT_AFTER_S + 1), now=NOW) is not None
    for minute in range(1, 60):
        assert watch.feed(
            ear(silent_for=SILENT_AFTER_S + minute * 60), now=NOW + minute * 60
        ) is None


def test_the_key_names_the_outage_not_the_moment_it_was_asked() -> None:
    """Otherwise asking twice about one fault makes it two things to
    say."""
    first = EarWatch().feed(ear(silent_for=30.0), now=NOW)
    later = EarWatch().feed(ear(silent_for=40.0), now=NOW + 10)

    assert first is not None and later is not None
    assert first[1] == later[1]


def test_a_second_fault_is_said_again() -> None:
    """The first remark was about the first outage. Someone who fixed
    that one has no reason to assume the next."""
    watch = EarWatch()
    assert watch.feed(ear(silent_for=SILENT_AFTER_S + 1), now=NOW) is not None
    assert watch.feed(ear(silent_for=0.05), now=NOW + 60) is None  # back
    assert watch.feed(ear(silent_for=SILENT_AFTER_S + 1), now=NOW + 600) is not None


def test_what_it_says_is_a_sentence_a_person_can_act_on() -> None:
    """"The input stream is not open" is not something anyone should
    have to hear from a thing that talks."""
    text, _ = EarWatch().feed(ear(stream_open=False), now=NOW)

    assert "мікрофон" in text
    assert "перезапустити" in text
    assert "stream" not in text.lower()


# ── reading the real front end ────────────────────────────────────────


def test_it_reads_an_audio_object() -> None:
    class _Audio:
        input_open = True
        mute_input_user = False

        def silent_for(self) -> float:
            return 42.0

    got = read(_Audio())
    assert got.wired is True and got.silent_for == 42.0 and got.deaf is True


def test_a_front_end_that_throws_does_not_take_the_watchdog_with_it() -> None:
    """The whole point of this is to be the part that still works when
    other parts do not."""

    class _Broken:
        input_open = True
        mute_input_user = False

        def silent_for(self) -> float:
            raise OSError("the device is gone")

    assert read(_Broken()).deaf is False  # honest: it cannot tell


def test_no_audio_reads_as_no_ear() -> None:
    assert read(None).wired is False


# ── through the engine ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_engine_raises_it_like_anything_else() -> None:
    """Deliberately the same road: judged, so it cannot land mid-sentence
    or repeat itself, and it costs the same trust if unwelcome."""
    from src.spine import intents as I
    from src.spine.engine import Engine

    added: list[tuple] = []

    class _Store:
        async def add(self, kind, text, *, origin=I.SELF, urgency=0.5,
                      dedupe_key=None, expires_ts=None):
            added.append((kind, origin, urgency, dedupe_key))

        async def pending(self, limit=10, now=None):
            return []

    async def hearing():
        return ear(stream_open=False)

    engine = Engine(store=_Store(), say=_silent, hearing=hearing)
    await engine._listen_to_itself(now=NOW)

    assert len(added) == 1
    kind, origin, urgency, key = added[0]
    assert kind == "deaf"
    assert origin == I.USER, "it is a fault in their tool, not the engine's idea"
    assert urgency >= 0.8, "it has to survive the night filter"
    assert key.startswith("deaf:")


@pytest.mark.asyncio
async def test_the_engine_does_not_ask_the_device_on_every_call() -> None:
    calls = {"n": 0}

    from src.spine.engine import Engine

    async def hearing():
        calls["n"] += 1
        return ear()

    class _Store:
        async def pending(self, limit=10, now=None):
            return []

    from src.spine.engine import HEAR_EVERY_S

    engine = Engine(store=_Store(), say=_silent, hearing=hearing)
    for tick in range(int(HEAR_EVERY_S)):  # one whole interval, second by second
        await engine._listen_to_itself(now=NOW + tick)

    assert calls["n"] == 1, "the tick runs every five seconds; this is not free"


async def _silent(_text: str) -> None:
    return None
