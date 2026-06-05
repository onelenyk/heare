"""Minimal HTTP API for daemon control — backs the web frontend."""
import asyncio
import json
import logging
import os
import re
import secrets
import signal
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_INDEX_HTML: str | None = None

from aiohttp import web
from src.agent.identity import load_identity
from src.agent.llm.providers import PROVIDERS, get_available, get_config
from src.agent.modes import VALID_MODES
from src.config import HEARE_HOME, write_browser_bridge_token
from src.dashboard_data import (
    counts,
    daemon_status,
    fetch_usage,
    open_db,
    read_voice_state,
)
from src.version import app_version


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
        self._app.router.add_post("/interrupt", self._handle_interrupt)
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
        self._app.router.add_get("/api/audio-devices", self._handle_audio_devices)
        self._app.router.add_post("/api/chrome/launch", self._handle_chrome_launch)
        self._app.router.add_get("/api/tools", self._handle_tools)
        self._app.router.add_get("/api/chrome/profiles", self._handle_chrome_profiles)
        self._app.router.add_post("/api/audio-devices/select", self._handle_audio_device_select)
        self._app.router.add_get("/api/bridge/status", self._handle_bridge_status)
        self._app.router.add_get("/api/bridge/token", self._handle_bridge_token)
        self._app.router.add_post("/api/bridge/rotate-token", self._handle_bridge_rotate_token)
        self._app.router.add_post("/api/bridge/toggle", self._handle_bridge_toggle)
        self._app.router.add_get("/api/prompts", self._handle_prompts)
        self._app.router.add_get("/api/prompts/preview", self._handle_prompt_preview)
        self._app.router.add_get("/api/prompts/{key}", self._handle_prompt_section)
        self._app.router.add_post("/api/prompts/{key}", self._handle_prompt_save)
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
        data["version"] = app_version(include_sha=False)
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

        data["interrupt_enabled"] = not self.config.interrupt_enabled_file.exists()

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

    async def _handle_interrupt(self, request):
        if self.state is None:
            return web.json_response({"ok": False, "error": "daemon initializing"}, status=503)
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
        new_state = not self.config.interrupt_enabled_file.exists()
        return web.json_response({"ok": True, "enabled": new_state})

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
                return web.json_response({"ok": True, "action": "stopped", "pid": None})
            except (OSError, ValueError, ProcessLookupError):
                return web.json_response({"ok": True, "action": "already_stopped", "pid": None})
        if action == "start":
            try:
                from src.daemon.watch_controls import daemon_pid, start_daemon

                running_pid = daemon_pid(self.config)
                if running_pid is not None:
                    return web.json_response({
                        "ok": True,
                        "action": "noop",
                        "pid": running_pid,
                        "message": "daemon already running",
                    })
                msg = await asyncio.to_thread(start_daemon, self.config)
                pid = daemon_pid(self.config)
                return web.json_response({
                    "ok": True,
                    "action": "started",
                    "pid": pid,
                    "message": msg,
                })
            except Exception as e:
                return web.json_response(
                    {"ok": False, "action": "start", "error": str(e)}, status=500
                )
        if action == "restart":
            try:
                from src.daemon.watch_controls import daemon_pid, restart_daemon

                msg = await asyncio.to_thread(restart_daemon, self.config)
                pid = daemon_pid(self.config)
                return web.json_response({
                    "ok": True,
                    "action": "restarted",
                    "pid": pid,
                    "message": msg,
                })
            except Exception as e:
                return web.json_response(
                    {"ok": False, "action": "restart", "error": str(e)}, status=500
                )
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

    async def _handle_audio_devices(self, request):
        """List audio devices via sounddevice, with active device markers."""
        try:
            import sounddevice as sd

            devices = []
            active_input = None
            active_output = None

            try:
                if self.config.audio_input_device_file.exists():
                    active_input = self.config.audio_input_device_file.read_text().strip() or None
            except Exception:
                pass
            try:
                if self.config.audio_output_device_file.exists():
                    active_output = self.config.audio_output_device_file.read_text().strip() or None
            except Exception:
                pass

            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass

            for i, dev in enumerate(sd.query_devices()):
                name = dev["name"]
                try:
                    hostapi_name = sd.query_hostapis(dev["hostapi"])["name"]
                except Exception:
                    hostapi_name = "?"
                devices.append({
                    "index": i,
                    "name": name,
                    "max_input_channels": int(dev.get("max_input_channels", 0)),
                    "max_output_channels": int(dev.get("max_output_channels", 0)),
                    "hostapi": hostapi_name,
                    "active_in": bool(active_input and active_input.lower() in name.lower()),
                    "active_out": bool(active_output and active_output.lower() in name.lower()),
                })
            return web.json_response({
                "devices": devices,
                "active_input": active_input,
                "active_output": active_output,
                "error": None,
            })
        except ImportError:
            return web.json_response({
                "devices": [],
                "active_input": None,
                "active_output": None,
                "error": "sounddevice not installed",
            })
        except Exception as e:
            return web.json_response({
                "devices": [],
                "active_input": None,
                "active_output": None,
                "error": str(e),
            }, status=500)

    async def _handle_chrome_launch(self, request):
        """Launch Chrome with CDP debug port, auto-selecting profile."""
        try:
            from src.daemon.browser import (
                ensure_debug_chrome,
                is_debug_reachable,
                list_chrome_profiles,
            )

            debug_port = 9222

            if is_debug_reachable(debug_port):
                return web.json_response({
                    "ok": True,
                    "status": f"chrome already attached on :{debug_port}",
                    "debug_port": debug_port,
                })

            body = await request.json()
            profile_directory = body.get("profile_directory")

            if profile_directory is None:
                profiles = list_chrome_profiles()
                if profiles:
                    profile_directory = profiles[0].directory

            msg = await asyncio.to_thread(
                ensure_debug_chrome, debug_port, profile_directory
            )
            return web.json_response({
                "ok": True,
                "status": msg,
                "debug_port": debug_port,
            })
        except Exception as e:
            return web.json_response(
                {"ok": False, "status": str(e), "debug_port": 9222}, status=500
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

            return web.json_response({
                "built_in": built_in,
                "skills": skills,
                "mcps": mcps,
                "error": error,
            })
        except Exception as e:
            return web.json_response({
                "built_in": [],
                "skills": [],
                "mcps": [],
                "error": str(e),
            }, status=500)

    async def _handle_chrome_profiles(self, request):
        """List Chrome profiles for the profile picker."""
        try:
            from src.daemon.browser import ChromeProfile, list_chrome_profiles

            profiles = list_chrome_profiles()
            return web.json_response({
                "profiles": [
                    {
                        "directory": p.directory,
                        "display_name": p.name or p.directory,
                        "last_used": p.last_used,
                    }
                    for p in profiles
                ],
                "error": None,
            })
        except Exception as e:
            return web.json_response({
                "profiles": [],
                "error": str(e),
            }, status=500)

    async def _handle_audio_device_select(self, request):
        """Select an audio device by writing its name to the hot-reload file."""
        try:
            body = await request.json()
            device_name = body.get("device_name", "").strip()
            kind = body.get("kind", "")
            if not device_name or kind not in ("input", "output"):
                return web.json_response(
                    {"ok": False, "error": "device_name and kind (input|output) required"},
                    status=400,
                )
            if kind == "input":
                self.config.audio_input_device_file.parent.mkdir(parents=True, exist_ok=True)
                self.config.audio_input_device_file.write_text(device_name)
            else:
                self.config.audio_output_device_file.parent.mkdir(parents=True, exist_ok=True)
                self.config.audio_output_device_file.write_text(device_name)
            return web.json_response({
                "ok": True,
                "kind": kind,
                "device": device_name,
            })
        except Exception as e:
            return web.json_response(
                {"ok": False, "error": str(e)}, status=500
            )

    async def _handle_bridge_status(self, request):
        """Return full bridge state: enabled, connected, pair code, token, ws url, port."""
        try:
            status_path = HEARE_HOME / "browser_bridge.status"
            port = self.config.browser_bridge_port
            enabled = self.config.browser_bridge_enabled
            has_token = bool(self.config.browser_bridge_token and len(self.config.browser_bridge_token) > 8)
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

            return web.json_response({
                "enabled": enabled,
                "port": port,
                "connected": connected,
                "has_token": has_token,
                "ws_url": f"ws://127.0.0.1:{port}",
                "pair_code": pair_code,
                "pair_remaining_s": round(pair_remaining_s, 1),
                "token_hint": token_hint,
            })
        except Exception as e:
            logger.exception("bridge/status failed")
            return web.json_response({
                "enabled": False,
                "port": self.config.browser_bridge_port,
                "connected": False,
                "has_token": False,
                "ws_url": f"ws://127.0.0.1:{self.config.browser_bridge_port}",
                "pair_code": None,
                "pair_remaining_s": 0.0,
                "error": str(e),
            })

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

            return web.json_response({
                "ok": True,
                "token": token,
                "restart_required": True,
                "message": "Restart the daemon for the new token to take effect.",
            })
        except Exception as e:
            logger.exception("bridge/rotate-token failed")
            return web.json_response(
                {"ok": False, "error": str(e)}, status=500
            )

    async def _handle_bridge_toggle(self, request):
        """Toggle browser_bridge_enabled in config.toml."""
        try:
            body = await request.json()
            enabled = bool(body.get("enabled", True))
        except Exception:
            return web.json_response({"ok": False, "error": "invalid JSON body"}, status=400)

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
                enabled_re = re.compile(r'(?m)^\s*enabled\s*=.*$')
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
                content = existing[: match.start()] + new_block + existing[match.end():]
            else:
                new_section = (
                    f"[browser_bridge]\n"
                    f"enabled = {'true' if enabled else 'false'}\n"
                    f"port = {self.config.browser_bridge_port}\n"
                )
                sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
                content = existing + sep + ("\n" if existing else "") + new_section

            fd, tmp_path = tempfile.mkstemp(
                prefix=".config.toml.", suffix=".tmp", dir=str(config_path.parent),
            )
            with os.fdopen(fd, "w") as fh:
                fh.write(content)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, config_path)

            # Also update the in-memory config
            self.config.browser_bridge_enabled = enabled

            return web.json_response({
                "ok": True,
                "enabled": enabled,
                "restart_required": True,
                "message": "Restart the daemon for the change to take effect.",
            })
        except Exception as e:
            logger.exception("bridge/toggle failed")
            return web.json_response(
                {"ok": False, "error": str(e)}, status=500
            )

    # ── Prompt Manager endpoints ─────────────────────────

    async def _handle_prompts(self, request):
        try:
            from pathlib import Path
            from src.agent.llm.prompt_sections import PROMPT_SECTIONS

            project_root = Path(__file__).resolve().parent.parent
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
                    preview = "(computed from identity data at render time)"
                elif ps.source == "dynamic":
                    preview = f"(computed from current {ps.key} state at render time)"
                results.append({
                    "key": ps.key,
                    "order": ps.order,
                    "source": ps.source,
                    "template_path": ps.template_path,
                    "char_count": char_count,
                    "content_preview": preview,
                })
            return web.json_response(results)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_prompt_section(self, request):
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
                return web.json_response({"error": f"unknown key: {key}"}, status=404)

            if section.source == "template" and section.template_path:
                tpath = project_root / section.template_path
                if tpath.exists():
                    content = tpath.read_text()
                    return web.json_response({
                        "key": section.key,
                        "order": section.order,
                        "source": section.source,
                        "template_path": section.template_path,
                        "char_count": len(content),
                        "content": content,
                    })
                return web.json_response({
                    "key": section.key,
                    "source": section.source,
                    "template_path": section.template_path,
                    "content": None,
                    "note": f"template not found: {section.template_path}",
                })

            return web.json_response({
                "key": section.key,
                "source": section.source,
                "content": None,
                "note": (
                    "computed at render time from identity data"
                    if section.source == "inline"
                    else "computed at render time from context"
                ),
            })
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
                return web.json_response({"ok": False, "error": f"unknown key: {key}"}, status=404)
            if section.source != "template":
                return web.json_response(
                    {"ok": False, "error": f"section '{key}' is not template-backed"}, status=400
                )
            if not section.template_path:
                return web.json_response(
                    {"ok": False, "error": f"section '{key}' has no template_path"}, status=400
                )

            # Defense-in-depth: ensure template_path stays within prompts/
            tpath = (project_root / section.template_path).resolve()
            prompts_dir = (project_root / "prompts").resolve()
            try:
                tpath.relative_to(prompts_dir)
            except ValueError:
                return web.json_response({"ok": False, "error": "invalid template path"}, status=400)

            body = await request.json()
            content = body.get("content", "")
            if not isinstance(content, str):
                return web.json_response({"ok": False, "error": "content must be a string"}, status=400)

            tpath.write_text(content)
            return web.json_response({"ok": True, "key": key, "char_count": len(content)})
        except Exception as e:
            logger.exception("prompt save failed for key=%s", key)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_prompt_preview(self, request):
        try:
            from src.agent.llm.context_injector import render_native_system_prompt

            preview = render_native_system_prompt(
                persona=(
                    "You are kort ⚡ — a digital creature.\n"
                    "Vibe: curious, warm, helpful.\n"
                    "You belong to Nazar. You speak Ukrainian."
                ),
                language="uk",
                context={
                    "recent_transcripts": [],
                    "conversation_summary": "No previous conversation.",
                    "active_topics": [],
                    "entities": [],
                    "recent_actions": [],
                },
            )
            return web.Response(text=preview, content_type="text/plain; charset=utf-8")
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    def _available_providers(self):
        return get_available(self.config)
