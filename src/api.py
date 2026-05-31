"""Minimal HTTP API for daemon control — backs the desktop app."""
from aiohttp import web
from src.agent.llm.providers import get_available
from src.agent.modes import VALID_MODES


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

    async def _handle_state(self, request):
        data = self.state.snapshot()
        # Add computed fields
        data["providers"] = self._available_providers()
        return web.json_response(data)

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

    def _available_providers(self):
        return get_available(self.config)
