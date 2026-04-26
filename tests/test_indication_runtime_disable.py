"""US-IND-A6: runtime disable / re-enable via Indication.reload()."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.config import IndicationSettings
from src.indication import (
    Indication,
    IndicationKind,
    IndicationLevel,
)


@dataclass
class RecordingBackend:
    name: str = "visual"
    captured: list = field(default_factory=list)
    aclose_calls: int = 0

    async def fire(self, kind, level, title, body, meta) -> None:
        self.captured.append((kind, level, title, body, meta))

    async def aclose(self) -> None:
        self.aclose_calls += 1


async def test_reload_disables_then_reenables_with_fresh_backends() -> None:
    backend1 = RecordingBackend()
    backend2 = RecordingBackend()
    settings_on = IndicationSettings(enabled=True, sound_enabled=False, notification_center_enabled=False)
    settings_off = IndicationSettings(enabled=False)

    ind = Indication(settings_on, [backend1])
    ind.notify(IndicationKind.MODE_CHANGED)
    await asyncio.sleep(0.05)
    assert len(backend1.captured) == 1, "first notify should fire"

    # Disable via reload
    await ind.reload(settings_off)
    assert ind.is_enabled is False
    assert backend1.aclose_calls == 1

    # Notifies during disabled state are dropped (no backends fire)
    ind.notify(IndicationKind.MODE_CHANGED)
    ind.notify(IndicationKind.HEARTBEAT_TICK)
    await asyncio.sleep(0.05)
    assert len(backend1.captured) == 1  # unchanged

    # Re-enable with fresh backend (caller's responsibility to provide new
    # backends after reload aclose'd the old ones).
    await ind.reload(settings_on, new_backends=[backend2])
    assert ind.is_enabled is True

    ind.notify(IndicationKind.MODE_CHANGED)
    await asyncio.sleep(0.05)
    assert len(backend2.captured) == 1
    assert backend1.captured == [(
        IndicationKind.MODE_CHANGED,
        IndicationLevel.INFO,
        "heare: mode change",
        "",
        {},
    )]


async def test_notify_during_drain_short_circuits_via_lock() -> None:
    """A notify arriving while reload() holds the lock with _enabled=False
    must not enqueue into a closing backend.
    """
    backend = RecordingBackend()
    ind = Indication(IndicationSettings(enabled=True), [backend])

    # Slowly close: aclose blocks until we set the event.
    close_event = asyncio.Event()

    async def slow_aclose():
        await close_event.wait()
        backend.aclose_calls += 1

    backend.aclose = slow_aclose  # type: ignore[method-assign]

    # Disable inline. Without awaiting, kick off reload so it proceeds to drain/aclose.
    reload_task = asyncio.create_task(ind.reload(IndicationSettings(enabled=False)))

    # Yield once so reload acquires lock and flips _enabled=False.
    await asyncio.sleep(0)

    # While reload is awaiting close_event inside aclose, notify must NOT fire.
    ind.notify(IndicationKind.MODE_CHANGED)
    ind.notify(IndicationKind.HEARTBEAT_TICK)
    await asyncio.sleep(0.05)
    assert backend.captured == []  # no fires during disabled drain

    close_event.set()
    await reload_task
    assert backend.aclose_calls == 1


async def test_sighup_handler_pattern_reaches_facade_via_singleton(
    monkeypatch, tmp_path
) -> None:
    """Regression for the architect-flagged SIGHUP closure bug: the SIGHUP
    handler in src/main.py must locate the Indication facade via the
    module-level singleton, NOT via locals() lookup that always returns None.

    This test simulates what _handle_sighup does end-to-end: reads settings,
    looks up the facade via get_indication(), then schedules reload.
    """
    from src import indication as ind_mod
    from src.config import load_settings

    backend = RecordingBackend()
    ind = Indication(IndicationSettings(enabled=True), [backend])
    ind_mod.set_indication(ind)
    try:
        # The handler body (mirroring src/main.py):
        facade = ind_mod.get_indication()
        assert facade is ind, "SIGHUP handler must resolve facade via singleton"

        # Disable in config and reload via the same path.
        monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("HEARE_MODE", raising=False)
        (tmp_path / "config.toml").write_text(
            "[indication]\nenabled = false\n"
        )
        new_settings = load_settings()
        await facade.reload(new_settings.indication)
        assert facade.is_enabled is False
    finally:
        ind_mod.set_indication(None)


async def test_aclose_after_reload_disable_is_idempotent() -> None:
    backend = RecordingBackend()
    ind = Indication(IndicationSettings(enabled=True), [backend])
    await ind.reload(IndicationSettings(enabled=False))
    # Second close on the (now closed) backend would increment if fired again.
    await ind.aclose()
    # aclose tolerates the disabled state; behavior is no-op-style.
    assert ind.is_enabled is False
