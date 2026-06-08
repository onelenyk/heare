"""Browser-extension bridge — token-authenticated WebSocket server on
127.0.0.1 that the Heare MV3 Chrome extension dials into.

See `.omc/plans/browser-extension-bridge.md` for the wire protocol and
threat model. The bridge accepts at most one authenticated connection at
a time; second clients get close code 4002. Wrong-token clients get
close code 4001. Origin headers must start with `chrome-extension://`.

All wire messages include `"v": 1`. All disconnect/timeout/not-connected
errors returned from `call()` are structured `{success, error, retryable}`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
import time
import uuid
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from src.config import HEARE_HOME, Settings, write_browser_bridge_token
from src.daemon.events import emit

logger = logging.getLogger("heare.browser_bridge")

WIRE_VERSION = 1
DEFAULT_RPC_TIMEOUT = 5.0
LONG_RPC_TIMEOUT = 15.0  # screenshot, wait_for
LONG_METHODS = frozenset({"screenshot", "wait_for"})

CLOSE_AUTH_FAILED = 4001
CLOSE_ALREADY_CONNECTED = 4002

PAIR_CODE_TTL_S = 60.0
PAIR_CODE_REGEN_AFTER_LONELY_S = 30.0
PAIR_MAX_ATTEMPTS = 5
PAIR_CODE_DIGITS = 6

ERR_DISCONNECTED_MID_RPC = {
    "success": False,
    "error": "Browser extension disconnected while processing request. "
    "Try again in a few seconds.",
    "retryable": True,
}
ERR_TIMEOUT = {
    "success": False,
    "error": "Browser extension timed out. The request may still be processing.",
    "retryable": True,
}
ERR_NOT_CONNECTED = {
    "success": False,
    "error": "Browser not connected. Install the Heare Bridge extension from "
    "extensions/heare-bridge/ via chrome://extensions.",
    "retryable": False,
}


def _err_remote(message: str, retryable: bool = False) -> dict:
    return {"success": False, "error": message, "retryable": retryable}


class BrowserBridge:
    """Single-connection WS server that brokers RPC between the daemon and
    the Heare Chrome extension."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._port = settings.browser_bridge_port
        self._token = settings.browser_bridge_token
        self._pending: dict[str, asyncio.Future[dict]] = {}
        self._ws: ServerConnection | None = None
        self._authed: bool = False
        self._server: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._status_path = HEARE_HOME / "browser_bridge.status"
        self._token_path = HEARE_HOME / "browser_bridge.token"
        self._pong_tasks: set[asyncio.Task] = set()
        # Pair-mode state — see "WPS-style pairing" in plan.
        self._pair_code: str | None = None
        self._pair_expires_at: float | None = None
        self._pair_attempts: int = 0
        self._lonely_since: float | None = None
        self._lonely_task: asyncio.Task | None = None
        self._last_auth_fail_log: float = 0.0

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def start(self) -> None:
        """Bind the server, ensure a token exists, write the convenience
        token file + status file, then run forever."""
        await self._ensure_token()
        self._write_token_file()
        self._lonely_since = time.time()
        self._write_status(False)
        self._lonely_task = asyncio.create_task(self._lonely_watcher())

        async def _process_request(
            connection: ServerConnection,
            request: Request,
        ) -> Response | None:
            origin = request.headers.get("Origin", "") or request.headers.get(
                "origin", ""
            )
            if not origin.startswith("chrome-extension://"):
                logger.warning(
                    "browser_bridge handshake rejected: bad origin=%r",
                    origin,
                )
                return connection.respond(403, "Forbidden: invalid Origin\n")
            return None

        self._server = await serve(
            self._handle_connection,
            host="127.0.0.1",
            port=self._port,
            process_request=_process_request,
            ping_interval=20,
            ping_timeout=20,
        )
        logger.info(
            "browser_bridge listening on 127.0.0.1:%d (token persisted in config.toml)",
            self._port,
        )
        try:
            await self._server.serve_forever()
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Close the server, drop the active client, fail outstanding RPCs.

        Idempotent: safe to call multiple times. Returns early if both the
        server and active websocket are already cleared.
        """
        if self._server is None and self._ws is None and self._lonely_task is None:
            return
        if self._lonely_task is not None:
            self._lonely_task.cancel()
            try:
                await self._lonely_task
            except (asyncio.CancelledError, Exception):
                pass
            self._lonely_task = None
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._fail_pending(ERR_DISCONNECTED_MID_RPC)
        self._authed = False
        self._write_status(False)

    # ── connection ────────────────────────────────────────────────────────
    async def _handle_connection(self, ws: ServerConnection) -> None:
        if self._ws is not None:
            # Refuse the second client. With the offscreen-document architecture
            # the WS no longer dies on SW suspension, so a second connection
            # almost always means a second Chrome profile competing for the
            # same daemon — accepting it would create a kick-war.
            logger.warning("browser_bridge refusing second client (close 4002)")
            try:
                await ws.close(code=CLOSE_ALREADY_CONNECTED, reason="already connected")
            except Exception:
                pass
            return

        # Auth handshake — first message must be {"type":"auth","token":...}.
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        except (asyncio.TimeoutError, ConnectionClosed):
            return
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            await ws.close(code=CLOSE_AUTH_FAILED, reason="bad auth")
            return
        if not isinstance(msg, dict):
            await ws.close(code=CLOSE_AUTH_FAILED, reason="bad auth")
            return

        mtype = msg.get("type")
        if mtype == "pair":
            if not await self._handle_pair_message(ws, msg):
                return
        elif mtype == "auth":
            if not secrets.compare_digest(str(msg.get("token", "")), self._token):
                now = time.time()
                if now - self._last_auth_fail_log > 300:  # once per 5 minutes
                    logger.warning(
                        "browser_bridge auth failed (close 4001) — suppressing repeats for 5min"
                    )
                    self._last_auth_fail_log = now
                await ws.close(code=CLOSE_AUTH_FAILED, reason="bad auth")
                return
        else:
            await ws.close(code=CLOSE_AUTH_FAILED, reason="bad auth")
            return

        self._ws = ws
        self._authed = True
        self._clear_pair_code()
        self._lonely_since = None
        self._write_status(True)
        try:
            if mtype == "pair":
                await ws.send(
                    json.dumps(
                        {
                            "v": WIRE_VERSION,
                            "type": "pair_result",
                            "ok": True,
                            "token": self._token,
                        }
                    )
                )
            else:
                await ws.send(
                    json.dumps(
                        {
                            "v": WIRE_VERSION,
                            "type": "auth_result",
                            "ok": True,
                        }
                    )
                )
        except ConnectionClosed:
            self._authed = False
            self._ws = None
            self._lonely_since = time.time()
            self._write_status(False)
            return

        logger.info("browser_bridge client authenticated")
        emit("browser", "connected", level="info")

        try:
            async for raw in ws:
                self._handle_inbound(raw)
        except ConnectionClosed:
            pass
        finally:
            self._ws = None
            self._authed = False
            self._lonely_since = time.time()
            self._fail_pending(ERR_DISCONNECTED_MID_RPC)
            self._write_status(False)
            logger.info("browser_bridge client disconnected")

    def _handle_inbound(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("browser_bridge dropping malformed message")
            return
        if not isinstance(msg, dict):
            logger.warning("browser_bridge dropping non-object message")
            return
        mtype = msg.get("type")
        if mtype == "response":
            req_id = msg.get("id")
            fut = self._pending.pop(req_id, None) if isinstance(req_id, str) else None
            if fut is None or fut.done():
                return
            if msg.get("ok"):
                fut.set_result({"success": True, "result": msg.get("result", {})})
            else:
                err = msg.get("error", {}) or {}
                code = err.get("code") if isinstance(err, dict) else None
                emsg = (
                    err.get("message") if isinstance(err, dict) else None
                ) or "remote error"
                fut.set_result(_err_remote(f"{code or 'ERROR'}: {emsg}"))
            return
        if mtype == "ping":
            ws = self._ws
            if ws is not None:
                task = asyncio.create_task(self._send_pong(ws))
                self._pong_tasks.add(task)
                task.add_done_callback(self._pong_tasks.discard)
            return
        if mtype == "pong":
            return
        logger.warning("browser_bridge dropping unknown message type=%r", mtype)

    async def _send_pong(self, ws: ServerConnection) -> None:
        try:
            await ws.send(json.dumps({"v": WIRE_VERSION, "type": "pong"}))
        except ConnectionClosed:
            pass

    # ── public API ────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._authed and self._ws is not None

    async def call(
        self,
        method: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        """Send an RPC and await the response. Returns either
        `{"success": True, "result": ...}` or a structured error dict
        with `retryable` set.
        """
        if not self.connected or self._ws is None:
            return dict(ERR_NOT_CONNECTED)

        if timeout is None:
            timeout = (
                LONG_RPC_TIMEOUT if method in LONG_METHODS else DEFAULT_RPC_TIMEOUT
            )

        req_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        self._pending[req_id] = fut

        envelope = {
            "v": WIRE_VERSION,
            "id": req_id,
            "type": "request",
            "method": method,
            "params": params or {},
        }
        tab_id = (params or {}).get("tab_id")
        started = time.monotonic()
        try:
            await self._ws.send(json.dumps(envelope))
        except ConnectionClosed:
            self._pending.pop(req_id, None)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "browser_bridge rpc method=%s tab=%s elapsed_ms=%d ok=False",
                method,
                tab_id,
                elapsed_ms,
            )
            return dict(ERR_DISCONNECTED_MID_RPC)

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "browser_bridge rpc method=%s tab=%s elapsed_ms=%d ok=False",
                method,
                tab_id,
                elapsed_ms,
            )
            return dict(ERR_TIMEOUT)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        ok = bool(result.get("success"))
        logger.info(
            "browser_bridge rpc method=%s tab=%s elapsed_ms=%d ok=%s",
            method,
            tab_id,
            elapsed_ms,
            ok,
        )
        return result

    # ── helpers ───────────────────────────────────────────────────────────
    async def _ensure_token(self) -> None:
        if self._token:
            return
        token = secrets.token_urlsafe(32)
        write_browser_bridge_token(self._settings, token)
        self._token = token
        logger.info("browser_bridge generated new token (persisted to config.toml)")

    def _write_token_file(self) -> None:
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._token_path.with_suffix(self._token_path.suffix + ".tmp")
            tmp.write_text(self._token + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._token_path)
            os.chmod(self._token_path, 0o600)
        except OSError as exc:
            logger.warning("browser_bridge could not write token file: %s", exc)

    def _write_status(self, connected: bool) -> None:
        try:
            self._status_path.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            pair_remaining = 0.0
            if self._pair_expires_at is not None:
                pair_remaining = max(0.0, self._pair_expires_at - now)
            payload = json.dumps(
                {
                    "connected": bool(connected),
                    "ts": now,
                    "port": self._port,
                    "pair_code": self._pair_code,
                    "pair_remaining_s": pair_remaining if self._pair_code else None,
                }
            )
            tmp = self._status_path.with_suffix(self._status_path.suffix + ".tmp")
            tmp.write_text(payload)
            os.replace(tmp, self._status_path)
        except OSError as exc:
            logger.debug("browser_bridge status write failed: %s", exc)

    # ── pairing ───────────────────────────────────────────────────────────
    async def _handle_pair_message(self, ws: ServerConnection, msg: dict) -> bool:
        """Validate a pair handshake. Returns True if pair accepted (caller
        proceeds to mark connection authed); False if rejected (connection
        already closed)."""
        code = str(msg.get("code", ""))
        active_code = self._pair_code
        expires_at = self._pair_expires_at
        now = time.time()
        if (
            not active_code
            or expires_at is None
            or now >= expires_at
            or len(code) != PAIR_CODE_DIGITS
        ):
            logger.warning("browser_bridge pair rejected (no/expired code)")
            try:
                await ws.send(
                    json.dumps(
                        {
                            "v": WIRE_VERSION,
                            "type": "pair_result",
                            "ok": False,
                            "error": "no active pair code",
                        }
                    )
                )
            except ConnectionClosed:
                pass
            await ws.close(code=CLOSE_AUTH_FAILED, reason="bad pair")
            return False
        if not secrets.compare_digest(code, active_code):
            self._pair_attempts += 1
            logger.warning(
                "browser_bridge pair wrong code (attempt %d/%d)",
                self._pair_attempts,
                PAIR_MAX_ATTEMPTS,
            )
            if self._pair_attempts >= PAIR_MAX_ATTEMPTS:
                self._clear_pair_code()
            try:
                await ws.send(
                    json.dumps(
                        {
                            "v": WIRE_VERSION,
                            "type": "pair_result",
                            "ok": False,
                            "error": "wrong code",
                        }
                    )
                )
            except ConnectionClosed:
                pass
            await ws.close(code=CLOSE_AUTH_FAILED, reason="bad pair")
            return False
        return True

    async def _lonely_watcher(self) -> None:
        """While no extension is connected, refresh a fresh pair code every
        time the previous one expires (and the very first time we hit the
        lonely threshold). Cancelled cleanly on stop()."""
        try:
            while True:
                await asyncio.sleep(1.0)
                if self._ws is not None:
                    continue
                now = time.time()
                if self._lonely_since is None:
                    self._lonely_since = now
                    continue
                lonely_for = now - self._lonely_since
                code_active = (
                    self._pair_code is not None
                    and self._pair_expires_at is not None
                    and now < self._pair_expires_at
                )
                if code_active:
                    continue
                if lonely_for < PAIR_CODE_REGEN_AFTER_LONELY_S:
                    continue
                self._generate_pair_code()
                self._write_status(False)
        except asyncio.CancelledError:
            return

    def _generate_pair_code(self) -> None:
        digits = "".join(secrets.choice("0123456789") for _ in range(PAIR_CODE_DIGITS))
        self._pair_code = digits
        self._pair_expires_at = time.time() + PAIR_CODE_TTL_S
        self._pair_attempts = 0
        copied = _copy_to_clipboard(digits)
        logger.info(
            "browser_bridge pair code %s (60s TTL, clipboard=%s)",
            digits,
            "ok" if copied else "skipped",
        )

    def _clear_pair_code(self) -> None:
        self._pair_code = None
        self._pair_expires_at = None
        self._pair_attempts = 0

    def _fail_pending(self, error: dict) -> None:
        if not self._pending:
            return
        for req_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result(dict(error))
        self._pending.clear()


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    try:
        import platform

        system = platform.system()
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
        elif system == "Linux":
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode(),
                check=True,
                capture_output=True,
            )
            return True
        return False
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


# ── module-level singleton wiring used by direct.py ──────────────────────
_bridge: BrowserBridge | None = None


def set_bridge(b: BrowserBridge | None) -> None:
    global _bridge
    _bridge = b


def _get_bridge() -> BrowserBridge | None:
    return _bridge
