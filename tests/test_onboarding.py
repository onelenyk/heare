"""Tests for ``src/daemon/onboarding.py``.

State-file path is controlled by the module-level ``HEARE_HOME`` constant
imported from ``src.config``. Tests redirect it via monkeypatch so no real
``~/.heare/`` is touched.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from src.config import Settings
from src.daemon.onboarding import (
    OnboardingStep,
    _check_capabilities_cached,
    _check_claude_configured,
    _check_claude_installed,
    _load_state,
    _save_state,
    _state_path,
    is_onboarded,
    migrate_existing_install,
    record_done,
    reset,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def heare_home(tmp_path: Path) -> Path:
    home = tmp_path / ".heare"
    home.mkdir(parents=True)
    return home


@pytest.fixture
def settings(heare_home: Path, monkeypatch) -> Settings:
    """Settings backed by a per-test temporary HEARE_HOME.

    Both ``src.config.HEARE_HOME`` and ``src.daemon.onboarding.HEARE_HOME``
    are patched because ``_state_path`` uses the onboarding module's copy.
    """
    workspace = heare_home / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("src.config.HEARE_HOME", heare_home)
    monkeypatch.setattr("src.daemon.onboarding.HEARE_HOME", heare_home)
    return Settings(
        workspace_dir=workspace,
        capabilities_file=heare_home / "capabilities.json",
    )


def _write_mcp_file(settings: Settings, servers: dict | None = None) -> None:
    body = {"mcpServers": servers if servers is not None else {}}
    (settings.workspace_dir / ".mcp.json").write_text(json.dumps(body))


def _patch_steps(monkeypatch, *fakes: OnboardingStep) -> None:
    monkeypatch.setattr("src.daemon.onboarding.STEPS", list(fakes))


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def test_state_file_load_missing(settings: Settings) -> None:
    state = _load_state(settings)
    assert state == {"version": 1, "steps": {}}


def test_state_file_load_corrupt(settings: Settings, heare_home: Path) -> None:
    (heare_home / "onboarding.json").write_text("{not json")
    state = _load_state(settings)
    assert state == {"version": 1, "steps": {}}


def test_state_file_v1_schema(settings: Settings, heare_home: Path) -> None:
    """Write a v1 state file directly; reload; assert structure intact."""
    v1 = {"version": 1, "steps": {"claude_installed": {"done_at": "2024-01-01T00:00:00+00:00"}}}
    (heare_home / "onboarding.json").write_text(json.dumps(v1))
    state = _load_state(settings)
    assert state["version"] == 1
    assert isinstance(state["steps"], dict)
    assert "claude_installed" in state["steps"]
    assert state["steps"]["claude_installed"]["done_at"] == "2024-01-01T00:00:00+00:00"


def test_state_idempotent(settings: Settings) -> None:
    """Write state twice; second write doesn't corrupt; file is valid JSON."""
    _save_state(settings, {"version": 1, "steps": {"a": {"done_at": "t1"}}})
    _save_state(settings, {"version": 1, "steps": {"a": {"done_at": "t1"}}})
    path = _state_path(settings)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert "a" in data["steps"]


