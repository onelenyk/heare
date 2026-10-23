"""Settings, read from ``~/.heare/config.toml``.

Live: the file is looked at again whenever it changes, so a provider or a
model chosen while the assistant runs takes effect on the next reply. A
dead lever is worse than no lever.

Every distribution that needs a setting makes its own Settings and asks it
for plain values; the file is the one place they agree on. The port is the
exception to "live": a socket is bound once, so a new port needs a restart.
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
    "memory_dir": HOME / "memory",
    "events_dir": HOME / "events",
    "screens_dir": HOME / "screens",
    "canvas_file": HOME / "canvas.json",
    "intents_path": HOME / "intents.json",
    "parts_file": HOME / "parts.json",
    "part_tokens_file": HOME / "part_tokens.json",
    "pair_codes_file": HOME / "pair_codes.json",
    "parts_dir": HOME / "parts",
    "api_token_file": HOME / "api_token",
    "machine_token_file": HOME / "machine_token",
    # Where the brain is. Empty means this machine — a body on another one
    # is told once, by the setup form.
    "url": "",
    "api_port": 9790,
    # This machine only. A container sets 0.0.0.0 so its published port
    # reaches it; the master token still guards every route but the page.
    "api_host": "127.0.0.1",
    # Where the keeper's setup form listens. Fixed, with a fallback to the
    # next free port; the address is printed when the form starts.
    "setup_port": 9791,
    # One agent does the work unless the person names another; two at
    # once made three versions of one program in twenty minutes.
    "executor": "opencode",
    # Which CLI and how it signs in. A blank binary means "find the known
    # one"; a key is named by its variable, never kept here.
    "executor_binary": "",
    "executor_model": "",
    "executor_auth": "login",
    "executor_auth_env": "",
    "executor_base_url": "",
    # Where agents work. Never the home folder: on 12 October an agent
    # left files in four places there.
    "executor_cwd": Path.home() / "heare-work",
    "context_tokens": 6000,
    # Proactivity has brakes: no unsolicited news at night, and only this
    # many news intents a day. Reminders the person asked for are exempt.
    "quiet_from": "22:00",
    "quiet_to": "08:00",
    "unsolicited_per_day": 1,
    # A daily pass over yesterday's conversation, in local time. It only
    # proposes facts; the owner still confirms them.
    "review_enabled": True,
    "review_hour": 4,
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

    @property
    def base(self) -> str:
        """Where the brain is: its own machine unless the file names another."""
        url = str(self.url).rstrip("/")
        return url or f"http://127.0.0.1:{self.api_port}"

    @property
    def secret(self) -> str:
        """What a part knocks with: a machine's own pairing first, the master
        second. A machine that paired for a brain elsewhere has no master."""
        machine = Path(self.machine_token_file)
        if machine.is_file():
            return machine.read_text(encoding="utf-8").strip()
        return Path(self.api_token_file).read_text(encoding="utf-8").strip()

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
