"""US-IND-A3: Indication facade — gating, cooldown, mode, quiet hours, thread-safety."""
from __future__ import annotations

import asyncio
import datetime as dt
import threading
from dataclasses import dataclass, field

from src.config import IndicationKindToggle, IndicationSettings, Mode
from src.indication import (
    Indication,
    IndicationKind,
    IndicationLevel,
    KIND_TO_LEVEL,
    _is_within_quiet_hours,
)


@dataclass
class RecordingBackend:
    name: str
    captured: list[tuple[IndicationKind, IndicationLevel, str, str, dict]] = field(
        default_factory=list
    )
    fail: bool = False

    async def fire(
        self,
        kind: IndicationKind,
        level: IndicationLevel,
        title: str,
        body: str,
        meta: dict,
    ) -> None:
        if self.fail:
            raise RuntimeError("backend boom")
        self.captured.append((kind, level, title, body, meta))

    async def aclose(self) -> None:
        pass


def _settings(**overrides) -> IndicationSettings:
    s = IndicationSettings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# Quiet-hours helper
# ---------------------------------------------------------------------------


def test_quiet_hours_simple_window() -> None:
    ranges = [("09:00", "17:00")]
    assert _is_within_quiet_hours(dt.time(10, 0), ranges) is True
    assert _is_within_quiet_hours(dt.time(8, 59), ranges) is False
    assert _is_within_quiet_hours(dt.time(17, 0), ranges) is False  # exclusive end


def test_quiet_hours_wraps_midnight() -> None:
    ranges = [("22:00", "07:00")]
    assert _is_within_quiet_hours(dt.time(23, 0), ranges) is True
    assert _is_within_quiet_hours(dt.time(2, 0), ranges) is True
    assert _is_within_quiet_hours(dt.time(7, 0), ranges) is False
    assert _is_within_quiet_hours(dt.time(8, 0), ranges) is False


def test_quiet_hours_invalid_entry_skipped() -> None:
    # Should not raise; invalid range is ignored.
    assert _is_within_quiet_hours(dt.time(12, 0), [("garbage", "07:00")]) is False


# ---------------------------------------------------------------------------
# Facade gating
# ---------------------------------------------------------------------------


async def test_notify_dispatches_to_all_enabled_backends() -> None:
    sound = RecordingBackend(name="sound")
    visual = RecordingBackend(name="visual")
    notif = RecordingBackend(name="notification")
    # Pin wallclock outside default 22:00-07:00 quiet hours so the sound
    # backend isn't suppressed when this test runs at night.
    outside_quiet = dt.datetime(2026, 4, 24, 12, 0)
    ind = Indication(
        _settings(), [sound, visual, notif], wallclock=lambda: outside_quiet
    )

    ind.notify(IndicationKind.OWNER_AUTO_ENROLLED, body="hi")
    await asyncio.sleep(0.05)

    # OWNER_AUTO_ENROLLED is SUCCESS level. Defaults: sound=True, visual=True, notification=False
    assert len(sound.captured) == 1
    assert len(visual.captured) == 1
    assert notif.captured == []


async def test_master_disable_blocks_everything() -> None:
    sound = RecordingBackend(name="sound")
    visual = RecordingBackend(name="visual")
    ind = Indication(_settings(enabled=False), [sound, visual])

    ind.notify(IndicationKind.ACTION_FAILED, body="x")
    await asyncio.sleep(0.05)
    assert sound.captured == []
    assert visual.captured == []


async def test_per_kind_toggle_disables_individual_backend() -> None:
    visual = RecordingBackend(name="visual")
    notif = RecordingBackend(name="notification")
    settings = _settings()
    # Disable visual for ATTENTION level only
    settings.kinds["attention"] = IndicationKindToggle(
        sound=True, visual=False, notification=True
    )
    # Pin wallclock outside default 22:00-07:00 quiet hours so ATTENTION
    # notification fires unconditionally.
    outside_quiet = dt.datetime(2026, 4, 24, 12, 0)
    ind = Indication(settings, [visual, notif], wallclock=lambda: outside_quiet)

    ind.notify(IndicationKind.CONFIRMATION_DEADLINE)  # level=ATTENTION
    await asyncio.sleep(0.05)

    assert visual.captured == []
    assert len(notif.captured) == 1


