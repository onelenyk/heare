"""The reducer is the panel's whole brain: events in, one word out.

The table the tests are built around, for a link that is live (``online``):

    event kind                 state       notes
    -------------------------  ----------  -------------------------------
    phase idle                 IDLE        the base phase
    phase hearing              HEAR
    phase thinking             THINK
    phase speaking             SPEAK
    permission {id}            ALLOW       remembers the id, wins over phase
    question {id}              ALLOW       same, question id kept
    permission_closed {id}     phase       only a matching id clears ALLOW
    question_closed {id}       phase       only a matching id clears ALLOW
    said failed != null        ERROR       5 s from now
    said failed null           unchanged   the text is never kept
    failed                     ERROR       10 s from now
    cannot                     ERROR       5 s from now
    anything else              unchanged   replay tolerance

Outside a live stream the link decides: boot/wifi/brain/pair take the place
of the phase, and offline (after a stream was online once) outranks
everything. ALLOW and unexpired ERROR still outrank the link, because a
permission is the one thing the panel must not miss while reconnecting.
"""

from __future__ import annotations

import pytest
from heare_panel import state

from tests.firmware.fakes import TICKS_PERIOD, FakeClock

NOW = 1_000
PHASES = (
    ("idle", state.IDLE),
    ("hearing", state.HEAR),
    ("thinking", state.THINK),
    ("speaking", state.SPEAK),
)


def live() -> state.Display:
    return state.initial("online")


def reduced(event: dict[str, object], now: int = NOW) -> state.Display:
    return state.reduce(live(), event, now)


def test_every_state_is_reachable():
    samples = {
        state.BOOT: state.initial("boot"),
        state.WIFI: state.initial("wifi"),
        state.PAIR: state.initial("pair"),
        state.BRAIN: state.initial("brain"),
        state.IDLE: reduced({"kind": "phase", "phase": "idle"}),
        state.HEAR: reduced({"kind": "phase", "phase": "hearing"}),
        state.THINK: reduced({"kind": "phase", "phase": "thinking"}),
        state.SPEAK: reduced({"kind": "phase", "phase": "speaking"}),
        state.ALLOW: reduced({"kind": "permission", "id": 7}),
        state.ERROR: reduced({"kind": "failed"}),
        state.OFFLINE: state.reduce(live(), {}, NOW, "offline"),
    }
    assert set(samples) == set(state.STATES)
    for expected, display in samples.items():
        assert display.state == expected


@pytest.mark.parametrize("phase,expected", PHASES)
def test_phase_maps_to_its_state(phase: str, expected: str):
    assert reduced({"kind": "phase", "phase": phase}).state == expected


def test_permission_and_question_remember_id_and_kind():
    permission = reduced({"kind": "permission", "id": 7})
    assert permission.state == state.ALLOW
    assert permission.allow_id == 7
    assert permission.allow_kind == "permission"

    question = reduced({"kind": "question", "id": 8})
    assert question.state == state.ALLOW
    assert question.allow_id == 8
    assert question.allow_kind == "question"


def test_allow_persists_until_the_matching_close():
    display = reduced({"kind": "phase", "phase": "speaking"})
    display = state.reduce(display, {"kind": "permission", "id": 7}, NOW)
    assert display.state == state.ALLOW

    display = state.reduce(display, {"kind": "phase", "phase": "thinking"}, NOW + 1)
    assert display.state == state.ALLOW

    display = state.reduce(display, {}, NOW + 2)
    assert display.state == state.ALLOW

    display = state.reduce(display, {"kind": "permission_closed", "id": 8}, NOW + 3)
    assert display.state == state.ALLOW
    assert display.allow_id == 7

    display = state.reduce(display, {"kind": "permission_closed", "id": 7}, NOW + 4)
    assert display.state == state.THINK
    assert display.allow_id is None


def test_a_question_is_not_closed_by_a_permission_of_the_same_id():
    display = reduced({"kind": "question", "id": 3})
    display = state.reduce(display, {"kind": "permission_closed", "id": 3}, NOW)
    assert display.state == state.ALLOW
    display = state.reduce(display, {"kind": "question_closed", "id": 3}, NOW + 1)
    assert display.state == state.IDLE


@pytest.mark.parametrize(
    "event,ttl",
    [
        ({"kind": "said", "failed": "output"}, state.SAID_ERROR_MS),
        ({"kind": "failed"}, state.FAILED_ERROR_MS),
        ({"kind": "cannot"}, state.CANNOT_ERROR_MS),
    ],
)
def test_error_ttl_expires(event: dict[str, object], ttl: int):
    display = state.reduce(live(), event, 0)
    assert display.state == state.ERROR
    assert display.error_until == ttl

    assert state.reduce(display, {}, ttl - 1).state == state.ERROR
    assert state.reduce(display, {}, ttl).state == state.IDLE
    assert state.reduce(display, {}, ttl + 1).state == state.IDLE


