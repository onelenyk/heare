"""Minimal HTTP API for daemon control — backs the web frontend.

Authentication
--------------
Every route here requires a token. It is generated on first run into
``~/.heare/api_token`` (mode 0600) and read into ``settings.api_token`` by
``load_settings()``. Send it one of two ways::

    curl -H "Authorization: Bearer $(cat ~/.heare/api_token)" \\
         http://127.0.0.1:9780/state

    # GET only — so EventSource, which cannot set headers, still works
    curl "http://127.0.0.1:9780/state?token=$(cat ~/.heare/api_token)"

Any script, shortcut or menu-bar helper that talks to this API must send
it; without it the answer is 401 and nothing happens.

Two things are exempt, because the page has to load before it can read
the token: ``GET /`` (the SPA shell, which is served the token inline as
``window.__HEARE_TOKEN__``) and the static assets under ``/assets/`` and
``/static/``. ``GET /api/health`` is exempt too, if it ever exists.

The token alone is not enough. A page on another origin can be made to
carry credentials, so a request arriving with an ``Origin`` header whose
host is not 127.0.0.1/localhost is refused with 403 even when the token
is correct — that cross-origin POST, reaching ``/inject`` and from there
the assistant and its shell tool, is the attack this exists to close.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import signal
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_INDEX_HTML: str | None = None

# Requests that must pass without a token, because they are what puts the
# token in the browser's hands in the first place. GET only — POST / is a
# form that saves API keys, and is no more exempt than any other write.
_EXEMPT_PATHS = frozenset({"/", "/api/health", "/favicon.ico"})
_EXEMPT_PREFIXES = ("/assets/", "/static/")

_LOCAL_ORIGIN_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _origin_is_local(origin: str) -> bool:
    """True if `origin` names this machine's loopback interface.

    Anything unparseable — including the literal ``null`` a sandboxed
    frame sends — is not local.
    """
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return host in _LOCAL_ORIGIN_HOSTS


def _bearer_token(request) -> str:
    """The token out of ``Authorization: Bearer <token>``, or ""."""
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        return value.strip()
    return ""


def _inject_token_html(html: str, token: str) -> str:
    """Return `html` with ``window.__HEARE_TOKEN__`` defined before </head>.

    This is how the dashboard bootstraps: it is served from this daemon,
    so the token can ride along in the document instead of asking the
    user to paste one. Idempotent, and a no-op without a token.
    """
    if not token or "__HEARE_TOKEN__" in html:
        return html
    tag = f'<script>window.__HEARE_TOKEN__ = "{token}";</script>'
    cut = html.lower().find("</head>")
    if cut == -1:
        return tag + html
    return html[:cut] + tag + html[cut:]

from aiohttp import web  # noqa: E402
from src.agent.identity import load_identity, save_identity, regenerate_identity  # noqa: E402
from src.agent.llm.providers import (
    PROVIDERS,
    get_available,
    get_config,
    list_models,
    make_identity_bootstrap,
)  # noqa: E402
from src.agent.modes import VALID_MODES  # noqa: E402
from src.config import (  # noqa: E402
    HEARE_HOME,
    api_key_fields,
    backup_session_file,
    set_capability_install_enabled,
    write_browser_bridge_token,
    write_config_toml_values,
    write_env_updates,
)
from src.memory.base import MemoryType  # noqa: E402
from src.spine.features import FEATURES  # noqa: E402
from src.dashboard_data import (  # noqa: E402
    counts,
    daemon_status,
    fetch_usage,
    open_db,
)
from src.version import app_version  # noqa: E402


def tail_lines(
    path: Path,
    count: int,
    *,
    window: int = 64 * 1024,
    max_bytes: int = 1_000_000,
) -> list[str]:
    """Return the last ``count`` lines of ``path`` without reading it whole.

    The daemon log rotates at 10 MB, so ``read_text()`` here meant parsing
    millions of characters to serve a handful of lines on every poll.

    The window grows when the tail turns out to be long-lined — the log
    carries multi-KB single lines (full system-prompt dumps), so a fixed
    window can come back with fewer lines than asked for. Reading is capped
    at ``max_bytes`` so a pathological file can't undo the point of this.

    ``window`` is the initial read size; it is a parameter so the growth path
    can be exercised without writing a multi-megabyte fixture.
    """
    while True:
        # Reopened each pass so a rotation between attempts can't leave us
        # reading from a handle pointing at the unlinked old file.
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - window)
            f.seek(start)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        if start > 0:
            # The window almost certainly cut a line in half — drop it.
            text = text.split("\n", 1)[-1]
        lines = text.splitlines()
        if len(lines) >= count or start == 0 or window >= max_bytes:
            return lines[-count:]
        window *= 4


class _TomlInline(int):
    """A value ``write_config_toml_values`` writes out verbatim.

    That writer is the one place that edits config.toml without dropping
    ``[section]`` headers or comments, and it is worth reusing — but its
    ``_toml_literal`` only knows bools, numbers and strings. A dict falls
    through to the string branch and lands in the file as
    ``spine_features = "{'roles': False}"``: valid TOML, a *string*, and
    silently ignored by ``features.resolve``, which requires a dict. So
    the toggle would appear to save and change nothing.

    Numbers are the one kind the writer emits through ``repr()``, so an
    int subclass whose ``repr`` is the inline table gets a real table into
    the file while the careful part of the writer — preamble only, tables
    and comments copied byte-for-byte, atomic replace — is reused rather
    than re-implemented here. (The tidier fix is a dict branch in
    ``_toml_literal``; this module cannot make it.)
    """

    def __new__(cls, literal: str):
        obj = super().__new__(cls, 0)
        obj._literal = literal
        return obj

    def __repr__(self) -> str:
        return self._literal


def _render_feature_table(table: dict[str, bool]) -> str:
    """A TOML inline table for ``spine_features``, in declaration order."""
    parts = [
        f"{f.name} = {'true' if table[f.name] else 'false'}"
        for f in FEATURES
        if f.name in table
    ]
    return "{ " + ", ".join(parts) + " }" if parts else "{}"


def _clamp_int(raw: str | None, default: int, low: int, high: int) -> int:
    """Parse a query-string integer, falling back to ``default`` if unusable."""
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


class API:
    def __init__(self, state, config, daemon_control=None, memory_backend=None):
        self.state = state
        self.config = config
        self._control = daemon_control  # (start_fn, stop_fn, restart_fn)
        self._memory_backend = memory_backend
        self._token = self._resolve_token()
        self._app = web.Application(middlewares=[self._auth_middleware])
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
        self._app.router.add_get("/state", self._handle_state)
        self._app.router.add_post("/state", self._handle_state_post)
        self._app.router.add_post("/mode", self._handle_mode)
        self._app.router.add_post("/mute", self._handle_mute)
        self._app.router.add_post("/interrupt", self._handle_interrupt)
        self._app.router.add_post("/provider", self._handle_provider)
        self._app.router.add_post("/model", self._handle_model)
        self._app.router.add_get("/api/providers", self._handle_providers_list)
        self._app.router.add_get("/api/models", self._handle_models_live)
        self._app.router.add_post("/cancel", self._handle_cancel)
        self._app.router.add_get("/activity", self._handle_activity)
        self._app.router.add_get("/logs", self._handle_logs)
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_post("/", self._handle_index)
        self._app.router.add_get("/display", self._handle_display)
        self._app.router.add_get("/events", self._handle_events)
        self._app.router.add_get("/canvas", self._handle_display)
        self._app.router.add_post("/daemon", self._handle_daemon)
        self._app.router.add_post("/inject", self._handle_inject)
        self._app.router.add_get("/settings/status", self._handle_settings_status)
        self._app.router.add_post("/settings", self._handle_settings)
        self._app.router.add_post(
            "/api/settings/allow-installs", self._handle_allow_installs
        )
        self._app.router.add_post(
            "/api/settings/reset-session", self._handle_reset_session
        )
        self._app.router.add_get("/api/features", self._handle_features)
        self._app.router.add_post("/api/features", self._handle_features_set)
        self._app.router.add_get("/mic/status", self._handle_mic_status)
        self._app.router.add_get("/api/audio-devices", self._handle_audio_devices)
        self._app.router.add_get("/api/tools", self._handle_tools)
        self._app.router.add_post(
            "/api/audio-devices/select", self._handle_audio_device_select
        )
        self._app.router.add_get("/api/bridge/status", self._handle_bridge_status)
        self._app.router.add_get("/api/bridge/token", self._handle_bridge_token)
        self._app.router.add_post(
            "/api/bridge/rotate-token", self._handle_bridge_rotate_token
        )
        self._app.router.add_post("/api/bridge/toggle", self._handle_bridge_toggle)
        self._app.router.add_get("/api/prompts", self._handle_prompts)
        self._app.router.add_get("/api/prompts/preview", self._handle_prompt_preview)
        self._app.router.add_get("/api/prompts/{key}", self._handle_prompt_section)
        self._app.router.add_post("/api/prompts/{key}", self._handle_prompt_save)
        self._app.router.add_get("/api/agents", self._handle_agents)
        self._app.router.add_post("/api/agents/start", self._handle_agents_start)
        self._app.router.add_post("/api/agents/cancel", self._handle_agents_cancel)
        self._app.router.add_get(
            "/api/agents/{session_id}/result", self._handle_agents_result
        )
        self._app.router.add_get("/api/setup", self._handle_setup_state)
        self._app.router.add_post("/api/setup/identity", self._handle_setup_identity)
        self._app.router.add_post(
            "/api/setup/identity/regenerate", self._handle_setup_identity_regenerate
        )
        self._app.router.add_post("/api/setup/config", self._handle_settings)
        self._app.router.add_post("/api/setup/complete", self._handle_setup_complete)
        self._app.router.add_get("/api/memories", self._handle_memories)
        self._app.router.add_get("/api/memories/stats", self._handle_memories_stats)
        self._app.router.add_post(
            "/api/memories/{id}/forget", self._handle_memories_forget
        )
        self._app.router.add_get("/api/artifacts", self._handle_artifacts_list)
        self._app.router.add_get(
            "/api/artifacts/{name}", self._handle_artifacts_get
        )
        self._runner = None
        self._site = None

    def register_routes(self, app: "web.Application") -> None:
        """Mount all API routes on an existing aiohttp app.

        Used when the API is embedded in another server (e.g. menubar Hub).
        The standalone ``start()`` method still creates its own app.
        """
        self._app = app
        # The host app owns the request chain, so authentication has to be
        # installed here as well — this is the app that actually listens on
        # 9780. Appending is only legal before the app starts, which is
        # where both callers (src/main.py, src/menubar.py) invoke this.
        if self._auth_middleware not in app.middlewares:
            app.middlewares.append(self._auth_middleware)
        self._app.router.add_get("/state", self._handle_state)
        self._app.router.add_post("/state", self._handle_state_post)
        self._app.router.add_post("/mode", self._handle_mode)
        self._app.router.add_post("/mute", self._handle_mute)
        self._app.router.add_post("/interrupt", self._handle_interrupt)
        self._app.router.add_post("/provider", self._handle_provider)
        self._app.router.add_post("/model", self._handle_model)
        self._app.router.add_get("/api/providers", self._handle_providers_list)
        self._app.router.add_get("/api/models", self._handle_models_live)
        self._app.router.add_post("/cancel", self._handle_cancel)
        self._app.router.add_get("/activity", self._handle_activity)
        self._app.router.add_get("/logs", self._handle_logs)
        self._app.router.add_get("/display", self._handle_display)
        self._app.router.add_get("/events", self._handle_events)
        self._app.router.add_get("/canvas", self._handle_display)
        self._app.router.add_post("/daemon", self._handle_daemon)
        self._app.router.add_post("/inject", self._handle_inject)
        self._app.router.add_get("/settings/status", self._handle_settings_status)
        self._app.router.add_post("/settings", self._handle_settings)
        self._app.router.add_post(
            "/api/settings/allow-installs", self._handle_allow_installs
        )
        self._app.router.add_post(
            "/api/settings/reset-session", self._handle_reset_session
        )
        self._app.router.add_get("/api/features", self._handle_features)
        self._app.router.add_post("/api/features", self._handle_features_set)
        self._app.router.add_get("/mic/status", self._handle_mic_status)
        self._app.router.add_get("/api/audio-devices", self._handle_audio_devices)
        self._app.router.add_get("/api/tools", self._handle_tools)
        self._app.router.add_post(
            "/api/audio-devices/select", self._handle_audio_device_select
        )
        self._app.router.add_get("/api/bridge/status", self._handle_bridge_status)
        self._app.router.add_get("/api/bridge/token", self._handle_bridge_token)
        self._app.router.add_post(
            "/api/bridge/rotate-token", self._handle_bridge_rotate_token
        )
        self._app.router.add_post("/api/bridge/toggle", self._handle_bridge_toggle)
        self._app.router.add_get("/api/prompts", self._handle_prompts)
        self._app.router.add_get("/api/prompts/preview", self._handle_prompt_preview)
        self._app.router.add_get("/api/prompts/{key}", self._handle_prompt_section)
        self._app.router.add_post("/api/prompts/{key}", self._handle_prompt_save)
        self._app.router.add_get("/api/agents", self._handle_agents)
        self._app.router.add_post("/api/agents/start", self._handle_agents_start)
        self._app.router.add_post("/api/agents/cancel", self._handle_agents_cancel)
        self._app.router.add_get(
            "/api/agents/{session_id}/result", self._handle_agents_result
        )
        self._app.router.add_get("/api/setup", self._handle_setup_state)
        self._app.router.add_post("/api/setup/identity", self._handle_setup_identity)
        self._app.router.add_post(
            "/api/setup/identity/regenerate", self._handle_setup_identity_regenerate
        )
        self._app.router.add_post("/api/setup/config", self._handle_settings)
        self._app.router.add_post("/api/setup/complete", self._handle_setup_complete)
        self._app.router.add_get("/api/artifacts", self._handle_artifacts_list)
        self._app.router.add_get(
            "/api/artifacts/{name}", self._handle_artifacts_get
        )
        # Present in the constructor's table but missing here, so the
        # memories card 404'd under the menubar, which mounts this one.
        self._app.router.add_get("/api/memories", self._handle_memories)
        self._app.router.add_get(
            "/api/memories/stats", self._handle_memories_stats
        )
        self._app.router.add_post(
            "/api/memories/{id}/forget", self._handle_memories_forget
        )

    # ── Authentication ─────────────────────────────────────
    #
    # One middleware, not 48 decorated handlers: a route added later is
    # protected by having been added, which is the only arrangement that
    # stays true after the next feature lands.

    def _resolve_token(self) -> str:
        """The expected token, or "" when there is none to compare against.

        Reads ``config.api_token`` — populated by ``load_settings()``. When
        the config object is a real ``Settings`` that has not been through
        that path, generate/read the file here. Anything else (a test
        double, a stub) yields "" and every guarded route answers 401:
        no token means no access, never open access.
        """
        token = getattr(self.config, "api_token", "")
        if isinstance(token, str) and token:
            return token
        try:
            from src.config import Settings, ensure_api_token

            if isinstance(self.config, Settings):
                return ensure_api_token(self.config)
        except Exception:
            logger.exception("could not resolve the API token")
        return ""

    def _expected_token(self) -> str:
        if not self._token:
            self._token = self._resolve_token()
        return self._token

    @staticmethod
    def _is_exempt(request) -> bool:
        if request.method != "GET":
            return False
        path = request.path
        return path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES)

    @web.middleware
    async def _auth_middleware(self, request, handler):
        # Origin first, and on every path: a valid token carried by a page
        # on another origin is exactly the request being refused here.
        origin = request.headers.get("Origin")
        if origin and not _origin_is_local(origin):
            logger.warning(
                "refused %s %s from origin %s", request.method, request.path, origin
            )
            return web.json_response(
                {"ok": False, "error": "forbidden_origin"}, status=403
            )

        if self._is_exempt(request):
            response = await handler(request)
            return self._with_token_injected(response)

        supplied = _bearer_token(request)
        if not supplied and request.method == "GET":
            # Query token for GET only. EventSource cannot set headers, so
            # /events has no other way in; a POST always can, and letting a
            # URL carry a write credential is how tokens end up in logs and
            # <img src> tags.
            supplied = request.query.get("token", "")

        expected = self._expected_token()
        if not expected or not secrets.compare_digest(supplied, expected):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    def _with_token_injected(self, response):
        """Serve the token inside the SPA shell so the page can use it.

        Applied in the middleware rather than in one handler because three
        different servers serve this document — src/api.py standalone,
        src/main.py and src/menubar.py — and only the middleware is common
        to all of them.
        """
        if not isinstance(response, web.Response):
            return response
        if response.content_type != "text/html":
            return response
        try:
            body = response.text
        except (UnicodeDecodeError, AttributeError):
            return response
        if not body:
            return response
        injected = _inject_token_html(body, self._expected_token())
        if injected != body:
            response.text = injected
        return response

    async def start(self):
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 9778, reuse_address=True)
        try:
            await self._site.start()
        except OSError:
            logger.exception("non-fatal socket error during server start")

    async def stop(self):
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()

    # ── Handlers ───────────────────────────────────────────

    async def _handle_index(self, request):
        if request.method == "POST":
            post = await request.post()
            await self._save_form_keys(post)
            raise web.HTTPFound("/")
        global _INDEX_HTML
        if _INDEX_HTML is None:
            for candidate in [
                Path(__file__).resolve().parent.parent.parent
                / "src"
                / "frontend"
                / "index.html",
                Path(__file__).resolve().parent / "frontend" / "index.html",
                Path(__file__).resolve().parent.parent / "frontend" / "index.html",
            ]:
                if candidate.exists():
                    _INDEX_HTML = candidate.read_text()
                    break
            if _INDEX_HTML is None:
                return web.Response(text="Frontend not found", status=500)
        # Injected per response, not into the cache: the token can be
        # rotated under a running daemon.
        return web.Response(
            text=_inject_token_html(_INDEX_HTML, self._expected_token()),
            content_type="text/html",
        )

    # ── API keys ──────────────────────────────────────────────────
    #
    # Four surfaces can set a key (settings card, brain card, setup modal,
    # the plain HTML form). They all funnel through the three helpers below
    # so validation, persistence and hot-apply behave identically no matter
    # which one the user reached for.

    @staticmethod
    def _normalise_key_payload(body) -> dict[str, str]:
        """Pick the API keys out of `body`, keyed by Settings attribute name.

        Accepts either spelling — ``deepseek_api_key`` (what the dashboard
        sends) or ``DEEPSEEK_API_KEY`` (what the setup modal sends) — because
        both are already in use by shipped frontends. Unknown names and blank
        values are dropped.
        """
        fields = api_key_fields()
        env_to_attr = {env: attr for attr, env in fields.items()}
        found: dict[str, str] = {}
        for raw_name, raw_value in (body or {}).items():
            if not isinstance(raw_value, str):
                continue
            value = raw_value.strip()
            if not value:
                continue
            if raw_name in fields:
                found[raw_name] = value
            elif raw_name.upper() in env_to_attr:
                found[env_to_attr[raw_name.upper()]] = value
        return found

    @staticmethod
    def _validate_api_keys(keys: dict[str, str]) -> list[str]:
        """Return human-readable complaints about `keys`; empty means good."""
        errors = []
        groq = keys.get("groq_api_key", "")
        if groq and not (groq.startswith("gsk_") or groq.startswith("sk-")):
            errors.append("Groq key must start with gsk_ or sk-")
        for attr, value in keys.items():
            if attr != "groq_api_key" and len(value) < 20:
                errors.append(f"{attr.replace('_', ' ').title()} key looks too short")
        return errors

    async def _apply_api_keys(self, keys: dict[str, str]) -> None:
        """Persist `keys` and apply them to the running daemon.

        Writes ~/.heare/.env (durable), os.environ + Settings (this process),
        and the ``key_<attr>`` state entries that SwitchableLLMService watches
        to rebuild its delegate — that last one is what makes a pasted key
        take effect without a restart.
        """
        if not keys:
            return
        fields = api_key_fields()
        write_env_updates({fields[attr]: value for attr, value in keys.items()})
        for attr, value in keys.items():
            os.environ[fields[attr]] = value
            setattr(self.config, attr, value)
            if self.state is not None:
                await self.state.set(f"key_{attr}", value)

    async def _save_form_keys(self, post):
        keys = self._normalise_key_payload(dict(post))
        if keys and not self._validate_api_keys(keys):
            await self._apply_api_keys(keys)

    def _bridge_connected(self) -> bool:
        """True only when a Chrome extension is actually attached.

        This used to read ``browser_bridge_enabled and token file exists``
        — two things that are true on a machine where the bridge has never
        run, so the header said "chrome connected" forever. Neither is
        evidence: the switch is an intention and the token file survives
        any process that wrote it.

        The bridge registers itself in-process (``set_bridge`` in
        src/main.py) and knows whether a client authenticated
        (``BrowserBridge.connected``). That object is the only local
        evidence there is, so no object means not connected — which is the
        honest answer on the spine engine, where the bridge is never
        started at all. A daemon in *another* process would also read
        false here; the status file it writes cannot tell "connected" from
        "was connected when it died", so it is deliberately not consulted.
        """
        try:
            from src.agent.browser_bridge import _get_bridge

            bridge = _get_bridge()
            if bridge is None:
                return False
            return bool(bridge.connected)
        except Exception:
            return False

    def _mcp_status_payload(self) -> dict | None:
        """The engine's ``mcp_status`` State key, parsed, or None.

        None means "this engine never said" (the pipecat path does not
        write the key) — which the UI must show as unknown rather than as
        an outage. The engine writes ``ok`` plus an ``error`` of "off" when
        the feature switch is off, so a reader can tell a switched-off MCP
        from a failed one.
        """
        if self.state is None:
            return None
        try:
            raw = self.state.get("mcp_status", "")
            if not isinstance(raw, str) or not raw:
                return None
            status = json.loads(raw)
        except Exception:
            return None
        if not isinstance(status, dict):
            return None
        servers = status.get("servers")
        try:
            tools = int(status.get("tools") or 0)
        except (TypeError, ValueError):
            tools = 0
        try:
            ts = float(status.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        return {
            "servers": [str(s) for s in servers] if isinstance(servers, list) else [],
            "tools": tools,
            "ok": bool(status.get("ok")),
            "error": str(status.get("error") or ""),
            "ts": ts,
        }

    async def _handle_state(self, request):
        if self.state is None:
            return web.json_response({"running": False})
        data = self.state.snapshot()
        # The raw key values live in state because that is how a hot-swapped
        # key reaches the LLM service, but this endpoint is unauthenticated —
        # never ship the secret itself. Callers only ever need "is it set?".
        for attr in api_key_fields():
            name = f"key_{attr}"
            if name in data:
                data[name] = bool(data[name])
        data["version"] = app_version(include_sha=False)
        data["providers"] = self._available_providers()

        # Models for current provider
        try:
            provider_key = data.get("provider", "")
            if provider_key in PROVIDERS:
                cfg = get_config(provider_key)
                data["models"] = (
                    list(cfg.model_whitelist)
                    if cfg.model_whitelist
                    else [cfg.default_model]
                )
                # Active model = the per-provider override (set via /model,
                # hot-swapped by SwitchableLLMService) or the provider default.
                # The top-level "model" snapshot is written once at startup and
                # goes stale after a hot-swap, so derive the live value here.
                data["model"] = (
                    data.get(f"model_{provider_key}") or cfg.default_model
                )
            else:
                data["models"] = []
        except Exception:
            data["models"] = []

        # Chrome bridge status — see _bridge_connected for why this is not
        # derived from the config any more. `chrome_enabled` is the switch,
        # `chrome` is the fact; the header tells the two apart.
        data["chrome"] = self._bridge_connected()
        try:
            data["chrome_enabled"] = bool(self.config.browser_bridge_enabled)
        except Exception:
            data["chrome_enabled"] = False

        data["interrupt_enabled"] = not self.config.interrupt_enabled_file.exists()

        try:
            identity = load_identity(self.config.identity_file)
            if identity:
                data["agent"] = identity["name"]
                data["emoji"] = identity["emoji"]
        except Exception:
            pass

        try:
            # In-process mode: pipeline status comes from state key
            if self._control is not None and self.state:
                running_str = self.state.get("running", "false")
                data["running"] = running_str == "true"
                data["pid"] = os.getpid()
                data["uptime"] = ""
            else:
                running, pid, uptime = daemon_status(self.config)
                data["running"] = running
                data["pid"] = pid
                data["uptime"] = uptime
        except Exception:
            pass

        try:
            con = open_db(self.config.db_path)
            cnt = counts(con)
            data["transcripts_count"] = cnt.get("transcripts", 0)
            data["actions_count"] = cnt.get("actions", 0)
            usage = fetch_usage(con)
            data["usage"] = {
                "llm_calls": usage.llm_calls,
                "llm_input_tokens": usage.llm_input_tokens,
                "llm_output_tokens": usage.llm_output_tokens,
                "llm_cost_usd": usage.llm_cost_usd,
                "stt_calls": usage.stt_calls,
                "stt_audio_seconds": usage.stt_audio_seconds,
                "stt_cost_usd": usage.stt_cost_usd,
                "tts_calls": usage.tts_calls,
                "tts_char_count": usage.tts_char_count,
                "tts_cost_usd": usage.tts_cost_usd,
                "total_cost_usd": usage.total_cost_usd,
            }
            if con:
                con.close()
        except Exception:
            pass

        try:
            raw = self.state.get("voice_state", "")
            if raw:
                vs = json.loads(raw)
                data["voice_state"] = {
                    "state": vs.get("state", "idle"),
                    "since_ts": vs.get("since_ts", 0.0),
                    "last_partial": vs.get("last_partial"),
                    "last_final": vs.get("last_final"),
                }
            else:
                data["voice_state"] = {
                    "state": "idle",
                    "since_ts": 0.0,
                    "last_partial": None,
                    "last_final": None,
                }
        except Exception:
            pass

        try:
            raw = self.state.get("agent_state", "")
            if raw:
                ag = json.loads(raw)
                data["agent_state"] = {
                    "state": ag.get("state", "idle"),
                    "since_ts": ag.get("since_ts", 0.0),
                }
            else:
                data["agent_state"] = {
                    "state": "idle",
                    "since_ts": 0.0,
                }
        except Exception:
            pass

        try:
            import aiosqlite

            async with aiosqlite.connect(str(self.config.db_path)) as db:
                row = await db.execute_fetchall(
                    "SELECT text, agent_mode FROM transcripts "
                    "WHERE agent_spoken = 1 OR mode = 'assistant' "
                    "ORDER BY ts DESC LIMIT 1"
                )
                if row:
                    data["last_response"] = row[0][0]
                    data["last_response_mode"] = row[0][1]
        except Exception:
            pass

        return web.json_response(data)

    async def _handle_state_post(self, request):
        if self.state is None:
            return web.json_response(
                {"ok": False, "error": "daemon initializing"}, status=503
            )
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid JSON"}, status=400
            )
        key = body.get("key")
        value = body.get("value")
        if not key or value is None:
            return web.json_response(
                {"ok": False, "error": "key and value are required"}, status=400
            )
        await self.state.set(str(key), str(value))
        return web.json_response({"ok": True, "key": key, "value": str(value)})

    async def _handle_activity(self, request):
        limit = _clamp_int(request.query.get("limit"), 50, 1, 500)
        # The feed merges two append-only tables — transcripts ('said' rows)
        # and actions ('did' rows: the agent's tool calls). Their id sequences
        # are independent, so paging is keyed on ts, the only cursor comparable
        # across both. Composite ids ('t<id>' / 'a<id>') keep React keys stable
        # and unambiguous.
        raw_before = request.query.get("before_ts")
        try:
            before_ts = (
                float(raw_before) if raw_before not in (None, "") else 0.0
            )
        except (TypeError, ValueError):
            before_ts = 0.0
        try:
            import aiosqlite

            sql = (
                # who comes from agent_spoken, not from mode: the mode
                # column is polymorphic (the pipecat engine wrote
                # 'assistant' on agent rows, the spine writes 'spine' on
                # both), so reading authorship from it labelled every
                # spine row as the user.
                "SELECT rid, ts, who, content, source, kind, tool, status "
                "FROM ("
                "  SELECT 't'||id AS rid, ts, "
                "         CASE WHEN agent_spoken = 1 OR mode = 'assistant' "
                "              THEN 'bot' ELSE 'you' END AS who, "
                "         text AS content, "
                "         source, 'said' AS kind, NULL AS tool, NULL AS status "
                "  FROM transcripts "
                "  UNION ALL "
                "  SELECT 'a'||id AS rid, ts, NULL AS who, "
                "         COALESCE(tool || ': ' || args, tool, 'action') AS content, "
                "         NULL AS source, 'did' AS kind, tool, status "
                "  FROM actions"
                ") "
            )
            params: list = []
            if before_ts:
                sql += "WHERE ts < ? "
                params.append(before_ts)
            sql += "ORDER BY ts DESC LIMIT ?"
            params.append(limit)

            async with aiosqlite.connect(str(self.config.db_path)) as db:
                db.row_factory = aiosqlite.Row
                rows = await db.execute_fetchall(sql, tuple(params))

            out: list = []
            for r in rows:
                if r["kind"] == "did":
                    out.append(
                        {
                            "id": r["rid"],
                            "ts": r["ts"],
                            "who": "agent",
                            "type": "did",
                            "content": r["content"],
                            "tool": r["tool"],
                            "status": r["status"],
                        }
                    )
                else:
                    out.append(
                        {
                            "id": r["rid"],
                            "ts": r["ts"],
                            "who": r["who"] or "you",
                            "type": "said",
                            "content": r["content"],
                            # NULL predates the column; every turn logged back
                            # then did arrive by mic, so it reads as 'voice'.
                            "source": r["source"] or "voice",
                        }
                    )
            return web.json_response(out)
        except Exception:
            return web.json_response([], status=500)

    async def _handle_logs(self, request):
        log_file = self.config.log_dir / "daemon.log"
        if not log_file.exists():
            return web.json_response({"lines": []})
        limit = _clamp_int(request.query.get("limit"), 200, 1, 1000)
        try:
            lines = await asyncio.to_thread(tail_lines, log_file, limit)
            return web.json_response({"lines": lines})
        except Exception:
            return web.json_response({"lines": []})

    async def _handle_mode(self, request):
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"})
        body = await request.json()
        mode = body.get("mode", "focus")
        if mode not in VALID_MODES:
            return web.json_response({"ok": False}, status=400)
        await self.state.set("mode", mode)
        return web.json_response({"ok": True, "mode": mode})

    async def _handle_mute(self, request):
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"})
        body = await request.json()
        target = body.get("target", "speaker")
        key = "mute_mic" if target == "mic" else "mute_bot"
        current = self.state.get_bool(key)
        await self.state.set_bool(key, not current)
        return web.json_response({"ok": True, "target": target, "muted": not current})

    async def _handle_interrupt(self, request):
        if self.state is None:
            return web.json_response(
                {"ok": False, "error": "daemon initializing"}, status=503
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        enabled = body.get("enabled", True)
        if enabled:
            self.config.interrupt_enabled_file.unlink(missing_ok=True)
        else:
            self.config.interrupt_enabled_file.parent.mkdir(parents=True, exist_ok=True)
            self.config.interrupt_enabled_file.touch()
        await self.state.set_bool("interrupt_off", not enabled)
        return web.json_response({"ok": True, "enabled": enabled})

    async def _handle_provider(self, request):
        """POST /provider — choose which model answers.

        The state entry alone was not enough: the engine resolves its
        provider from ``Settings``, and Settings is loaded from config.toml.
        Writing only to state is how this switch came to change nothing at
        all — every reply still came from DeepSeek, because that was a
        constant in src/spine/llm.py and nothing ever consulted the choice.

        So all three: state (what the dashboard reads back), the live
        Settings (what the next turn resolves against), and config.toml
        (what survives a restart).
        """
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"})
        body = await request.json()
        provider = body.get("provider", "")
        if provider not in PROVIDERS:
            return web.json_response({"ok": False}, status=400)
        await self.state.set("provider", provider)
        self.config.llm_provider = provider
        write_config_toml_values({"llm_provider": provider})
        return web.json_response({"ok": True, "provider": provider})

    async def _handle_model(self, request):
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"})
        body = await request.json()
        provider = body.get("provider", "")
        model = body.get("model", "")
        if provider not in PROVIDERS or not model:
            return web.json_response({"ok": False}, status=400)
        await self.state.set(f"model_{provider}", model)
        setattr(self.config, f"{provider}_model", model)
        write_config_toml_values({f"{provider}_model": model})
        return web.json_response({"ok": True, "provider": provider, "model": model})

    async def _handle_providers_list(self, request):
        return web.json_response(
            [
                {
                    "key": cfg.key,
                    "display_name": cfg.display_name,
                    "dashboard_color": cfg.dashboard_color,
                    "default_model": cfg.default_model,
                    "configured": bool(getattr(self.config, cfg.api_key_attr, None)),
                }
                for cfg in PROVIDERS.values()
            ]
        )

    async def _handle_models_live(self, request):
        provider = request.query.get("provider", "")
        if provider not in PROVIDERS:
            return web.json_response({"ok": False}, status=400)
        cfg = get_config(provider)
        api_key = getattr(self.config, cfg.api_key_attr, None)
        fallback = list(cfg.model_whitelist) if cfg.model_whitelist else [cfg.default_model]
        if not api_key:
            return web.json_response(
                {"ok": True, "models": fallback, "source": "fallback", "reason": "no_key"}
            )
        try:
            models = await list_models(cfg, api_key)
            if not models:
                raise ValueError("empty model list")
            return web.json_response({"ok": True, "models": models, "source": "live"})
        except Exception as e:
            logger.warning("live model fetch failed for %s: %s", provider, e)
            return web.json_response(
                {"ok": True, "models": fallback, "source": "fallback", "reason": str(e)}
            )

    async def _handle_cancel(self, request):
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"})
        await self.state.set("cancel", "1")
        return web.json_response({"ok": True})

    async def _handle_display(self, request):
        """Return latest display of any type (text, html, code, etc.)."""
        if self.state is None:
            return web.json_response(
                {"content": None, "format": None, "title": None, "ts": None}
            )
        try:
            row = await self.state.get_latest_canvas()
            if row:
                return web.json_response(row)
            return web.json_response(
                {"content": None, "format": None, "title": None, "ts": None}
            )
        except Exception:
            return web.json_response(
                {"content": None, "format": None, "title": None, "ts": None}
            )

    async def _handle_events(self, request):
        from src.daemon.events import recent

        return web.json_response(recent(limit=50))

    async def _handle_daemon(self, request):
        body = await request.json()
        action = body.get("action", "")

        # In-process control: menubar passes callbacks when daemon
        # runs inside the same process instead of a subprocess.
        if self._control is not None:
            try:
                if action == "start" and self._control[0]:
                    await self._control[0]()
                    return web.json_response({"ok": True, "action": "started", "pid": None})
                if action == "stop" and self._control[1]:
                    self._control[1]()
                    return web.json_response({"ok": True, "action": "stopped", "pid": None})
                if action == "restart" and self._control[2]:
                    self._control[2]()
                    return web.json_response({"ok": True, "action": "restarted", "pid": None})
            except Exception as e:
                return web.json_response(
                    {"ok": False, "action": action, "error": str(e)}, status=500
                )
        if action == "stop":
            try:
                pid_file = self.config.pid_file
                if pid_file.exists():
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, signal.SIGTERM)
                return web.json_response({"ok": True, "action": "stopped", "pid": None})
            except (OSError, ValueError, ProcessLookupError):
                return web.json_response(
                    {"ok": True, "action": "already_stopped", "pid": None}
                )
        if action == "start":
            try:
                from src.daemon.watch_controls import daemon_pid, start_daemon

                running_pid = daemon_pid(self.config)
                if running_pid is not None:
                    return web.json_response(
                        {
                            "ok": True,
                            "action": "noop",
                            "pid": running_pid,
                            "message": "daemon already running",
                        }
                    )
                msg = await asyncio.to_thread(start_daemon, self.config)
                pid = daemon_pid(self.config)
                return web.json_response(
                    {
                        "ok": True,
                        "action": "started",
                        "pid": pid,
                        "message": msg,
                    }
                )
            except Exception as e:
                return web.json_response(
                    {"ok": False, "action": "start", "error": str(e)}, status=500
                )
        if action == "restart":
            try:
                from src.daemon.watch_controls import daemon_pid, restart_daemon

                msg = await asyncio.to_thread(restart_daemon, self.config)
                pid = daemon_pid(self.config)
                return web.json_response(
                    {
                        "ok": True,
                        "action": "restarted",
                        "pid": pid,
                        "message": msg,
                    }
                )
            except Exception as e:
                return web.json_response(
                    {"ok": False, "action": "restart", "error": str(e)}, status=500
                )
        return web.json_response(
            {"ok": False, "error": f"unknown action: {action}"}, status=400
        )

    async def _handle_inject(self, request):
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "empty text"}, status=400)

        from src.inject import inject_text

        # Writes .tmp then renames — the daemon polls this folder every 250ms
        # and a plain write can be read half-finished.
        inject_text(self.config.inject_dir, text)

        # The file is queued either way, but nothing drains it while the
        # pipeline is down, so say which happened rather than claiming
        # delivery.
        running = bool(self.state and self.state.get("running") == "true")
        return web.json_response({"ok": True, "delivered": running})

    async def _handle_settings_status(self, request):
        """Return current configuration status."""
        import os

        groq = bool(
            os.environ.get("GROQ_API_KEY", "").startswith("gsk_")
            or os.environ.get("GROQ_API_KEY", "").startswith("sk-")
        )
        deepseek = bool(os.environ.get("DEEPSEEK_API_KEY", "").startswith("sk-"))
        return web.json_response(
            {
                "configured": groq or deepseek,
                "groq_key": groq,
                "deepseek_key": deepseek,
                "language": self.config.groq_language or "uk",
                "tts_voice": self.config.tts_voice or "uk-UA-OstapNeural",
                "mode": self.config.mode.value
                if hasattr(self.config.mode, "value")
                else str(self.config.mode),
            }
        )

    async def _handle_settings(self, request):
        """POST /settings — the one endpoint that takes an API key.

        Also served at ``/api/setup/config``, which the first-run modal
        calls. That used to be a second handler with an identical body, and
        beside it lived a third — a plain HTML form at ``POST /setup`` that
        no shipped frontend had called in a long time and that wrote the
        .env by hand, badly. Three doors into one drawer is how the drawer
        ends up with different locks.
        """
        body = await request.json()

        keys = self._normalise_key_payload(body)
        errors = self._validate_api_keys(keys)
        if errors:
            return web.json_response({"ok": False, "errors": errors}, status=400)

        await self._apply_api_keys(keys)
        self._save_locale_settings(body)

        return web.json_response({"ok": True, "applied": True})

    def _save_locale_settings(self, body) -> None:
        """Persist language / TTS voice from `body` to config.toml, if present."""
        locale = {}
        if body.get("language"):
            locale["groq_language"] = body["language"]
        if body.get("tts_voice"):
            locale["tts_voice"] = body["tts_voice"]
        if locale:
            write_config_toml_values(locale)

    async def _handle_allow_installs(self, request):
        """POST /api/settings/allow-installs — allow or block installing
        skills and MCP servers.

        Replaces /api/settings/passphrase, which took a secret word that
        was only ever tested for emptiness.
        """
        body = await request.json()
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return web.json_response(
                {"ok": False, "error": "enabled must be true or false"}, status=400
            )
        set_capability_install_enabled(enabled)
        return web.json_response(
            {"ok": True, "enabled": enabled, "restart_required": True}
        )

    async def _handle_reset_session(self, request):
        """POST /api/settings/reset-session — backup session.json and start fresh."""
        if not self.config:
            return web.json_response({"ok": False, "error": "no config"}, status=500)
        backup = backup_session_file(self.config)
        if backup is None:
            return web.json_response(
                {"ok": False, "error": "no session file to reset"}, status=400
            )
        return web.json_response(
            {"ok": True, "backup_path": str(backup), "restart_required": True}
        )

    # ── Subsystem switches ─────────────────────────────────
    #
    # Two truths that must never be merged: what the engine actually wired
    # at its last start (the State key, written by the composition root)
    # and what config.toml asks for next time. A subsystem is chosen at
    # build time, so writing here cannot switch a running one off — the
    # only honest UI says both numbers and offers the restart.

    @staticmethod
    def _feature_names() -> list[str]:
        return [f.name for f in FEATURES]

    @staticmethod
    def _feature_config() -> dict[str, bool]:
        """The ``spine_features`` table as config.toml has it.

        Filtered to known names and real booleans: the file is hand-edited,
        and ``features.resolve`` ignores anything else, so the dashboard
        must not show a value the engine would never honour.
        """
        import tomllib

        from src.config import HEARE_HOME as home

        path = home / "config.toml"
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        table = data.get("spine_features")
        if not isinstance(table, dict):
            return {}
        known = set(API._feature_names())
        return {
            name: value
            for name, value in table.items()
            if name in known and isinstance(value, bool)
        }

    def _live_features(self) -> list | None:
        """What the running engine reports it wired, or None when it does
        not report at all (the pipecat engine, or a daemon older than the
        State key). None and [] are different answers and stay different."""
        if self.state is None:
            return None
        raw = self.state.snapshot().get("spine_features")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, list) else None

    async def _handle_features(self, request):
        """GET /api/features — the running truth and the configured one."""
        return web.json_response(
            {
                "ok": True,
                "live": self._live_features(),
                "config": self._feature_config(),
                # The defaults, so the widget can show what an absent key
                # means without keeping its own copy of the list.
                "defaults": {f.name: f.default for f in FEATURES},
            }
        )

    async def _handle_features_set(self, request):
        """POST /api/features — set one subsystem for the next start.

        ``{"name": "roles", "enabled": false}``. Nothing else in
        config.toml is touched, and nothing about the running engine
        changes: the answer carries ``restart_required``.
        """
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid JSON"}, status=400
            )
        name = body.get("name")
        enabled = body.get("enabled")
        known = self._feature_names()
        if not isinstance(name, str) or name not in known:
            return web.json_response(
                {"ok": False, "error": f"unknown subsystem: {name}", "known": known},
                status=400,
            )
        if not isinstance(enabled, bool):
            return web.json_response(
                {"ok": False, "error": "enabled must be true or false"}, status=400
            )

        table = self._feature_config()
        table[name] = enabled
        try:
            write_config_toml_values(
                {"spine_features": _TomlInline(_render_feature_table(table))}
            )
        except OSError as e:
            logger.exception("could not write spine_features")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

        return web.json_response(
            {"ok": True, "config": table, "restart_required": True}
        )

    async def _handle_mic_status(self, request):
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            inputs = [d for d in devices if d["max_input_channels"] > 0]
            if not inputs:
                return web.json_response(
                    {"ok": True, "mic_available": False, "reason": "no_input_device"}
                )
            try:
                sd.check_input_settings(device=inputs[0]["name"])
                return web.json_response({"ok": True, "mic_available": True})
            except sd.PortAudioError:
                return web.json_response(
                    {"ok": True, "mic_available": False, "reason": "permission_denied"}
                )
        except Exception:
            return web.json_response(
                {"ok": True, "mic_available": False, "reason": "unknown"}
            )

    async def _handle_audio_devices(self, request):
        """List audio devices via sounddevice, with active device markers."""
        try:
            import sounddevice as sd

            devices = []
            active_input = None
            active_output = None

            try:
                if self.config.audio_input_device_file.exists():
                    active_input = (
                        self.config.audio_input_device_file.read_text().strip() or None
                    )
            except Exception:
                pass
            try:
                if self.config.audio_output_device_file.exists():
                    active_output = (
                        self.config.audio_output_device_file.read_text().strip() or None
                    )
            except Exception:
                pass

            for i, dev in enumerate(sd.query_devices()):
                name = dev["name"]
                try:
                    hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
                except Exception:
                    hostapi_name = "?"
                devices.append(
                    {
                        "index": i,
                        "name": name,
                        "max_input_channels": int(dev.get("max_input_channels", 0)),
                        "max_output_channels": int(dev.get("max_output_channels", 0)),
                        "hostapi": hostapi_name,
                        "active_in": bool(
                            active_input and active_input.lower() in name.lower()
                        ),
                        "active_out": bool(
                            active_output and active_output.lower() in name.lower()
                        ),
                    }
                )
            return web.json_response(
                {
                    "devices": devices,
                    "active_input": active_input,
                    "active_output": active_output,
                    "error": None,
                }
            )
        except ImportError:
            return web.json_response(
                {
                    "devices": [],
                    "active_input": None,
                    "active_output": None,
                    "error": "sounddevice not installed",
                }
            )
        except Exception as e:
            return web.json_response(
                {
                    "devices": [],
                    "active_input": None,
                    "active_output": None,
                    "error": str(e),
                },
                status=500,
            )

    async def _handle_tools(self, request):
        """List all available tools: built-in, skills, and MCP servers."""
        try:
            from src.agent.tools.direct import (
                _list_built_in_tools,
                _list_mcp_servers,
                _list_skills,
            )

            built_in = _list_built_in_tools()
            skills = []
            mcps = []
            error = None

            try:
                skills = _list_skills(self.config)
            except Exception as e:
                logger.warning("tools endpoint: skills list failed: %s", e)
                error = f"skills: {e}"

            try:
                mcps = _list_mcp_servers(self.config)
            except Exception as e:
                logger.warning("tools endpoint: mcp list failed: %s", e)
                if error:
                    error += f"; mcps: {e}"
                else:
                    error = f"mcps: {e}"

            return web.json_response(
                {
                    "built_in": built_in,
                    "skills": skills,
                    "mcps": mcps,
                    "error": error,
                    # What .mcp.json *asks for* is the list above; this is
                    # what actually connected. They disagree exactly when
                    # something is wrong, which is when anyone looks.
                    "mcp_status": self._mcp_status_payload(),
                }
            )
        except Exception as e:
            return web.json_response(
                {
                    "built_in": [],
                    "skills": [],
                    "mcps": [],
                    "error": str(e),
                    "mcp_status": self._mcp_status_payload(),
                },
                status=500,
            )

    async def _handle_audio_device_select(self, request):
        """Select an audio device by writing its name to the hot-reload file."""
        try:
            body = await request.json()
            device_name = body.get("device_name", "").strip()
            kind = body.get("kind", "")
            if not device_name or kind not in ("input", "output"):
                return web.json_response(
                    {
                        "ok": False,
                        "error": "device_name and kind (input|output) required",
                    },
                    status=400,
                )
            if kind == "input":
                self.config.audio_input_device_file.parent.mkdir(
                    parents=True, exist_ok=True
                )
                self.config.audio_input_device_file.write_text(device_name)
            else:
                self.config.audio_output_device_file.parent.mkdir(
                    parents=True, exist_ok=True
                )
                self.config.audio_output_device_file.write_text(device_name)
            return web.json_response(
                {
                    "ok": True,
                    "kind": kind,
                    "device": device_name,
                }
            )
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_bridge_status(self, request):
        """Return full bridge state: enabled, connected, pair code, token, ws url, port."""
        try:
            status_path = HEARE_HOME / "browser_bridge.status"
            port = self.config.browser_bridge_port
            enabled = self.config.browser_bridge_enabled
            has_token = bool(
                self.config.browser_bridge_token
                and len(self.config.browser_bridge_token) > 8
            )
            connected = False
            pair_code = None
            pair_remaining_s = 0.0

            try:
                raw = json.loads(status_path.read_text())
                connected = bool(raw.get("connected", False))
                pc = raw.get("pair_code")
                if pc:
                    pair_code = str(pc)
                    # Use stored remaining time from the status file
                    if raw.get("pair_remaining_s") is not None:
                        pair_remaining_s = max(0.0, float(raw["pair_remaining_s"]))
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                pass

            token_hint = None
            if has_token:
                tok = self.config.browser_bridge_token
                token_hint = "••••" + tok[-4:] if len(tok) >= 4 else "••••" + tok

            return web.json_response(
                {
                    "enabled": enabled,
                    "port": port,
                    "connected": connected,
                    "has_token": has_token,
                    "ws_url": f"ws://127.0.0.1:{port}",
                    "pair_code": pair_code,
                    "pair_remaining_s": round(pair_remaining_s, 1),
                    "token_hint": token_hint,
                }
            )
        except Exception as e:
            logger.exception("bridge/status failed")
            return web.json_response(
                {
                    "enabled": False,
                    "port": self.config.browser_bridge_port,
                    "connected": False,
                    "has_token": False,
                    "ws_url": f"ws://127.0.0.1:{self.config.browser_bridge_port}",
                    "pair_code": None,
                    "pair_remaining_s": 0.0,
                    "error": str(e),
                }
            )

    async def _handle_bridge_token(self, request):
        """Return the full bridge token (sensitive — requires explicit request)."""
        try:
            token = self.config.browser_bridge_token
            if not token:
                return web.json_response({"token": None})
            return web.json_response({"token": token})
        except Exception as e:
            logger.exception("bridge/token failed")
            return web.json_response({"token": None, "error": str(e)})

    async def _handle_bridge_rotate_token(self, request):
        """Generate a new browser-bridge token, persist it, return JSON."""
        try:
            token = secrets.token_urlsafe(32)
            write_browser_bridge_token(self.config, token)

            token_path = HEARE_HOME / "browser_bridge.token"
            token_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(token + "\n")

            return web.json_response(
                {
                    "ok": True,
                    "token": token,
                    "restart_required": True,
                    "message": "Restart the daemon for the new token to take effect.",
                }
            )
        except Exception as e:
            logger.exception("bridge/rotate-token failed")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_bridge_toggle(self, request):
        """Toggle browser_bridge_enabled in config.toml."""
        try:
            body = await request.json()
            enabled = bool(body.get("enabled", True))
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid JSON body"}, status=400
            )

        try:
            config_path = HEARE_HOME / "config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)

            existing = config_path.read_text() if config_path.exists() else ""
            section_re = re.compile(
                r"(?ms)^\[browser_bridge\][^\[]*?(?=^\[|\Z)",
            )
            match = section_re.search(existing)

            if match:
                block = match.group(0)
                enabled_re = re.compile(r"(?m)^\s*enabled\s*=.*$")
                new_line = "enabled = true" if enabled else "enabled = false"
                if enabled_re.search(block):
                    new_block = enabled_re.sub(new_line, block, count=1)
                else:
                    new_block = re.sub(
                        r"^\[browser_bridge\][^\n]*\n",
                        lambda m: m.group(0) + new_line + "\n",
                        block,
                        count=1,
                    )
                content = (
                    existing[: match.start()] + new_block + existing[match.end() :]
                )
            else:
                new_section = (
                    f"[browser_bridge]\n"
                    f"enabled = {'true' if enabled else 'false'}\n"
                    f"port = {self.config.browser_bridge_port}\n"
                )
                sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
                content = existing + sep + ("\n" if existing else "") + new_section

            fd, tmp_path = tempfile.mkstemp(
                prefix=".config.toml.",
                suffix=".tmp",
                dir=str(config_path.parent),
            )
            with os.fdopen(fd, "w") as fh:
                fh.write(content)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, config_path)

            # Also update the in-memory config
            self.config.browser_bridge_enabled = enabled

            return web.json_response(
                {
                    "ok": True,
                    "enabled": enabled,
                    "restart_required": True,
                    "message": "Restart the daemon for the change to take effect.",
                }
            )
        except Exception as e:
            logger.exception("bridge/toggle failed")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    # ── Prompt Manager endpoints ─────────────────────────

    async def _handle_prompts(self, request):
        try:
            from pathlib import Path
            from src.agent.llm.prompt_sections import PROMPT_SECTIONS
            from src.agent.llm.prompt_sections import (
                _render_persona_inline,
                _render_hard_constraints,
                _render_tool_catalog,
            )
            from src.agent.identity import load_identity, render_persona

            project_root = Path(__file__).resolve().parent.parent

            # Load identity for persona rendering
            identity = load_identity(Path.home() / ".heare" / "identity.json")
            persona_text = ""
            if identity:
                persona_template = (
                    project_root / "prompts" / "persona.txt"
                ).read_text()
                persona_text = render_persona(persona_template, identity)

            lang = (
                self.config.groq_language
                if self.config.groq_language not in ("auto", "")
                else "uk"
            )

            results = []
            for ps in PROMPT_SECTIONS:
                char_count = 0
                preview = ""
                if ps.source == "template" and ps.template_path:
                    tpath = project_root / ps.template_path
                    if tpath.exists():
                        content = tpath.read_text()
                        char_count = len(content)
                        preview = content[:120].replace("\n", " ")
                elif ps.source == "inline":
                    if ps.key == "persona":
                        content = _render_persona_inline(persona_text, lang)
                        char_count = len(content)
                        preview = content[:120].replace("\n", " ")
                    elif ps.key == "hard_constraints":
                        content = _render_hard_constraints(lang)
                        char_count = len(content)
                        preview = content[:120].replace("\n", " ")
                    elif ps.key == "tool_catalog":
                        content = _render_tool_catalog()
                        char_count = len(content)
                        preview = content[:120].replace("\n", " ")
                    else:
                        preview = "(computed at render time)"
                elif ps.source == "dynamic":
                    preview = f"(computed from current {ps.key} state at render time)"
                results.append(
                    {
                        "key": ps.key,
                        "order": ps.order,
                        "source": ps.source,
                        "template_path": ps.template_path,
                        "char_count": char_count,
                        "content_preview": preview,
                    }
                )
            return web.json_response(results)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_prompt_section(self, request):
        try:
            from pathlib import Path
            from src.agent.llm.prompt_sections import PROMPT_SECTIONS
            from src.agent.llm.prompt_sections import (
                _render_persona_inline,
                _render_hard_constraints,
                _render_tool_catalog,
            )
            from src.agent.identity import load_identity, render_persona

            project_root = Path(__file__).resolve().parent.parent
            key = request.match_info["key"]
            section = None
            for ps in PROMPT_SECTIONS:
                if ps.key == key:
                    section = ps
                    break
            if section is None:
                return web.json_response({"error": f"unknown key: {key}"}, status=404)

            lang = (
                self.config.groq_language
                if self.config.groq_language not in ("auto", "")
                else "uk"
            )

            if section.source == "template" and section.template_path:
                tpath = project_root / section.template_path
                if tpath.exists():
                    content = tpath.read_text()
                    return web.json_response(
                        {
                            "key": section.key,
                            "order": section.order,
                            "source": section.source,
                            "template_path": section.template_path,
                            "char_count": len(content),
                            "content": content,
                        }
                    )
                return web.json_response(
                    {
                        "key": section.key,
                        "source": section.source,
                        "template_path": section.template_path,
                        "content": None,
                        "note": f"template not found: {section.template_path}",
                    }
                )

            if section.source == "inline":
                if section.key == "persona":
                    identity = load_identity(Path.home() / ".heare" / "identity.json")
                    persona_text = ""
                    if identity:
                        persona_template = (
                            project_root / "prompts" / "persona.txt"
                        ).read_text()
                        persona_text = render_persona(persona_template, identity)
                    content = _render_persona_inline(persona_text, lang)
                elif section.key == "hard_constraints":
                    content = _render_hard_constraints(lang)
                elif section.key == "tool_catalog":
                    content = _render_tool_catalog()
                else:
                    content = "(computed at render time)"
                return web.json_response(
                    {
                        "key": section.key,
                        "order": section.order,
                        "source": section.source,
                        "char_count": len(content),
                        "content": content,
                    }
                )

            return web.json_response(
                {
                    "key": section.key,
                    "source": section.source,
                    "content": None,
                    "note": "computed at render time from context",
                }
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_prompt_save(self, request):
        try:
            from pathlib import Path
            from src.agent.llm.prompt_sections import PROMPT_SECTIONS

            project_root = Path(__file__).resolve().parent.parent
            key = request.match_info["key"]
            section = None
            for ps in PROMPT_SECTIONS:
                if ps.key == key:
                    section = ps
                    break
            if section is None:
                return web.json_response(
                    {"ok": False, "error": f"unknown key: {key}"}, status=404
                )
            if section.source != "template":
                return web.json_response(
                    {"ok": False, "error": f"section '{key}' is not template-backed"},
                    status=400,
                )
            if not section.template_path:
                return web.json_response(
                    {"ok": False, "error": f"section '{key}' has no template_path"},
                    status=400,
                )

            # Defense-in-depth: ensure template_path stays within prompts/
            tpath = (project_root / section.template_path).resolve()
            prompts_dir = (project_root / "prompts").resolve()
            try:
                tpath.relative_to(prompts_dir)
            except ValueError:
                return web.json_response(
                    {"ok": False, "error": "invalid template path"}, status=400
                )

            body = await request.json()
            content = body.get("content", "")
            if not isinstance(content, str):
                return web.json_response(
                    {"ok": False, "error": "content must be a string"}, status=400
                )

            tpath.write_text(content)
            return web.json_response(
                {"ok": True, "key": key, "char_count": len(content)}
            )
        except Exception as e:
            logger.exception("prompt save failed for key=%s", key)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_prompt_preview(self, request):
        try:
            from src.agent.llm.context_injector import render_native_system_prompt
            from src.agent.identity import load_identity, render_persona
            from pathlib import Path

            # Load the real identity from ~/.heare/identity.json
            persona_text = ""
            identity = load_identity(Path.home() / ".heare" / "identity.json")
            if identity:
                persona_template = (
                    Path(__file__).resolve().parent.parent / "prompts" / "persona.txt"
                ).read_text()
                persona_text = render_persona(persona_template, identity)

            # Use the language from settings (or default to uk)
            lang = (
                self.config.groq_language
                if self.config.groq_language not in ("auto", "")
                else "uk"
            )

            preview = render_native_system_prompt(
                persona=persona_text,
                language=lang,
                context={
                    "time": "2026-01-01 12:00:00",
                    "timezone": "Europe/Kiev",
                    "mode_block": "MODE GATE: ambient\nVoice output ON. Full engagement.",
                    "recent_transcripts": "(none)",
                    "conversation_summary": "No previous conversation.",
                    "active_topics": "",
                    "entities": "",
                    "recent_turns": "(none)",
                    "recent_actions": "(none)",
                },
            )
            return web.Response(text=preview, content_type="text/plain; charset=utf-8")
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_agents(self, request):
        try:
            from src.agent.subagent_manager import get_agent_manager

            mgr = get_agent_manager()
            if mgr is None:
                return web.json_response(
                    {"agents": [], "error": "Agent manager not initialized"}, status=503
                )
            agents = mgr.list_all()
            return web.json_response({"agents": agents, "count": len(agents)})
        except Exception as e:
            return web.json_response({"agents": [], "error": str(e)}, status=500)

    async def _handle_agents_start(self, request):
        try:
            body = await request.json()
            prompt = body.get("prompt", "").strip()
            if not prompt:
                return web.json_response({"error": "prompt is required"}, status=400)
            from src.agent.subagent_manager import get_agent_manager

            mgr = get_agent_manager()
            if mgr is None:
                return web.json_response(
                    {"error": "Agent manager not initialized"}, status=503
                )
            state = await mgr.start(prompt, cwd=body.get("cwd"))
            return web.json_response(
                {
                    "session_id": state.session_id,
                    "status": state.status,
                    "prompt": state.prompt,
                    "started_at": state.started_at,
                }
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_agents_cancel(self, request):
        try:
            body = await request.json()
            session_id = body.get("session_id", "").strip()
            if not session_id:
                return web.json_response(
                    {"error": "session_id is required"}, status=400
                )
            from src.agent.subagent_manager import get_agent_manager

            mgr = get_agent_manager()
            if mgr is None:
                return web.json_response(
                    {"error": "Agent manager not initialized"}, status=503
                )
            result = await mgr.cancel(session_id)
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_agents_result(self, request):
        session_id = request.match_info.get("session_id", "")
        try:
            from src.agent.subagent_manager import get_agent_manager

            mgr = get_agent_manager()
            if mgr is None:
                return web.json_response(
                    {"error": "Agent manager not initialized"}, status=503
                )
            result = mgr.result(session_id)
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Setup handlers ────────────────────────────────────

    async def _handle_setup_state(self, request):
        """GET /api/setup — full setup state."""
        identity = None
        if self.config and hasattr(self.config, "identity_file"):
            identity = load_identity(self.config.identity_file)

        config = {}
        if self.config:
            config["language"] = getattr(self.config, "groq_language", "en")
            config["tts_voice"] = getattr(self.config, "tts_voice", "en-US-AriaNeural")

        groq_ok = bool(
            os.environ.get("GROQ_API_KEY", "").startswith("gsk_")
            or os.environ.get("GROQ_API_KEY", "").startswith("sk-")
        )
        available = get_available(self.config) if self.config else []
        llm_ok = len(available) > 0

        # Read setup complete flag
        setup_flag = HEARE_HOME / ".setup_complete"
        setup_complete = setup_flag.exists()

        return web.json_response(
            {
                "setup_complete": setup_complete,
                "identity": identity,
                "config": {
                    "language": config.get("language", "en"),
                    "tts_voice": config.get("tts_voice", "en-US-AriaNeural"),
                    "groq_key_configured": groq_ok,
                    "llm_key_configured": llm_ok,
                    "available_providers": available,
                },
            }
        )

    async def _handle_setup_identity(self, request):
        """POST /api/setup/identity — save identity fields."""
        if not self.config:
            return web.json_response({"ok": False, "error": "no config"}, status=500)

        body = await request.json()
        identity = {
            "name": body.get("name", ""),
            "creature": body.get("creature", ""),
            "vibe": body.get("vibe", ""),
            "emoji": body.get("emoji", ""),
            "tagline": body.get("tagline", ""),
            "generated_at": body.get("generated_at", ""),
        }
        try:
            save_identity(self.config, identity)
            return web.json_response({"ok": True, "identity": identity})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_setup_identity_regenerate(self, request):
        """POST /api/setup/identity/regenerate — regenerate persona via LLM."""
        if not self.config:
            return web.json_response({"ok": False, "error": "no config"}, status=500)

        try:
            available = get_available(self.config)
            if not available:
                return web.json_response(
                    {"ok": False, "error": "No LLM API keys configured"}, status=400
                )
            provider_name = available[0]
            active_cfg = PROVIDERS.get(provider_name)
            api_key = getattr(self.config, active_cfg.api_key_attr)
            identity_factory = make_identity_bootstrap(
                active_cfg,
                api_key,
                active_cfg.default_model,
                active_cfg.timeout,
            )
            identity = await regenerate_identity(identity_factory, self.config)
            return web.json_response({"ok": True, "identity": identity})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_setup_complete(self, request):
        """POST /api/setup/complete — mark setup as done."""
        setup_flag = HEARE_HOME / ".setup_complete"
        setup_flag.parent.mkdir(parents=True, exist_ok=True)
        setup_flag.touch()
        return web.json_response({"ok": True})

    # ── Memory dashboard (requires memory_backend) ────────────────

    async def _handle_memories(self, request):
        q = request.query.get("q", "")
        type_filter = request.query.get("type", None)
        limit = _clamp_int(request.query.get("limit"), 50, 1, 200)
        mb = self._memory_backend
        if mb is None:
            return web.json_response({"memories": []})
        if q:
            types = [MemoryType(type_filter)] if type_filter else None
            entries = await mb.search(q, limit=limit, types=types)
        else:
            entries = await mb.context(limit=limit)
        return web.json_response(
            {
                "memories": [
                    {
                        "id": e.id,
                        "type": e.type.value,
                        "content": e.content,
                        "source": e.source,
                        "confidence": e.confidence,
                        "created_ts": e.created_ts,
                        "last_accessed": e.last_accessed,
                    }
                    for e in entries
                ]
            }
        )

    async def _handle_memories_stats(self, request):
        mb = self._memory_backend
        if mb is None:
            return web.json_response({"total": 0, "by_type": {}, "archived": 0})
        stats = await mb.stats()
        return web.json_response(stats)

    async def _handle_memories_forget(self, request):
        memory_id = request.match_info["id"]
        mb = self._memory_backend
        if mb is None:
            return web.json_response({"ok": False, "error": "no memory backend"}, status=503)
        ok = await mb.forget(memory_id)
        if not ok:
            return web.json_response({"ok": False, "error": "memory not found"}, status=404)
        return web.json_response({"ok": True})

    # ── Artifacts (role sessions: meeting secretary, etc.) ────────

    async def _handle_artifacts_list(self, request):
        """GET /api/artifacts — list *.md artifact files, newest first."""
        artifacts_dir = Path(self.config.workspace_dir) / "artifacts"
        if not artifacts_dir.exists():
            return web.json_response({"ok": True, "artifacts": []})
        try:
            entries = []
            for p in artifacts_dir.iterdir():
                if not p.is_file() or p.suffix != ".md":
                    continue
                st = p.stat()
                entries.append(
                    {"name": p.name, "size": st.st_size, "mtime": st.st_mtime}
                )
            entries.sort(key=lambda e: e["mtime"], reverse=True)
            return web.json_response({"ok": True, "artifacts": entries})
        except OSError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_artifacts_get(self, request):
        """GET /api/artifacts/{name} — return one artifact's raw content.

        Unauthenticated localhost endpoint (known repo-wide issue) — the
        name is validated defensively so it can't be used to read files
        outside the artifacts dir, but this does not add auth.
        """
        name = request.match_info.get("name", "")
        if not name or "/" in name or "\\" in name or ".." in name:
            return web.json_response(
                {"ok": False, "error": "invalid name"}, status=400
            )
        artifacts_dir = Path(self.config.workspace_dir) / "artifacts"
        try:
            artifacts_dir_resolved = artifacts_dir.resolve()
            target = (artifacts_dir / name).resolve()
        except OSError:
            return web.json_response(
                {"ok": False, "error": "invalid name"}, status=400
            )
        if not target.is_relative_to(artifacts_dir_resolved):
            return web.json_response(
                {"ok": False, "error": "invalid name"}, status=400
            )
        if not target.is_file():
            return web.json_response(
                {"ok": False, "error": "not found"}, status=404
            )
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        return web.Response(text=content, content_type="text/plain", charset="utf-8")

    def _available_providers(self):
        return get_available(self.config)
