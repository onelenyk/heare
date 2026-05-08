"""Onboarding registry, state file, and gate helpers.

Drives ``heare setup`` and the ``_cmd_start`` hard-gate. Each
``OnboardingStep`` knows how to check itself (``is_done``) and how to
satisfy itself (``on_confirm``). Adding a step is a one-line append to
``STEPS``.

State is persisted at ``~/.heare/onboarding.json`` with the shape::

    {"version": 1, "steps": {<id>: {"done_at": "<iso>", "migrated": ...}}}

Done-ness semantics (per plan, .omc/plans/onboarding-flow.md):

* Machine-checkable steps (``requires_attestation=False``): done iff
  ``is_done(settings)`` returns ``True``. The state file is irrelevant.
* Attestation steps (``requires_attestation=True``): done iff the step
  is recorded in state AND ``is_done(settings)`` is currently ``True``.

Note: ``~/.heare/.onboarded`` (legacy) is written by ``set-passphrase``
and is unrelated to this system.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.config import HEARE_HOME, Settings
from src.daemon.workspace import ensure_workspace_mcp


logger = logging.getLogger("heare.onboarding")


_STATE_FILE_NAME = "onboarding.json"
_STATE_VERSION = 1


@dataclass(frozen=True)
class OnboardingStep:
    """One step in the onboarding flow.

    ``requires_attestation=True`` means the state file is consulted in
    addition to ``is_done`` — used when ``is_done`` cannot tell "user
    actually did the work" from "user didn't need to do anything".
    """

    id: str
    title: str
    instructions: str
    is_done: Callable[[Settings], bool]
    on_confirm: Callable[[Settings], None]
    requires_attestation: bool = False


# ---------------------------------------------------------------------------
# Step bodies (module-level so tests can monkeypatch)
# ---------------------------------------------------------------------------


def _check_claude_installed(_settings: Settings) -> bool:
    return shutil.which("claude") is not None


def _confirm_claude_installed(_settings: Settings) -> None:
    if shutil.which("claude") is None:
        raise RuntimeError(
            "claude CLI not found on PATH — install from "
            "https://docs.claude.com/claude-code"
        )


def _check_claude_configured(settings: Settings) -> bool:
    mcp = settings.workspace_dir / ".mcp.json"
    if not mcp.exists():
        return False
    try:
        data = json.loads(mcp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and "mcpServers" in data


def _confirm_claude_configured(settings: Settings) -> None:
    # Idempotent: no-ops if target exists.
    ensure_workspace_mcp(settings.workspace_dir)


def _check_capabilities_cached(settings: Settings) -> bool:
    cache = settings.capabilities_file
    return cache.exists() and cache.stat().st_size > 2


def _confirm_capabilities_cached(settings: Settings) -> None:
    """Run ``refresh_capabilities`` synchronously.

    Sync CLI entry point only. If a future caller invokes this from inside
    a running event loop, raise a clear error rather than letting
    ``asyncio.run`` fail opaquely.
    """
    from src.daemon.claude_capabilities import refresh_capabilities

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Expected: no running loop, sync caller.
        asyncio.run(refresh_capabilities(
            settings.capabilities_file,
            workspace_dir=settings.workspace_dir,
            force=True,
        ))
        return
    raise RuntimeError(
        "_confirm_capabilities_cached must be called from a sync context. "
        "Inside an event loop, await refresh_capabilities directly."
    )


STEPS: list[OnboardingStep] = [
    OnboardingStep(
        id="claude_installed",
        title="claude CLI on PATH",
        instructions=(
            "Install Claude Code: https://docs.claude.com/claude-code\n"
            "Then re-run `heare setup`."
        ),
        is_done=_check_claude_installed,
        on_confirm=_confirm_claude_installed,
    ),
    OnboardingStep(
        id="claude_configured",
        title="claude is authenticated and MCPs are configured",
        instructions=(
            "Run `claude` interactively and ensure:\n"
            "  1. Authentication is complete (login if first run)\n"
            "  2. Run `/mcp` to view connected MCP servers\n"
            "  3. Authenticate any unauthenticated remote MCPs you want voice-accessible\n"
            "When done, return here and press [enter] to confirm."
        ),
        is_done=_check_claude_configured,
        on_confirm=_confirm_claude_configured,
        requires_attestation=True,
    ),
    OnboardingStep(
        id="capabilities_cached",
        title="initial capability snapshot built",
        instructions=(
            "Building the initial capability snapshot — reads "
            "workspace/.mcp.json and ~/.claude/skills. Should take <1s."
        ),
        is_done=_check_capabilities_cached,
        on_confirm=_confirm_capabilities_cached,
    ),
]


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def _state_path(_settings: Settings) -> Path:
    return HEARE_HOME / _STATE_FILE_NAME


def _load_state(settings: Settings) -> dict:
    """Return the state dict. Missing/corrupt → default shape."""
    path = _state_path(settings)
    if not path.exists():
        return {"version": _STATE_VERSION, "steps": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": _STATE_VERSION, "steps": {}}
    if not isinstance(data, dict) or "steps" not in data or not isinstance(data["steps"], dict):
        return {"version": _STATE_VERSION, "steps": {}}
    # Future migrations: branch on data.get("version", 0) and rewrite shape.
    return data


def _save_state(settings: Settings, state: dict) -> None:
    path = _state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def record_done(settings: Settings, step_id: str) -> None:
    state = _load_state(settings)
    state["steps"].setdefault(step_id, {})["done_at"] = (
        datetime.now(timezone.utc).isoformat()
    )
    _save_state(settings, state)


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------


def step_status(settings: Settings) -> list[tuple[OnboardingStep, bool]]:
    """Return ``(step, done?)`` for each step in ``STEPS``.

    Done-ness rules:
      * Machine-checkable (``requires_attestation=False``): done iff
        ``is_done(settings)``. State file is ignored.
      * Attestation (``requires_attestation=True``): done iff the step is
        recorded in state AND ``is_done(settings)``. Deletion of the
        artifact after recording trips the gate again.
    """
    state = _load_state(settings)
    out: list[tuple[OnboardingStep, bool]] = []
    for step in STEPS:
        live = step.is_done(settings)
        if step.requires_attestation:
            recorded = step.id in state["steps"]
            done = recorded and live
        else:
            done = live
        out.append((step, done))
    return out


def list_pending(settings: Settings) -> list[OnboardingStep]:
    return [s for s, done in step_status(settings) if not done]


def is_onboarded(settings: Settings) -> bool:
    return not list_pending(settings)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_existing_install(settings: Settings) -> None:
    """For users predating onboarding: synthesize records from current state.

    For each step whose live ``is_done`` returns ``True`` and which isn't
    yet recorded, write a record with ``done_at = now`` and ``migrated:
    True``. For attestation steps, this means upgrade-time artifact
    existence is treated as proxy attestation; if the artifact is later
    deleted, ``is_done`` flips false and the gate trips again.
    """
    state = _load_state(settings)
    changed = False
    for step in STEPS:
        if step.id in state["steps"]:
            continue
        if step.is_done(settings):
            state["steps"][step.id] = {
                "done_at": datetime.now(timezone.utc).isoformat(),
                "migrated": True,
            }
            changed = True
    if changed:
        _save_state(settings, state)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def reset(settings: Settings) -> None:
    """Wipe onboarding state and the capability cache.

    ``workspace/.mcp.json`` is intentionally preserved — it may contain
    user edits beyond the seeded baseline.
    """
    _state_path(settings).unlink(missing_ok=True)
    settings.capabilities_file.unlink(missing_ok=True)
