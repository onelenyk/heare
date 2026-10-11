"""Settings, read from the same ``~/.heare/config.toml`` the old app uses.

Live: the file is looked at again whenever it changes, so a provider or a
model chosen while the assistant runs takes effect on the next reply. A
dead lever is worse than no lever.

Only ``heare.app`` reads this. Parts are handed the object and ask it for
plain values; none of them knows there is a file.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from heare import keys

HOME = Path.home() / ".heare"


# What a setting is when the file does not say. Only what a part cannot
# sensibly default by itself belongs here.
DEFAULTS = {"identity_file": HOME / "identity.json"}


class Settings:
    def __init__(self, path: Path | None = None, **overrides) -> None:
        self._path = path or HOME / "config.toml"
        self._overrides = overrides
        self._seen: float | None = None
        self._data: dict = {}

    def _read(self) -> dict:
        try:
            changed = self._path.stat().st_mtime
        except OSError:
            return {}
        if changed != self._seen:
            try:
                self._data = tomllib.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                self._data = {}
            self._seen = changed
        return self._data

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._overrides:
            return self._overrides[name]
        if name.endswith("_api_key"):
            return keys.get(name.upper())
        return self._read().get(name, DEFAULTS.get(name, ""))
