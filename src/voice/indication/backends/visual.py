"""VisualBackend — append-only JSONL at <log_dir>/indication.jsonl.

Each `fire()` appends one JSON object and (cheaply) trims the file to its
last `keep_last` lines so it never grows unbounded. The watch dashboard
reads the tail of this file to render the indication panel.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.voice.indication.core import IndicationKind, IndicationLevel

logger = logging.getLogger("heare.indication.visual")


class VisualBackend:
    name = "visual"

    def __init__(self, path: Path, keep_last: int = 200) -> None:
        self._path = Path(path)
        self._keep_last = keep_last
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def fire(
        self,
        kind: "IndicationKind",
        level: "IndicationLevel",
        title: str,
        body: str,
        meta: dict,
    ) -> None:
        record = {
            "ts": _time.time(),
            "kind": kind.value,
            "level": level.value,
            "title": title,
            "body": body,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)
            self._maybe_trim()
        except OSError:
            logger.warning("indication.visual: write failed", exc_info=True)

    def _maybe_trim(self) -> None:
        try:
            with self._path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return
        if len(lines) <= self._keep_last:
            return
        kept = lines[-self._keep_last :]
        fd, tmp_name = tempfile.mkstemp(
            prefix=".indication.", suffix=".jsonl.tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(kept)
            os.replace(tmp_name, self._path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            logger.warning("indication.visual: trim failed", exc_info=True)

    async def aclose(self) -> None:
        return
