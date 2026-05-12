"""FastAPI backend for the overlay window.

Contract
--------

* Read endpoints aggregate the JSON state files the pipeline already
  writes (``voice_state_file``, ``audio_event_file``) and the flag
  files it reads (``mute_file``, ``mute_input_file``).
* Write endpoints touch / remove the same flag files using the helpers
  in :mod:`src.pipeline.stages.mute_gate` and
  :mod:`src.pipeline.stages.cancel_flag_gate` so the contract stays in
  one place.
* The app is intended to be served on ``127.0.0.1`` only —
  :func:`bind_host` returns the loopback address so callers don't have
  to remember.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import Settings
from src.pipeline.stages.cancel_flag_gate import request_cancel
from src.pipeline.stages.mute_gate import (
    is_input_muted,
    is_muted,
    set_input_mute,
    set_mute,
)


logger = logging.getLogger("heare.overlay")

DAEMON_FRESHNESS_SECONDS = 10.0
STATIC_DIR = Path(__file__).parent / "static"


def bind_host() -> str:
    """Localhost-only — overlay must never bind a routable interface."""
    return "127.0.0.1"


class MuteToggle(BaseModel):
    on: bool


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.exception("overlay: failed to read %s", path)
        return None


def _daemon_online(state_path: Path) -> bool:
    if not state_path.exists():
        return False
    try:
        mtime = state_path.stat().st_mtime
    except OSError:
        return False
    return (time.time() - mtime) < DAEMON_FRESHNESS_SECONDS


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="heare-overlay", docs_url=None, redoc_url=None)

    @app.get("/api/state")
    def _state() -> JSONResponse:
        return JSONResponse(
            {
                "voice_state": _read_json(settings.voice_state_file),
                "audio_event": _read_json(settings.audio_event_file),
                "mute_output": is_muted(settings.mute_file),
                "mute_input": is_input_muted(settings.mute_input_file),
                "daemon_online": _daemon_online(settings.voice_state_file),
            }
        )

    @app.post("/api/mute/output")
    def _mute_output(toggle: MuteToggle) -> dict[str, bool]:
        return {"muted": set_mute(settings.mute_file, toggle.on)}

    @app.post("/api/mute/input")
    def _mute_input(toggle: MuteToggle) -> dict[str, bool]:
        return {"muted": set_input_mute(settings.mute_input_file, toggle.on)}

    @app.post("/api/cancel")
    def _cancel() -> dict[str, bool]:
        request_cancel(settings.cancel_flag_file)
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def _index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


__all__ = ["bind_host", "create_app"]