async def test_cooldown_coalesces_same_kind() -> None:
    visual = RecordingBackend(name="visual")
    fake_now = [100.0]

    def clock() -> float:
        return fake_now[0]

    ind = Indication(
        _settings(cooldown_seconds=1.5),
        [visual],
        clock=clock,
    )

    ind.notify(IndicationKind.INFO if False else IndicationKind.AWAITING_CONFIRMATION)
    fake_now[0] += 0.5
    ind.notify(IndicationKind.AWAITING_CONFIRMATION)  # within cooldown — dropped
    fake_now[0] += 1.5
    ind.notify(IndicationKind.AWAITING_CONFIRMATION)  # outside cooldown — fires
    await asyncio.sleep(0.05)

    assert len(visual.captured) == 2


async def test_cooldown_does_not_block_different_kind() -> None:
    visual = RecordingBackend(name="visual")
    fake_now = [0.0]
    ind = Indication(
        _settings(cooldown_seconds=1.5),
        [visual],
        clock=lambda: fake_now[0],
    )

    ind.notify(IndicationKind.AWAITING_CONFIRMATION)
    ind.notify(IndicationKind.MODE_CHANGED)  # different kind — fires immediately
    await asyncio.sleep(0.05)

    assert len(visual.captured) == 2


async def test_quiet_hours_block_sound_not_visual_not_notification_for_input_waiting() -> None:
    sound = RecordingBackend(name="sound")
    visual = RecordingBackend(name="visual")
    notif = RecordingBackend(name="notification")

    # Wallclock "inside" quiet hours.
    inside = dt.datetime(2026, 4, 24, 23, 30)
    settings = _settings(quiet_hours=[("22:00", "07:00")])
    settings.kinds["input_waiting"] = IndicationKindToggle(
        sound=True, visual=True, notification=True
    )
    ind = Indication(
        settings,
        [sound, visual, notif],
        wallclock=lambda: inside,
    )

    ind.notify(IndicationKind.AWAITING_CONFIRMATION)  # level=INPUT_WAITING
    await asyncio.sleep(0.05)

    assert sound.captured == []  # quiet hours block sound
    assert len(visual.captured) == 1
    # INPUT_WAITING bypasses quiet hours for notification
    assert len(notif.captured) == 1


async def test_quiet_hours_block_notification_for_non_input_waiting_levels() -> None:
    notif = RecordingBackend(name="notification")
    inside = dt.datetime(2026, 4, 24, 23, 30)
    settings = _settings(quiet_hours=[("22:00", "07:00")])
    settings.kinds["error"] = IndicationKindToggle(
        sound=True, visual=True, notification=True
    )
    ind = Indication(settings, [notif], wallclock=lambda: inside)

    ind.notify(IndicationKind.ACTION_FAILED)  # level=ERROR
    await asyncio.sleep(0.05)

    assert notif.captured == []


async def test_mode_silent_blocks_sound_only() -> None:
    sound = RecordingBackend(name="sound")
    visual = RecordingBackend(name="visual")
    settings = _settings()
    # ensure ATTENTION sound is configured
    settings.kinds["attention"] = IndicationKindToggle(True, True, True)
    ind = Indication(
        settings,
        [sound, visual],
        mode_provider=lambda: Mode.SILENT,
    )

    ind.notify(IndicationKind.CONFIRMATION_DEADLINE)  # ATTENTION
    await asyncio.sleep(0.05)

    assert sound.captured == []
    assert len(visual.captured) == 1


async def test_mode_focus_blocks_sound_for_info_levels() -> None:
    sound = RecordingBackend(name="sound")
    settings = _settings()
    # Allow SoundBackend to fire for INFO at the per-kind layer.
    settings.kinds["info"] = IndicationKindToggle(sound=True, visual=True, notification=False)
    ind = Indication(
        settings,
        [sound],
        mode_provider=lambda: Mode.FOCUS,
    )
    ind.notify(IndicationKind.HEARTBEAT_TICK)  # INFO
    await asyncio.sleep(0.05)
    assert sound.captured == []  # focus mode blocks INFO sound


async def test_mode_focus_allows_sound_for_attention() -> None:
    sound = RecordingBackend(name="sound")
    outside_quiet = dt.datetime(2026, 4, 24, 12, 0)
    ind = Indication(
        _settings(),
        [sound],
        mode_provider=lambda: Mode.FOCUS,
        wallclock=lambda: outside_quiet,
    )
    ind.notify(IndicationKind.CONFIRMATION_DEADLINE)  # ATTENTION
    await asyncio.sleep(0.05)
    assert len(sound.captured) == 1