def test_record_done_writes_iso_timestamp(settings: Settings) -> None:
    record_done(settings, "claude_installed")
    state = _load_state(settings)
    assert "claude_installed" in state["steps"]
    iso = state["steps"]["claude_installed"]["done_at"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", iso)


def test_save_state_atomic_no_tmp_leftover(settings: Settings) -> None:
    _save_state(settings, {"version": 1, "steps": {"x": {"done_at": "now"}}})
    assert _state_path(settings).exists()
    assert not _state_path(settings).with_suffix(".json.tmp").exists()


# ---------------------------------------------------------------------------
# OnboardingStep dataclass: machine-checkable semantics
# ---------------------------------------------------------------------------


def test_machine_checkable_step_done(monkeypatch, settings: Settings) -> None:
    """requires_attestation=False + is_done=True → done without state record."""
    fake = OnboardingStep(
        id="m", title="m", instructions="",
        is_done=lambda _s: True,
        on_confirm=lambda _s: None,
    )
    _patch_steps(monkeypatch, fake)
    assert is_onboarded(settings) is True


def test_machine_checkable_step_pending_when_is_done_false(
    monkeypatch, settings: Settings
) -> None:
    """requires_attestation=False + is_done=False → not done even with stale record."""
    fake = OnboardingStep(
        id="m", title="m", instructions="",
        is_done=lambda _s: False,
        on_confirm=lambda _s: None,
    )
    _patch_steps(monkeypatch, fake)
    record_done(settings, "m")  # stale record — must NOT flip the gate
    assert is_onboarded(settings) is False


# ---------------------------------------------------------------------------
# OnboardingStep dataclass: attestation semantics
# ---------------------------------------------------------------------------


def test_attestation_step_requires_state(monkeypatch, settings: Settings) -> None:
    """requires_attestation=True: needs BOTH state-file record AND is_done=True."""
    artifact_present: dict[str, bool] = {"v": False}

    fake = OnboardingStep(
        id="a", title="a", instructions="",
        is_done=lambda _s: artifact_present["v"],
        on_confirm=lambda _s: None,
        requires_attestation=True,
    )
    _patch_steps(monkeypatch, fake)

    # Neither → not done
    assert is_onboarded(settings) is False

    # Artifact only (no record) → not done
    artifact_present["v"] = True
    assert is_onboarded(settings) is False

    # Record only (no artifact) → not done
    artifact_present["v"] = False
    record_done(settings, "a")
    assert is_onboarded(settings) is False

    # Both present → done
    artifact_present["v"] = True
    assert is_onboarded(settings) is True


def test_artifact_deletion_after_record_trips_gate(
    monkeypatch, settings: Settings
) -> None:
    """Deleting the artifact after recording must re-trip the gate."""
    artifact_present: dict[str, bool] = {"v": True}
    fake = OnboardingStep(
        id="a", title="a", instructions="",
        is_done=lambda _s: artifact_present["v"],
        on_confirm=lambda _s: None,
        requires_attestation=True,
    )
    _patch_steps(monkeypatch, fake)
    record_done(settings, "a")
    assert is_onboarded(settings) is True
    artifact_present["v"] = False
    assert is_onboarded(settings) is False


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migrate_legacy_onboarded(monkeypatch, tmp_path: Path) -> None:
    """Touch legacy ~/.heare/.onboarded; call migrate; assert state file written."""
    heare_home = tmp_path / ".heare"
    heare_home.mkdir(parents=True)
    workspace = heare_home / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("src.config.HEARE_HOME", heare_home)
    monkeypatch.setattr("src.daemon.onboarding.HEARE_HOME", heare_home)

    s = Settings(
        workspace_dir=workspace,
        capabilities_file=heare_home / "capabilities.json",
    )

    # Stub out STEPS: one machine-checkable step that is already done
    fake_done = OnboardingStep(
        id="claude_installed",
        title="t",
        instructions="",
        is_done=lambda _s: True,
        on_confirm=lambda _s: None,
    )
    fake_undone = OnboardingStep(
        id="claude_configured",
        title="t",
        instructions="",
        is_done=lambda _s: False,
        on_confirm=lambda _s: None,
    )
    monkeypatch.setattr("src.daemon.onboarding.STEPS", [fake_done, fake_undone])

    migrate_existing_install(s)

    state_file = heare_home / "onboarding.json"
    assert state_file.exists(), "state file must be created by migrate_existing_install"
    data = json.loads(state_file.read_text())
    assert "claude_installed" in data["steps"], "done step must be recorded"
    assert data["steps"]["claude_installed"]["migrated"] is True
    assert "done_at" in data["steps"]["claude_installed"]
    assert "claude_configured" not in data["steps"], "undone step must NOT be recorded"


def test_migrate_does_not_overwrite_existing_records(
    monkeypatch, settings: Settings
) -> None:
    """Existing records are preserved; migrated flag is not added."""
    fake = OnboardingStep(
        id="a", title="t", instructions="",
        is_done=lambda _s: True,
        on_confirm=lambda _s: None,
    )
    _patch_steps(monkeypatch, fake)
    record_done(settings, "a")
    pre = _load_state(settings)["steps"]["a"]["done_at"]
    migrate_existing_install(settings)
    post = _load_state(settings)["steps"]["a"]["done_at"]
    assert pre == post
    assert "migrated" not in _load_state(settings)["steps"]["a"]


def test_migrate_no_change_when_all_steps_pending(
    monkeypatch, settings: Settings, heare_home: Path
) -> None:
    """When no step is done, state file is not created."""
    fake = OnboardingStep(
        id="x", title="t", instructions="",
        is_done=lambda _s: False,
        on_confirm=lambda _s: None,
    )
    _patch_steps(monkeypatch, fake)
    migrate_existing_install(settings)
    assert not (heare_home / "onboarding.json").exists()


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_clears_state_and_capabilities_preserves_mcp(
    settings: Settings,
) -> None:
    record_done(settings, "claude_installed")
    settings.capabilities_file.write_text(json.dumps({"skills": []}))
    _write_mcp_file(settings, {"keep-me": {"command": "x"}})

    reset(settings)

    assert not _state_path(settings).exists()
    assert not settings.capabilities_file.exists()
    mcp = settings.workspace_dir / ".mcp.json"
    assert mcp.exists()
    assert json.loads(mcp.read_text())["mcpServers"] == {"keep-me": {"command": "x"}}


# ---------------------------------------------------------------------------
# Step body checks (skip capabilities — touches claude_capabilities import)
# ---------------------------------------------------------------------------


def test_check_claude_installed_yes(monkeypatch, settings: Settings) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")
    assert _check_claude_installed(settings) is True


def test_check_claude_installed_no(monkeypatch, settings: Settings) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert _check_claude_installed(settings) is False


def test_check_claude_configured_missing_file(settings: Settings) -> None:
    assert _check_claude_configured(settings) is False


def test_check_claude_configured_valid_empty(settings: Settings) -> None:
    _write_mcp_file(settings, {})
    assert _check_claude_configured(settings) is True


def test_check_capabilities_cached_missing(settings: Settings) -> None:
    assert _check_capabilities_cached(settings) is False


def test_check_capabilities_cached_too_small(settings: Settings) -> None:
    settings.capabilities_file.write_text("{}")  # 2 bytes, threshold is > 2
    assert _check_capabilities_cached(settings) is False


def test_check_capabilities_cached_populated(settings: Settings) -> None:
    settings.capabilities_file.write_text(json.dumps({"skills": [], "mcp_servers": []}))
    assert _check_capabilities_cached(settings) is True
