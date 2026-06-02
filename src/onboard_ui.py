"""Lightweight onboarding web server.

Runs BEFORE the full daemon starts when no API keys are configured.
Serves a setup page where the user enters their API keys, then signals
completion so the daemon can proceed with the real startup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import webbrowser
from pathlib import Path

from aiohttp import web

logger = logging.getLogger("heare.onboard_ui")

_ONBOARD_HTML: str | None = None
PORT = 9778


def _get_html() -> str:
    global _ONBOARD_HTML
    if _ONBOARD_HTML is None:
        for candidate in [
            Path(__file__).resolve().parent / "frontend" / "onboarding.html",
            Path(__file__).resolve().parent.parent / "src" / "frontend" / "onboarding.html",
        ]:
            if candidate.exists():
                _ONBOARD_HTML = candidate.read_text()
                break
        if _ONBOARD_HTML is None:
            raise FileNotFoundError("onboarding.html not found")
    return _ONBOARD_HTML


async def run_onboarding(project_dir: str | None = None) -> bool:
    """Run the onboarding server, blocking until the user completes setup.

    Returns True if onboarding completed successfully, False on error.
    """
    done_event = asyncio.Event()
    save_error: str | None = None

    async def handle_index(request: web.Request) -> web.Response:
        return web.Response(text=_get_html(), content_type="text/html")

    async def handle_settings_status(request: web.Request) -> web.Response:
        groq = bool(
            os.environ.get("GROQ_API_KEY", "").startswith("gsk_")
            or os.environ.get("GROQ_API_KEY", "").startswith("sk-")
        )
        return web.json_response({
            "configured": groq,
            "groq_key": groq,
            "language": "en",
            "tts_voice": "en-US-AriaNeural",
            "mode": "focus",
        })

    async def handle_settings(request: web.Request) -> web.Response:
        nonlocal save_error
        body = await request.json()

        env_path = _find_env(project_dir)
        if env_path is None:
            save_error = "Could not locate .env file"
            return web.json_response({"ok": False, "error": save_error}, status=500)

        updates: dict[str, str] = {}

        # Map provider-specific keys to their env var names
        provider_keys = {
            "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "ZAI_API_KEY",
            "OPENCODE_API_KEY", "GROQ_API_KEY",
        }

        for key, value in body.items():
            upper = key.upper()
            if upper in provider_keys and value:
                updates[upper] = value
            elif upper == "GROQ_API_KEY" and value:
                updates["GROQ_API_KEY"] = value

        if updates:
            existing: dict[str, str] = {}
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if "=" in line and not line.startswith("#"):
                            k, v = line.split("=", 1)
                            existing[k.strip()] = v.strip()
            existing.update(updates)
            env_path = str(env_path)
            with open(env_path, "w") as f:
                for k, v in existing.items():
                    f.write(f"{k}={v}\n")
            os.environ.update(updates)

        # Write config.toml for language/voice (skip if already exists
        # with valid settings — we only seed defaults, never overwrite)
        config_path = os.path.expanduser("~/.heare/config.toml")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        if not os.path.exists(config_path):
            lang = body.get("language", "en")
            voice = body.get("tts_voice", "en-US-AriaNeural")
            with open(config_path, "w") as f:
                f.write(f'groq_language = "{lang}"\n')
                f.write(f'tts_voice = "{voice}"\n')
                f.write('mode = "focus"\n')

        return web.json_response({"ok": True, "restart_needed": True})

    async def handle_done(request: web.Request) -> web.Response:
        done_event.set()
        return web.json_response({"ok": True})

    # Suppress aiohttp access logs
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/settings/status", handle_settings_status)
    app.router.add_post("/settings", handle_settings)
    app.router.add_post("/done", handle_done)

    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", PORT)
        await site.start()
    except OSError:
        logger.warning("Port %d in use — checking if daemon already running", PORT)
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"http://127.0.0.1:{PORT}/state", timeout=2)
                if r.status_code == 200:
                    logger.info("Daemon already running — opening dashboard")
                    webbrowser.open(f"http://127.0.0.1:{PORT}/")
                    await runner.cleanup()
                    return True
        except Exception:
            pass
        logger.error("Port %d occupied by unknown process", PORT)
        await runner.cleanup()
        return False

    logger.info("Onboarding server at http://127.0.0.1:%d", PORT)

    # Open the browser
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
    except Exception:
        logger.warning("Could not open browser automatically")

    # Wait for user to complete onboarding
    try:
        await asyncio.wait_for(done_event.wait(), timeout=600)  # 10 min timeout
        logger.info("Onboarding complete — proceeding to daemon start")
    except asyncio.TimeoutError:
        logger.warning("Onboarding timed out after 10 minutes")
    finally:
        await site.stop()
        await runner.cleanup()

    return not save_error and done_event.is_set()


def _find_env(project_dir: str | None) -> Path:
    """Find the .env file to write to. Prefers ~/.heare/.env so settings
    persist across app updates when running from a read-only bundle."""
    home_env = Path.home() / ".heare" / ".env"
    if home_env.exists():
        return home_env

    for p in [
        home_env,
        Path.cwd() / ".env",
    ]:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        except OSError:
            continue

    return home_env
