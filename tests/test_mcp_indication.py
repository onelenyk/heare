"""US-IND-B4 / C4: MCP_AUTH_REQUIRED + MCP_DISCONNECTED debounced notifications."""
from __future__ import annotations

import asyncio

from src import indication as ind_mod
from src.config import IndicationSettings
from src.indication import IndicationKind
from src.mcp_utils import (
    notify_mcp_auth_required,
    notify_mcp_disconnected,
    reset_mcp_indication_debounce,
)


class _Capturing:
    name = "visual"

    def __init__(self) -> None:
        self.captured: list = []

    async def fire(self, kind, level, title, body, meta) -> None:
        self.captured.append((kind, level, title, body, meta))

    async def aclose(self) -> None:
        pass


async def _setup() -> tuple[_Capturing, callable]:
    backend = _Capturing()
    settings = IndicationSettings(
        sound_enabled=False,
        notification_center_enabled=False,
        cooldown_seconds=0.0,
    )
    ind = ind_mod.Indication(settings, [backend])
    ind_mod.set_indication(ind)
    reset_mcp_indication_debounce()

    def teardown() -> None:
        ind_mod.set_indication(None)
        reset_mcp_indication_debounce()

    return backend, teardown


async def test_auth_required_fires_once_per_server() -> None:
    backend, teardown = await _setup()
    try:
        assert notify_mcp_auth_required("github") is True
        assert notify_mcp_auth_required("github") is False
        assert notify_mcp_auth_required("notion") is True
        await asyncio.sleep(0.05)
    finally:
        teardown()

    auth_events = [t for t in backend.captured if t[0] == IndicationKind.MCP_AUTH_REQUIRED]
    assert len(auth_events) == 2
    bodies = [t[3] for t in auth_events]
    assert "github" in bodies[0]
    assert "notion" in bodies[1]


async def test_auth_required_body_text_does_not_claim_subcommand() -> None:
    """Plan §B4: body must NOT promise `claude mcp authenticate` (unverified)."""
    backend, teardown = await _setup()
    try:
        notify_mcp_auth_required("github")
        await asyncio.sleep(0.05)
    finally:
        teardown()

    body = backend.captured[0][3]
    assert "claude mcp authenticate" not in body
    assert "check terminal" in body


async def test_disconnected_fires_once_per_server() -> None:
    backend, teardown = await _setup()
    try:
        assert notify_mcp_disconnected("notion") is True
        assert notify_mcp_disconnected("notion") is False
        await asyncio.sleep(0.05)
    finally:
        teardown()

    events = [t for t in backend.captured if t[0] == IndicationKind.MCP_DISCONNECTED]
    assert len(events) == 1


async def test_reset_debounce_allows_re_notify() -> None:
    backend, teardown = await _setup()
    try:
        notify_mcp_auth_required("github")
        reset_mcp_indication_debounce()
        notify_mcp_auth_required("github")
        await asyncio.sleep(0.05)
    finally:
        teardown()

    events = [t for t in backend.captured if t[0] == IndicationKind.MCP_AUTH_REQUIRED]
    assert len(events) == 2
