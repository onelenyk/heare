"""Phrases pre-rendered into the TTS cache so they play instantly.

Warming these at startup lets cancel/confirm/fallback phrases fire in
<50ms instead of ~700ms via live Edge TTS. Add a phrase here + restart
the daemon to pick it up.

This module owns the list; `src/decider.py` and anywhere else that
previously owned it re-exports from here (to be deleted in Phase 2.7).
"""
from __future__ import annotations

FIXED_PHRASES: list[str] = [
    "okay",
    "nevermind, cancelled",
    "Скажи: так чи ні?",
    "дія не вдалася",
    "Скажи: гава так, або гава ні",
    "Скажи пароль, або гава ні",
    "Хвилинку, щось не так.",
]