def test_error_deadline_is_wrap_safe(fake_clock: FakeClock):
    fake_clock = FakeClock(TICKS_PERIOD - 100)
    display = state.reduce(live(), {"kind": "failed"}, fake_clock.now_ms)
    assert display.state == state.ERROR

    fake_clock.advance(9_999)
    assert state.reduce(display, {}, fake_clock.now_ms).state == state.ERROR

    fake_clock.advance(1)
    assert state.reduce(display, {}, fake_clock.now_ms).state == state.IDLE


def test_successful_said_keeps_no_text():
    display = reduced({"kind": "said", "failed": None, "text": "a secret sentence"})
    assert display.state == state.IDLE
    assert "a secret sentence" not in repr(display)


def test_unknown_kind_changes_nothing():
    display = reduced({"kind": "permission", "id": 7})
    for event in (
        {"kind": "bogus"},
        {"kind": "phase", "phase": "dancing"},
        {"kind": "speaking"},
        {},
    ):
        assert state.reduce(display, event, NOW + 1) == display


def test_duplicate_phase_is_idempotent():
    display = reduced({"kind": "phase", "phase": "hearing"})
    assert state.reduce(display, {"kind": "phase", "phase": "hearing"}, NOW) == display


def test_out_of_order_replay_keeps_the_newest_phase():
    display = state.reduce(
        live(), {"kind": "phase", "phase": "thinking", "at": 20.0}, NOW
    )
    display = state.reduce(
        display, {"kind": "phase", "phase": "hearing", "at": 10.0}, NOW
    )
    assert display.state == state.THINK

    display = state.reduce(display, {"kind": "phase", "phase": "idle", "at": 30.0}, NOW)
    assert display.state == state.IDLE


def test_replay_in_order_ends_at_the_last_phase():
    display = live()
    for at, phase in enumerate(("idle", "hearing", "thinking", "speaking")):
        display = state.reduce(
            display, {"kind": "phase", "phase": phase, "at": float(at)}, NOW
        )
    assert display.state == state.SPEAK


def test_offline_overrides_allow_and_online_returns_to_it():
    display = state.reduce(live(), {"kind": "permission", "id": 7}, NOW)
    assert display.state == state.ALLOW

    display = state.reduce(display, {}, NOW + 1, "offline")
    assert display.state == state.OFFLINE
    assert state.reduce(display, {}, NOW + 999_999).state == state.OFFLINE

    display = state.reduce(display, {}, NOW + 2, "online")
    assert display.state == state.ALLOW

    display = state.reduce(
        display, {"kind": "permission_closed", "id": 7}, NOW + 3, "online"
    )
    assert display.state == state.IDLE


def test_offline_before_the_first_stream_is_still_looking_for_the_brain():
    display = state.reduce(state.initial("brain"), {}, NOW, "offline")
    assert display.state == state.BRAIN


def test_online_clears_offline_without_a_permission():
    display = state.reduce(live(), {}, NOW, "offline")
    assert display.state == state.OFFLINE
    assert state.reduce(display, {}, NOW + 1, "online").state == state.IDLE


def test_link_none_keeps_the_previous_link():
    display = state.initial("wifi")
    assert state.reduce(display, {}, NOW).link == "wifi"
    assert state.reduce(display, {}, NOW, "boot").link == "boot"


def _both(now: int, high: str, low: str) -> state.Display:
    link = "offline" if state.OFFLINE in (high, low) else "online"
    allow_kind = "permission" if state.ALLOW in (high, low) else None
    error_until = now + 1_000 if state.ERROR in (high, low) else None
    phase_states = {state.SPEAK, state.THINK, state.HEAR, state.IDLE}
    if high in phase_states:
        phase = high
    elif low in phase_states:
        phase = low
    else:
        phase = state.IDLE
    return state.Display(
        state=state.IDLE,
        phase=phase,
        allow_id=1 if allow_kind else None,
        allow_kind=allow_kind,
        error_until=error_until,
        link=link,
        was_online=link == "offline",
    )


PRIORITY = (
    state.OFFLINE,
    state.ALLOW,
    state.ERROR,
    state.SPEAK,
    state.THINK,
    state.HEAR,
    state.IDLE,
)


@pytest.mark.parametrize(
    "high,low",
    [
        (PRIORITY[i], PRIORITY[j])
        for i in range(len(PRIORITY))
        for j in range(i + 1, len(PRIORITY))
    ],
)
def test_every_priority_pair_high_wins(high: str, low: str):
    display = _both(NOW, high, low)
    assert state.priority(display, NOW) == high


@pytest.mark.parametrize("phase,expected", PHASES)
def test_priority_of_a_phase_alone(phase: str, expected: str):
    display = state.Display(state=expected, phase=expected)
    assert state.priority(display, NOW) == expected
