"""What the ear kept, for the person to hear and mark: was it said to the
assistant, or around it, and what was really said.

The voice writes ``<stamp>.wav`` and ``<stamp>.json`` side by side; the
pieces of one reply share a ``turn``. The door reads that folder on this
machine — while the brain lives beside the voice. When the brain moves to a
server, the voice has to hand the recordings over itself.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

LABELS = ("me", "background")
# A stamp as the voice writes it: 20261024-223200-123-00
NAME = re.compile(r"\d{8}-\d{6}-\d{3}(?:-\d{2})?")
TRUTH_CHARS = 600


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class Recordings:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _metas(self) -> list[tuple[Path, dict]]:
        found = []
        for path in sorted(self.root.glob("*.json")):
            if NAME.fullmatch(path.stem) and (data := _read(path)) is not None:
                found.append((path, data))
        return found

    def replies(self, limit: int = 60) -> dict:
        """The newest replies first, each with its pieces in the order heard.
        A recording from before replies were numbered is a reply of its own."""
        groups: dict[str, list[tuple[Path, dict]]] = {}
        for path, data in self._metas():
            key = str(data.get("turn") or path.stem)
            groups.setdefault(key, []).append((path, data))
        replies = []
        for key, pieces in groups.items():
            first = pieces[0][1]
            replies.append({
                "turn": key,
                "at": first.get("at"),
                "ms": sum(int(d.get("ms") or 0) for _, d in pieces),
                "text": " ".join(str(d.get("text") or "") for _, d in pieces
                                 if not d.get("dropped")).strip(),
                "dropped": all(d.get("dropped") for _, d in pieces),
                "pieces": [{"name": p.stem, "ms": d.get("ms"), "text": d.get("text") or "",
                            "dropped": d.get("dropped"), "mean_db": d.get("mean_db")}
                           for p, d in pieces],
                "label": first.get("label"),
                "truth": first.get("truth"),
            })
        replies.sort(key=lambda r: r["at"] or 0, reverse=True)
        marked = sum(1 for r in replies if r["label"])
        return {"replies": replies[:limit], "total": len(replies), "marked": marked}

    def sound(self, name: str) -> Path | None:
        if not NAME.fullmatch(name):
            return None
        path = self.root / f"{name}.wav"
        return path if path.is_file() else None

    def mark(self, turn: str, label: object, truth: object = None) -> int:
        """Mark every piece of one reply; how many were marked. A label
        that is not one of ours, or text that is not text, is the page's
        mistake and raises ValueError."""
        if label not in LABELS:
            raise ValueError("label is «me» or «background»")
        if truth is not None and not isinstance(truth, str):
            raise ValueError("truth is text")
        truth = " ".join(truth.split())[:TRUTH_CHARS] if truth else None
        marked = 0
        for path, data in self._metas():
            if str(data.get("turn") or path.stem) != turn:
                continue
            data["label"] = label
            data["truth"] = truth if label == "me" else None
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            marked += 1
        return marked
