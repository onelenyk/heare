"""Minimal HTTP API for daemon control — backs the desktop app."""
import sqlite3

from aiohttp import web
from src.agent.identity import load_identity
from src.agent.llm.providers import get_available
from src.agent.modes import VALID_MODES
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
        self._app.router.add_get("/state", self._handle_state)
        self._app.router.add_post("/mode", self._handle_mode)
        self._app.router.add_post("/mute", self._handle_mute)
        self._app.router.add_post("/provider", self._handle_provider)
        self._app.router.add_post("/model", self._handle_model)
        self._app.router.add_post("/cancel", self._handle_cancel)
        self._app.router.add_get("/activity", self._handle_activity)
        self._app.router.add_get("/logs", self._handle_logs)
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/canvas", self._handle_canvas)
        self._runner = None
        self._site = None

    async def start(self):
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 9778)
        await self._site.start()

    async def stop(self):
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()

    # ── Handlers ───────────────────────────────────────────

    async def _handle_index(self, request):
        from src.desktop.app import HTML

        return web.Response(text=HTML, content_type="text/html")

    async def _handle_state(self, request):
        data = self.state.snapshot()
        data["providers"] = self._available_providers()

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
            vs = read_voice_state(self.config.voice_state_file)
            data["voice_state"] = {
                "state": vs.state,
                "since_ts": vs.since_ts,
                "last_partial": vs.last_partial,
                "last_final": vs.last_final,
            }
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
        body = await request.json()
        mode = body.get("mode", "focus")
        if mode not in VALID_MODES:
            return web.json_response({"ok": False}, status=400)
        await self.state.set("mode", mode)
        return web.json_response({"ok": True, "mode": mode})

    async def _handle_mute(self, request):
        body = await request.json()
        target = body.get("target", "speaker")
        key = "mute_mic" if target == "mic" else "mute_bot"
        current = self.state.get_bool(key)
        await self.state.set_bool(key, not current)
        return web.json_response({"ok": True, "target": target, "muted": not current})

    async def _handle_provider(self, request):
        body = await request.json()
        provider = body.get("provider", "")
        if provider not in self._available_providers():
            return web.json_response({"ok": False}, status=400)
        await self.state.set("provider", provider)
        return web.json_response({"ok": True, "provider": provider})

    async def _handle_model(self, request):
        body = await request.json()
        model = body.get("model", "")
        if not model:
            return web.json_response({"ok": False}, status=400)
        await self.state.set("model", model)
        return web.json_response({"ok": True, "model": model})

    async def _handle_cancel(self, request):
        await self.state.set("cancel", "1")
        return web.json_response({"ok": True})

    async def _handle_canvas(self, request):
        """Return latest unrendered canvas content."""
        try:
            row = await self.state.get_latest_canvas()
            if row:
                return web.json_response(row)
            return web.json_response({"html": None, "ts": None})
        except Exception:
            return web.json_response({"html": None, "ts": None})

    def _available_providers(self):
        return get_available(self.config)
