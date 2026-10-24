"""What the ear heard, kept: the sound of each phrase and what the
recogniser made of it, side by side.

Recognition is weighed on these, not on memory of how it felt: the same
sound can be sent again — whole instead of in pieces, with a hint, to
another recogniser — and the texts compared. Kept on this machine only,
the oldest going as new ones come.
"""

from __future__ import annotations

import array
import json
import logging
import math
import time
import wave
from pathlib import Path

logger = logging.getLogger("heare.voice.heard")

# A phrase is a few seconds, ~100 KB: five hundred is tens of megabytes,
# and enough of a person's speech to measure anything on.
KEEP = 500
RATE = 16000


def levels(pcm: bytes) -> dict:
    """How loud a phrase was, in dBFS: its peak and its mean. A voice close
    to the microphone and a television across the room differ here."""
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    if not samples:
        return {"peak_db": -120.0, "mean_db": -120.0}
    peak = max(abs(v) for v in samples)
    rms = math.sqrt(sum(v * v for v in samples) / len(samples))

    def db(value: float) -> float:
        return round(20 * math.log10(value / 32768.0), 1) if value else -120.0

    return {"peak_db": db(peak), "mean_db": db(rms)}


class Heard:
    def __init__(self, root: Path, keep: int = KEEP) -> None:
        self.root = root
        self.keep = keep

    def add(self, pcm: bytes, about: dict) -> Path | None:
        """One phrase: ``<stamp>.wav`` and ``<stamp>.json`` beside it.
        A failure to keep is logged, never raised: hearing goes on."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            now = time.time()
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) \
                + f"-{int(now * 1000) % 1000:03d}"
            # Two phrases in one millisecond: a count, of fixed width so
            # the names still sort in the order they were heard.
            more = 0
            while (path := self.root / f"{stamp}-{more:02d}.wav").exists():
                more += 1
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(pcm)
            meta = {"at": now, "ms": round(len(pcm) / (RATE * 2) * 1000), **about}
            path.with_suffix(".json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            self._trim()
            return path
        except OSError:
            logger.exception("could not keep what was heard")
            return None

    def _trim(self) -> None:
        sounds = sorted(self.root.glob("*.wav"))
        for old in sounds[: max(0, len(sounds) - self.keep)]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
