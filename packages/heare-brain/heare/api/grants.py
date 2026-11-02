"""Which part may hold which key, for which code.

A part names the code it runs; a yes is for exactly that code, so new code
is a new question. A paired machine vouches for its parts, never for keys.
The fingerprint is the part's own word: it catches an update, not a liar.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Grants:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._yes: dict[str, dict[str, str]] = self._read()
        # In memory only: after a restart the part simply asks again.
        self.waiting: dict[tuple[str, str], dict[str, str]] = {}
        # A no holds for that code, or the card would be back in five seconds.
        self._no: dict[tuple[str, str], str] = {}

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fresh = self._path.with_suffix(".tmp")
        fresh.write_text(json.dumps(self._yes), encoding="utf-8")
        fresh.chmod(0o600)
        os.replace(fresh, self._path)

    def allowed(self, pid: str, key: str, fingerprint: str) -> bool:
        return bool(fingerprint) and self._yes.get(pid, {}).get(key) == fingerprint

    def ask(self, pid: str, key: str, fingerprint: str, version: str) -> None:
        if self._no.get((pid, key)) == fingerprint:
            return
        self.waiting[(pid, key)] = {
            "pid": pid, "key": key, "fingerprint": fingerprint, "version": version,
            "update": "yes" if key in self._yes.get(pid, {}) else "",
        }

    def answer(self, pid: str, key: str, allow: bool) -> bool:
        """Only what was really asked can be answered."""
        asked = self.waiting.pop((pid, key), None)
        if asked is None:
            return False
        if allow:
            self._yes.setdefault(pid, {})[key] = asked["fingerprint"]
        else:
            self._yes.get(pid, {}).pop(key, None)
            self._no[(pid, key)] = asked["fingerprint"]
        self._write()
        return True

    def forget(self, pid: str) -> None:
        self._yes.pop(pid, None)
        self.waiting = {k: v for k, v in self.waiting.items() if k[0] != pid}
        self._write()
