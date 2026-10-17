"""Settings, read from the same ``~/.heare/config.toml`` the old app uses.

Live: the file is looked at again whenever it changes, so a provider or a
model chosen while the assistant runs takes effect on the next reply. A
dead lever is worse than no lever.

Only ``heare.app`` reads this. Parts are handed the object and ask it for
plain values; none of them knows there is a file.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from heare import keys

HOME = Path.home() / ".heare"


# What a setting is when the file does not say. Only what a part cannot
# sensibly default by itself belongs here.
DEFAULTS = {
    "identity_file": HOME / "identity.json",
    "db_path": HOME / "heare.db",
    "screens_dir": HOME / "screens",
    "canvas_file": HOME / "canvas.json",
    "intents_path": HOME / "intents.json",
    "parts_file": HOME / "parts.json",
    "api_token_file": HOME / "api_token",
    "api_port": 9790,
    # One agent does the work unless the person names another; two at
    # once made three versions of one program in twenty minutes.
    "executor": "opencode",
    # Where agents work. Never the home folder: on 12 October an agent
    # left files in four places there.
    "executor_cwd": Path.home() / "heare-work",
}


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

    def view(self) -> dict:
        """The plain settings as the file has them — never a key."""
        return {
            k: v for k, v in self._read().items()
            if isinstance(v, (str, int, float, bool)) and "key" not in k.lower()
        }

    def set(self, name: str, value: str | int | float | bool) -> None:
        """Change one plain setting in the file. Heard on the next turn."""
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or "key" in name:
            raise ValueError(f"not a setting: {name!r}")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("a setting is text, a number or a switch")
        rendered = (
            ("true" if value else "false") if isinstance(value, bool)
            else json.dumps(value, ensure_ascii=False)
        )
        line = f"{name} = {rendered}"
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        # Top-level keys end where the first table begins.
        top = next((i for i, text in enumerate(lines) if text.lstrip().startswith("[")),
                   len(lines))
        for i in range(top):
            if re.match(rf"\s*{name}\s*=", lines[i]):
                lines[i] = line
                break
        else:
            lines.insert(top, line)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._seen = None
