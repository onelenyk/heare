"""Pipeline event system — structured observability for every stage.

Events are written to:
  1. daemon.log  — human-readable line with stage prefix
  2. events.jsonl — machine-parseable JSON (one per line)
  3. _ring buffer — last 200 events for API polling
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("heare.events")

STAGE_LABELS = {
    "vad": "🎤 VAD",
    "stt": "📝 STT",
    "gate": "🚪 GATE",
    "prompt": "💬 PROMPT",
    "llm": "🧠 LLM",
    "tts": "🔊 TTS",
    "mute": "🔇 MUTE",
    "echo": "🔄 ECHO",
    "browser": "🌐 BROWSER",
    "system": "⚙️ SYSTEM",
}

LEVEL_ORDER = {"critical": 0, "important": 1, "info": 2, "debug": 3}


@dataclass
class PipelineEvent:
    stage: str
    event: str
    level: str = "info"
    ts: float = field(default_factory=time.time)
    data: dict = field(default_factory=dict)


_ring: deque[PipelineEvent] = deque(maxlen=200)
_events_file: Path | None = None
_last_rate_limited: dict[str, float] = {}


def setup(log_dir: Path) -> None:
    global _events_file
    _events_file = log_dir / "events.jsonl"


def emit(stage: str, event: str, level: str = "info", **data) -> None:
    """Emit a pipeline event. Thread-safe for asyncio (single thread)."""
    e = PipelineEvent(stage=stage, event=event, level=level, data=data)
    _ring.append(e)

    label = STAGE_LABELS.get(stage, stage)
    log_msg = f"[{label}] {event}"
    if data:
        extras = " ".join(f"{k}={v}" for k, v in list(data.items())[:3])
        log_msg += f" ({extras})"

    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.log(log_level, log_msg)

    if _events_file:
        try:
            with open(_events_file, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "stage": stage,
                            "event": event,
                            "level": level,
                            "ts": e.ts,
                            "data": data,
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass


def rate_limited(
    stage: str, event: str, key: str, interval: float = 5.0, level: str = "info", **data
) -> bool:
    """Emit only if the same (stage, key) hasn't been emitted in ``interval`` seconds.
    Returns True if emitted, False if suppressed."""
    now = time.time()
    rk = f"{stage}:{key}"
    if rk in _last_rate_limited and (now - _last_rate_limited[rk]) < interval:
        return False
    _last_rate_limited[rk] = now
    emit(stage, event, level=level, **data)
    return True


def recent(limit: int = 50) -> list[dict]:
    """Return last N events for API polling."""
    return [
        {
            "stage": e.stage,
            "event": e.event,
            "level": e.level,
            "ts": e.ts,
            "data": e.data,
        }
        for e in list(_ring)[-limit:]
    ]


__all__ = ["PipelineEvent", "emit", "rate_limited", "recent", "setup", "STAGE_LABELS"]
