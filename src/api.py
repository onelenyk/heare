"""Minimal HTTP API for daemon control — backs the web frontend."""
import json
import logging
import os
import signal
import sqlite3
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_INDEX_HTML: str | None = None

from aiohttp import web
from src.agent.identity import load_identity
from src.agent.llm.providers import PROVIDERS, get_available, get_config
from src.agent.modes import VALID_MODES
from src.config import HEARE_HOME
from src.watch.data import (
    counts,
    daemon_status,
    fetch_usage,
    open_db,
    read_voice_state,
)


class API:
    def __init__(self, state, config, daemon_control=None):
        self.state = state
        self.config = config
        self._control = daemon_control  # (start_fn, stop_fn, restart_fn)
        self._app = web.Application()
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
        self._app.router.add_get("/state", self._handle_state)
        self._app.router.add_post("/mode", self._handle_mode)
        self._app.router.add_post("/mute", self._handle_mute)
        self._app.router.add_post("/provider", self._handle_provider)
        self._app.router.add_post("/model", self._handle_model)
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
        self._app.router.add_post("/setup", self._handle_setup)
        self._app.router.add_get("/mic/status", self._handle_mic_status)
        self._runner = None
        self._site = None

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
                Path(__file__).resolve().parent.parent.parent / "src" / "frontend" / "index.html",
                Path(__file__).resolve().parent / "frontend" / "index.html",
                Path(__file__).resolve().parent.parent / "frontend" / "index.html",
            ]:
                if candidate.exists():
                    _INDEX_HTML = candidate.read_text()
                    break
            if _INDEX_HTML is None:
                return web.Response(text="Frontend not found", status=500)
        return web.Response(text=_INDEX_HTML, content_type="text/html")

    async def _save_form_keys(self, post):
        if self.state is None:
            return
        for attr in ("groq_api_key", "deepseek_api_key", "zai_api_key", "opencode_api_key"):
            val = post.get(attr, "").strip()
            if val and len(val) >= 30:
                await self.state.set(f"key_{attr}", val)

    async def _handle_state(self, request):
        if self.state is None:
            return web.json_response({"running": False})
        data = self.state.snapshot()
        data["providers"] = self._available_providers()

        # Models for current provider
        try:
            provider_key = data.get("provider", "")
            if provider_key in PROVIDERS:
                cfg = get_config(provider_key)
                data["models"] = list(cfg.model_whitelist) if cfg.model_whitelist else [cfg.default_model]
            else:
                data["models"] = []
        except Exception:
            data["models"] = []

        # Chrome bridge status
        try:
            token_file = HEARE_HOME / "browser_bridge.token"
            data["chrome"] = self.config.browser_bridge_enabled and token_file.exists()
        except Exception:
            data["chrome"] = False

        try:
            identity = load_identity(self.config.identity_file)
            if identity:
                data["agent"] = identity["name"]
                data["emoji"] = identity["emoji"]
        except Exception:
            pass

        try:
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
            db = sqlite3.connect(str(self.config.db_path))
            row = db.execute(
                "SELECT text, agent_mode FROM transcripts WHERE mode='assistant' ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            db.close()
            if row:
                data["last_response"] = row[0]
                data["last_response_mode"] = row[1]
        except Exception:
            pass

        return web.json_response(data)

    async def _handle_activity(self, request):
        try:
            db = sqlite3.connect(str(self.config.db_path))
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT ts, mode as who, agent_spoken as type, text as content "
                "FROM transcripts ORDER BY ts DESC LIMIT 30"
            ).fetchall()
            db.close()
            return web.json_response([
                {
                    "ts": r["ts"],
                    "who": "bot" if r["who"] == "assistant" else "you",
                    "type": "said",
                    "content": r["content"],
                }
                for r in rows
            ])
        except Exception:
            return web.json_response([], status=500)

    async def _handle_logs(self, request):
        log_file = self.config.log_dir / "daemon.log"
        if not log_file.exists():
            return web.json_response({"lines": []})
        try:
            lines = log_file.read_text().splitlines()[-20:]
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

    async def _handle_provider(self, request):
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"})
        body = await request.json()
        provider = body.get("provider", "")
        if provider not in self._available_providers():
            return web.json_response({"ok": False}, status=400)
        await self.state.set("provider", provider)
        return web.json_response({"ok": True, "provider": provider})

    async def _handle_model(self, request):
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"})
        body = await request.json()
        model = body.get("model", "")
        if not model:
            return web.json_response({"ok": False}, status=400)
        await self.state.set("model", model)
        return web.json_response({"ok": True, "model": model})

    async def _handle_cancel(self, request):
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"})
        await self.state.set("cancel", "1")
        return web.json_response({"ok": True})

    async def _handle_display(self, request):
        """Return latest display of any type (text, html, code, etc.)."""
        if self.state is None:
            return web.json_response({"content": None, "format": None, "title": None, "ts": None})
        try:
            row = await self.state.get_latest_canvas()
            if row:
                return web.json_response(row)
            return web.json_response({"content": None, "format": None, "title": None, "ts": None})
        except Exception:
            return web.json_response({"content": None, "format": None, "title": None, "ts": None})

    async def _handle_events(self, request):
        from src.daemon.events import recent

        return web.json_response(recent(limit=50))

    async def _handle_daemon(self, request):
        body = await request.json()
        action = body.get("action", "")
        if action == "stop":
            try:
                pid_file = self.config.pid_file
                if pid_file.exists():
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, signal.SIGTERM)
                return web.json_response({"ok": True, "action": "stopped"})
            except (OSError, ValueError, ProcessLookupError):
                return web.json_response({"ok": True, "action": "already_stopped"})
        return web.json_response({"ok": False, "error": f"unknown action: {action}"}, status=400)

    async def _handle_inject(self, request):
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "empty text"}, status=400)
        fname = f"inject_{int(time.time())}_{uuid.uuid4().hex[:6]}.txt"
        self.config.inject_dir.mkdir(parents=True, exist_ok=True)
        (self.config.inject_dir / fname).write_text(text)
        return web.json_response({"ok": True})

    async def _handle_settings_status(self, request):
        """Return current configuration status."""
        import os
        groq = bool(os.environ.get("GROQ_API_KEY", "").startswith("gsk_") or os.environ.get("GROQ_API_KEY", "").startswith("sk-"))
        deepseek = bool(os.environ.get("DEEPSEEK_API_KEY", "").startswith("sk-"))
        return web.json_response({
            "configured": groq or deepseek,
            "groq_key": groq,
            "deepseek_key": deepseek,
            "language": self.config.groq_language or "uk",
            "tts_voice": self.config.tts_voice or "uk-UA-OstapNeural",
            "mode": self.config.mode.value if hasattr(self.config.mode, 'value') else str(self.config.mode),
        })

    async def _handle_settings(self, request):
        """Save settings to .env, config.toml, and apply keys live."""
        import os
        from dotenv import load_dotenv
        body = await request.json()

        env_path = str(Path.home() / ".heare" / ".env")

        updates = {}
        if body.get("groq_api_key"):
            updates["GROQ_API_KEY"] = body["groq_api_key"]
        if body.get("deepseek_api_key"):
            updates["DEEPSEEK_API_KEY"] = body["deepseek_api_key"]

        errors = []
        groq_key = body.get("groq_api_key", "").strip()
        if groq_key and not (groq_key.startswith("gsk_") or groq_key.startswith("sk-")):
            errors.append("Groq key must start with gsk_ or sk-")
        for attr in ("deepseek_api_key", "zai_api_key", "opencode_api_key"):
            val = body.get(attr, "").strip()
            if val and not val.startswith("sk-"):
                errors.append(f"{attr.replace('_', ' ').title()} key must start with sk-")
        if errors:
            return web.json_response({"ok": False, "errors": errors}, status=400)

        if updates:
            existing = {}
            if os.path.exists(env_path):
                for line in open(env_path):
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        existing[k.strip()] = v.strip()
            existing.update(updates)
            with open(env_path, "w") as f:
                for k, v in existing.items():
                    f.write(f"{k}={v}\n")
            os.environ.update(updates)
            load_dotenv(env_path, override=True)

        for attr in ("groq_api_key", "deepseek_api_key", "zai_api_key", "opencode_api_key"):
            if body.get(attr):
                setattr(self.config, attr, body[attr])
                val = body.get(attr, "").strip()
                if val and len(val) >= 30:
                    await self.state.set(f"key_{attr}", val)

        return web.json_response({"ok": True, "applied": True})

    async def _handle_setup(self, request):
        post = await request.post()
        updates = {}
        for key in ("groq_api_key", "deepseek_api_key", "zai_api_key", "opencode_api_key"):
            val = post.get(key, "").strip()
            if val:
                updates[key.upper()] = val

        if updates:
            env_path = str(Path.home() / ".heare" / ".env")
            existing = {}
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if "=" in line and not line.startswith("#"):
                            k, v = line.split("=", 1)
                            existing[k.strip()] = v.strip()
            existing.update(updates)
            with open(env_path, "w") as f:
                for k, v in existing.items():
                    f.write(f"{k}={v}\n")
            os.environ.update(updates)
            from dotenv import load_dotenv
            load_dotenv(env_path, override=True)

        for attr in ("groq_api_key", "deepseek_api_key", "zai_api_key", "opencode_api_key"):
            val = post.get(attr, "").strip()
            if val:
                setattr(self.config, attr, val)

        return web.Response(
            text=(
                '<!DOCTYPE html><html><head><meta charset="utf-8">'
                '<meta http-equiv="refresh" content="2;url=/">'
                '<title>Heare — Starting</title>'
                '<style>body{background:#0d1117;color:#c9d1d9;'
                'font-family:-apple-system,sans-serif;display:flex;'
                'align-items:center;justify-content:center;'
                'min-height:100vh;margin:0;font-size:18px}'
                '</style></head><body>'
                'Starting Heare…</body></html>'
            ),
            content_type="text/html",
        )

    async def _handle_mic_status(self, request):
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            inputs = [d for d in devices if d["max_input_channels"] > 0]
            if not inputs:
                return web.json_response({"ok": True, "mic_available": False, "reason": "no_input_device"})
            try:
                sd.check_input_settings(device=inputs[0]["name"])
                return web.json_response({"ok": True, "mic_available": True})
            except sd.PortAudioError:
                return web.json_response({"ok": True, "mic_available": False, "reason": "permission_denied"})
        except Exception:
            return web.json_response({"ok": True, "mic_available": False, "reason": "unknown"})

    def _available_providers(self):
        return get_available(self.config)
