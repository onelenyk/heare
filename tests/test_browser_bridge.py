"""Unit tests for BrowserBridge — 13 test cases, no real Chrome needed.

All tests use a mock WebSocket client (websockets.connect) against a real
BrowserBridge server instance bound to a free ephemeral port. A tmp directory
stands in for ~/.heare so token/status files don't pollute the user's home.
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import websockets
from websockets.asyncio.client import connect

from src.config import Settings
from src.agent.browser_bridge import (
    BrowserBridge,
    CLOSE_AUTH_FAILED,
    WIRE_VERSION,
    ERR_NOT_CONNECTED,
    ERR_TIMEOUT,
    ERR_DISCONNECTED_MID_RPC,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _free_port() -> int:
    """Return an available TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _settings(tmp_path: Path, token: str = "test-token-abc", port: int | None = None) -> Settings:
    if port is None:
        port = _free_port()
    s = Settings(
        workspace_dir=tmp_path,
        browser_bridge_enabled=True,
        browser_bridge_port=port,
        browser_bridge_token=token,
    )
    return s


async def _start_bridge(settings: Settings, heare_home: Path) -> tuple[BrowserBridge, asyncio.Task]:
    """Start a BrowserBridge in a background task, patching HEARE_HOME."""
    with patch("src.agent.browser_bridge.HEARE_HOME", heare_home):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        # Give the server a moment to bind.
        await asyncio.sleep(0.05)
    return bridge, task