async def test_backend_failure_does_not_block_others() -> None:
    bad = RecordingBackend(name="sound", fail=True)
    good = RecordingBackend(name="visual")
    ind = Indication(_settings(), [bad, good])
    ind.notify(IndicationKind.ACTION_FAILED)
    await asyncio.sleep(0.05)
    assert len(good.captured) == 1


async def test_unknown_kind_is_ignored() -> None:
    visual = RecordingBackend(name="visual")
    ind = Indication(_settings(), [visual])

    class _Fake(str):
        value = "made_up"

    ind.notify(_Fake("made_up"))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    assert visual.captured == []


# ---------------------------------------------------------------------------
# Thread-safety (the load-bearing AC for A3)
# ---------------------------------------------------------------------------


async def test_notify_from_worker_thread_dispatches_via_run_coroutine_threadsafe() -> None:
    """Spawn a worker thread, call notify() from it, assert delivery."""
    visual = RecordingBackend(name="visual")
    ind = Indication(_settings(), [visual])

    started = threading.Event()

    def worker() -> None:
        started.set()
        ind.notify(IndicationKind.MODE_CHANGED, body="thread-fired")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    started.wait(timeout=1.0)
    t.join(timeout=1.0)

    # Allow the loop to drain the off-loop submission.
    deadline = asyncio.get_event_loop().time() + 1.0
    while not visual.captured and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.02)
    assert len(visual.captured) == 1
    assert visual.captured[0][3] == "thread-fired"


async def test_notify_from_main_loop_dispatches_via_call_soon_threadsafe() -> None:
    visual = RecordingBackend(name="visual")
    ind = Indication(_settings(), [visual])
    ind.notify(IndicationKind.MODE_CHANGED, body="main-loop")
    await asyncio.sleep(0.05)
    assert visual.captured[-1][3] == "main-loop"


# ---------------------------------------------------------------------------
# aclose / reload basics (A6 covers reload edge cases)
# ---------------------------------------------------------------------------


async def test_aclose_is_idempotent_and_disables() -> None:
    visual = RecordingBackend(name="visual")
    ind = Indication(_settings(), [visual])
    await ind.aclose()
    ind.notify(IndicationKind.MODE_CHANGED)
    await asyncio.sleep(0.05)
    assert visual.captured == []
    # second aclose is a no-op
    await ind.aclose()


async def test_kind_to_level_covers_every_kind() -> None:
    """Catch additions to IndicationKind that forget the level mapping."""
    missing = [k for k in IndicationKind if k not in KIND_TO_LEVEL]
    assert missing == [], f"kinds without level mapping: {missing}"


# ---------------------------------------------------------------------------
# US-EG-1: enrollment-active flag
# ---------------------------------------------------------------------------


async def test_enrollment_flag_set_by_start_and_end() -> None:
    """notify(REENROLL_RECORDING_START) -> True; notify(REENROLL_RECORDING_END) -> False."""
    visual = RecordingBackend(name="visual")
    ind = Indication(_settings(), [visual])
    assert ind.is_enrollment_active is False
    ind.notify(IndicationKind.REENROLL_RECORDING_START)
    assert ind.is_enrollment_active is True
    ind.notify(IndicationKind.REENROLL_RECORDING_END)
    assert ind.is_enrollment_active is False


async def test_enrollment_flag_updates_through_cooldown() -> None:
    """Even if the kind is in cooldown, the flag still updates."""
    fake_now = [100.0]
    visual = RecordingBackend(name="visual")
    ind = Indication(
        _settings(cooldown_seconds=10.0),
        [visual],
        clock=lambda: fake_now[0],
    )
    ind.notify(IndicationKind.REENROLL_RECORDING_START)
    assert ind.is_enrollment_active is True
    # Within cooldown: backend dispatch is coalesced, but flag must remain True.
    ind.notify(IndicationKind.REENROLL_RECORDING_START)
    assert ind.is_enrollment_active is True
    ind.notify(IndicationKind.REENROLL_RECORDING_END)
    assert ind.is_enrollment_active is False


async def test_enrollment_flag_updates_when_indication_disabled() -> None:
    """When _enabled is False, the flag must still update for input gating."""
    visual = RecordingBackend(name="visual")
    ind = Indication(_settings(enabled=False), [visual])
    assert ind.is_enrollment_active is False
    ind.notify(IndicationKind.REENROLL_RECORDING_START)
    assert ind.is_enrollment_active is True
    ind.notify(IndicationKind.REENROLL_RECORDING_END)
    assert ind.is_enrollment_active is False