async def _auth_client(port: int, token: str, origin: str = "chrome-extension://test"):
    """Open a WS connection and perform the auth handshake. Returns the ws."""
    ws = await connect(
        f"ws://127.0.0.1:{port}",
        additional_headers={"Origin": origin},
    )
    await ws.send(json.dumps({"v": WIRE_VERSION, "type": "auth", "token": token}))
    raw = await ws.recv()
    msg = json.loads(raw)
    assert msg.get("type") == "auth_result"
    assert msg.get("ok") is True
    return ws


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_heare(tmp_path: Path) -> Path:
    h = tmp_path / "heare_home"
    h.mkdir()
    return h


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_server_lifecycle(tmp_path: Path, tmp_heare: Path) -> None:
    """Bridge starts, listens on the configured port, stops cleanly."""
    port = _free_port()
    settings = _settings(tmp_path, port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        # Port should be reachable
        with socket.socket() as s:
            s.settimeout(1)
            # raw connect should succeed (handshake may fail but port is open)
            try:
                s.connect(("127.0.0.1", port))
            except OSError:
                pytest.fail("Bridge port not reachable after start()")

        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_auth_success(tmp_path: Path, tmp_heare: Path) -> None:
    """Client sending the correct token receives auth_result ok=true."""
    port = _free_port()
    settings = _settings(tmp_path, token="mytoken", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await _auth_client(port, "mytoken")
        assert bridge.connected is True
        await ws.close()

        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_auth_failure(tmp_path: Path, tmp_heare: Path) -> None:
    """Client sending a wrong token receives close code 4001."""
    port = _free_port()
    settings = _settings(tmp_path, token="correct", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await connect(
            f"ws://127.0.0.1:{port}",
            additional_headers={"Origin": "chrome-extension://test"},
        )
        await ws.send(json.dumps({"v": WIRE_VERSION, "type": "auth", "token": "wrong"}))
        with pytest.raises(websockets.exceptions.ConnectionClosedError) as exc_info:
            await ws.recv()
        assert exc_info.value.rcvd.code == CLOSE_AUTH_FAILED

        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_multiple_connections_accepted(tmp_path: Path, tmp_heare: Path) -> None:
    """Two clients can connect simultaneously; list_tabs aggregates across both."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws1 = await _auth_client(port, "tok")
        ws2 = await _auth_client(port, "tok")

        assert bridge.connected is True

        tabs1 = [{"id": 1, "url": "https://a.com", "title": "A", "active": True}]
        tabs2 = [{"id": 2, "url": "https://b.com", "title": "B", "active": False}]

        async def responder(ws, tabs):
            raw = await ws.recv()
            req = json.loads(raw)
            assert req["method"] == "list_tabs"
            await ws.send(json.dumps({
                "v": WIRE_VERSION, "id": req["id"], "type": "response",
                "ok": True, "result": {"tabs": tabs},
            }))

        r1 = asyncio.create_task(responder(ws1, tabs1))
        r2 = asyncio.create_task(responder(ws2, tabs2))
        result = await bridge.list_tabs()
        await asyncio.gather(r1, r2)

        assert result["success"] is True
        all_tabs = result["result"]["tabs"]
        assert len(all_tabs) == 2
        conn_ids = {t["_conn"] for t in all_tabs}
        assert len(conn_ids) == 2  # each profile gets its own conn_id
        urls = {t["url"] for t in all_tabs}
        assert urls == {"https://a.com", "https://b.com"}

        await ws1.close()
        await ws2.close()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_rpc_round_trip(tmp_path: Path, tmp_heare: Path) -> None:
    """bridge.call() sends a request; mock client replies; caller gets result."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await _auth_client(port, "tok")

        expected_tabs = [{"id": 1, "url": "https://example.com", "title": "Example", "active": True}]

        async def mock_responder():
            raw = await ws.recv()
            req = json.loads(raw)
            assert req["type"] == "request"
            assert req["method"] == "list_tabs"
            assert req["v"] == WIRE_VERSION
            await ws.send(json.dumps({
                "v": WIRE_VERSION,
                "id": req["id"],
                "type": "response",
                "ok": True,
                "result": {"tabs": expected_tabs},
            }))

        responder = asyncio.create_task(mock_responder())
        result = await bridge.call("list_tabs", {})
        await responder

        assert result["success"] is True
        assert result["result"]["tabs"] == expected_tabs

        await ws.close()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_request_correlation(tmp_path: Path, tmp_heare: Path) -> None:
    """Two concurrent calls with out-of-order mock responses each get the correct result."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await _auth_client(port, "tok")

        reqs: list[dict] = []

        async def collect_and_respond():
            # Collect both requests
            r1 = json.loads(await ws.recv())
            r2 = json.loads(await ws.recv())
            reqs.extend([r1, r2])
            # Reply in reverse order
            await ws.send(json.dumps({
                "v": WIRE_VERSION, "id": r2["id"], "type": "response",
                "ok": True, "result": {"answer": "second"},
            }))
            await ws.send(json.dumps({
                "v": WIRE_VERSION, "id": r1["id"], "type": "response",
                "ok": True, "result": {"answer": "first"},
            }))

        responder = asyncio.create_task(collect_and_respond())
        call1 = asyncio.create_task(bridge.call("list_tabs", {}))
        call2 = asyncio.create_task(bridge.call("read_page", {}))
        r1, r2 = await asyncio.gather(call1, call2)
        await responder

        # Each call should match its own response regardless of reply order.
        answers = {r1["result"]["answer"], r2["result"]["answer"]}
        assert answers == {"first", "second"}

        await ws.close()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_rpc_timeout(tmp_path: Path, tmp_heare: Path) -> None:
    """Mock never responds; bridge.call() returns retryable=True error."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await _auth_client(port, "tok")

        result = await bridge.call("list_tabs", {}, timeout=0.1)
        assert result["success"] is False
        assert result["retryable"] is True

        await ws.close()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_connection_drop(tmp_path: Path, tmp_heare: Path) -> None:
    """Mock disconnects mid-call; outstanding futures resolve with retryable error."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await _auth_client(port, "tok")
        assert bridge.connected is True

        call_task = asyncio.create_task(bridge.call("list_tabs", {}, timeout=2.0))
        # Let the request be sent, then close abruptly
        await asyncio.sleep(0.05)
        await ws.close()

        result = await call_task
        assert result["success"] is False
        assert result["retryable"] is True

        await asyncio.sleep(0.05)
        assert bridge.connected is False

        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_reconnect(tmp_path: Path, tmp_heare: Path) -> None:
    """After a drop, a new client can connect, auth, and calls succeed."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws1 = await _auth_client(port, "tok")
        await ws1.close()
        await asyncio.sleep(0.05)

        ws2 = await _auth_client(port, "tok")
        assert bridge.connected is True

        async def respond():
            raw = await ws2.recv()
            req = json.loads(raw)
            await ws2.send(json.dumps({
                "v": WIRE_VERSION, "id": req["id"], "type": "response",
                "ok": True, "result": {"tabs": []},
            }))

        resp_task = asyncio.create_task(respond())
        result = await bridge.call("list_tabs", {})
        await resp_task

        assert result["success"] is True

        await ws2.close()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_malformed_message(tmp_path: Path, tmp_heare: Path) -> None:
    """Client sends non-JSON; server logs a warning and does not crash."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await _auth_client(port, "tok")
        await ws.send("this is not json {{{{")
        await asyncio.sleep(0.05)

        # Bridge should still be running / connected
        assert bridge.connected is True

        await ws.close()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_token_persistence(tmp_path: Path, tmp_heare: Path, monkeypatch) -> None:
    """Bridge reads token from Settings and does NOT regenerate when one is present.

    Verifies (a) write_browser_bridge_token is never called when a token exists,
    and (b) a client authenticating with the persisted token succeeds.
    """
    from unittest.mock import MagicMock

    port = _free_port()
    original_token = "persistent-token-xyz"
    settings = _settings(tmp_path, token=original_token, port=port)

    write_mock = MagicMock()
    monkeypatch.setattr(
        "src.agent.browser_bridge.write_browser_bridge_token", write_mock,
    )

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        # The bridge's internal token should be unchanged
        assert bridge._token == original_token
        # write_browser_bridge_token must NOT have been called when token already present
        assert not write_mock.called

        # Auth round-trip with the persisted token must succeed
        ws = await _auth_client(port, original_token)
        assert bridge.connected is True
        await ws.close()

        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_protocol_version(tmp_path: Path, tmp_heare: Path) -> None:
    """Every outbound server message includes 'v': 1."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await connect(
            f"ws://127.0.0.1:{port}",
            additional_headers={"Origin": "chrome-extension://test"},
        )
        await ws.send(json.dumps({"v": WIRE_VERSION, "type": "auth", "token": "tok"}))
        raw = await ws.recv()
        auth_result = json.loads(raw)
        assert auth_result.get("v") == WIRE_VERSION

        # Also verify RPC response includes v:1
        async def respond():
            req_raw = await ws.recv()
            req = json.loads(req_raw)
            assert req["v"] == WIRE_VERSION
            await ws.send(json.dumps({
                "v": WIRE_VERSION, "id": req["id"], "type": "response",
                "ok": True, "result": {},
            }))

        resp_task = asyncio.create_task(respond())
        result = await bridge.call("list_tabs", {})
        await resp_task

        # The outbound request envelope is verified inside mock_responder via req["v"]
        # The auth_result already confirmed v:1 is present on server messages.
        assert auth_result["v"] == WIRE_VERSION

        await ws.close()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_origin_validation(tmp_path: Path, tmp_heare: Path) -> None:
    """WS handshake without chrome-extension:// origin is rejected (403)."""
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        with pytest.raises(Exception):
            # Should fail at the HTTP upgrade (403) or connection closed
            ws = await connect(
                f"ws://127.0.0.1:{port}",
                additional_headers={"Origin": "http://localhost:3000"},
            )
            await ws.recv()

        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_lonely_watcher_debounce(
    tmp_path: Path, tmp_heare: Path, monkeypatch
) -> None:
    """A reconnect within the debounce window does NOT mint a new pair code,
    but a sustained lonely period does. Monkey-patches the threshold to 2.0 s
    so the test runs in ~3 s instead of ~35 s of real time.
    """
    monkeypatch.setattr(
        "src.agent.browser_bridge.PAIR_CODE_REGEN_AFTER_LONELY_S", 2.0
    )

    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        # Auth clears any startup pair code and resets the lonely timer.
        ws = await _auth_client(port, "tok")
        assert bridge.connected is True
        assert bridge._pair_code is None

        # Disconnect: lonely timer starts ticking from now.
        await ws.close()
        await asyncio.sleep(0.1)  # let server observe the close
        assert bridge.connected is False
        disconnect_t = time.monotonic()

        # Within the 2.0 s threshold: no new code yet.
        await asyncio.sleep(1.0)
        assert bridge._pair_code is None, (
            "pair code minted during the debounce window"
        )

        # Past the threshold: watcher should have generated a code.
        # Watcher polls every 1.0 s, so allow up to ~3.5 s total.
        deadline = disconnect_t + 3.5
        while time.monotonic() < deadline and bridge._pair_code is None:
            await asyncio.sleep(0.1)
        assert bridge._pair_code is not None, (
            "pair code never minted past the debounce window"
        )

        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_activate_tab_rpc_round_trip(
    tmp_path: Path, tmp_heare: Path
) -> None:
    """`bridge.call('activate_tab', ...)` round-trips a request/response with
    the mock client. Guards the wiring of the activate_browser_tab tool added
    after the offscreen migration — there was no Python test for it before.
    """
    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        ws = await _auth_client(port, "tok")

        async def mock_responder():
            raw = await ws.recv()
            req = json.loads(raw)
            assert req["type"] == "request"
            assert req["method"] == "activate_tab"
            assert req["params"] == {"tab_id": 42}
            await ws.send(json.dumps({
                "v": WIRE_VERSION,
                "id": req["id"],
                "type": "response",
                "ok": True,
                "result": {"tab_id": 42, "url": "https://example.com", "title": "Example"},
            }))

        responder = asyncio.create_task(mock_responder())
        result = await bridge.call("activate_tab", {"tab_id": 42})
        await responder

        assert result["success"] is True
        assert result["result"]["tab_id"] == 42

        await ws.close()
        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_rapid_cycling_no_pair_codes(
    tmp_path: Path, tmp_heare: Path, monkeypatch
) -> None:
    """Rapid connect/disconnect cycling — each shorter than the debounce
    threshold — must not produce a pair code, because every reconnect
    resets `_lonely_since` and the watcher's `lonely_for` never reaches
    the threshold.
    """
    monkeypatch.setattr(
        "src.agent.browser_bridge.PAIR_CODE_REGEN_AFTER_LONELY_S", 2.0
    )

    port = _free_port()
    settings = _settings(tmp_path, token="tok", port=port)

    with patch("src.agent.browser_bridge.HEARE_HOME", tmp_heare):
        bridge = BrowserBridge(settings)
        task = asyncio.create_task(bridge.start())
        await asyncio.sleep(0.05)

        # Authenticate immediately so the startup lonely timer is cleared
        # before it can trigger a pair-code generation.
        first_ws = await _auth_client(port, "tok")
        await first_ws.close()
        await asyncio.sleep(0.1)

        # 5 cycles, each disconnect window is well below the 2.0 s threshold.
        for _ in range(5):
            ws = await _auth_client(port, "tok")
            await asyncio.sleep(0.5)
            await ws.close()
            await asyncio.sleep(0.5)
            assert bridge._pair_code is None, (
                "pair code minted during rapid cycling"
            )

        await bridge.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
