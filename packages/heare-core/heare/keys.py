"""Where a secret comes from: the environment, then the system keyring,
then a file.

The keyring is the macOS Keychain (service ``heare``, the store the old
app writes) or a desktop's Secret Service. A server has neither: there
the key lives in ``~/.heare/keys.json``, readable by its owner only — the
same protection the brain's own token has beside it. A key can be asked
for by name and is never listed, logged or returned through the API.

Two sources are explicit: a key may already be in the environment, or it
may be kept in the store. A caller can ask which one is in force and copy
the environment's value into the store (``adopt``) without the value ever
leaving this module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SERVICE = "heare"
NAMES = (
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENCODE_API_KEY",
    "ZAI_API_KEY",
)

# The test backend keeps keys in the process only, so a test can tell
# "set in the environment" from "stored" and never writes the Keychain —
# nor the developer's keys file.
_STORED: dict[str, str] = {}


def get(name: str) -> str | None:
    """The key, or None. The environment wins; then the store."""
    return _from_env(name) or _from_store(name)


def source(name: str) -> str | None:
    """Where the key in force comes from: ``"env"``, ``"stored"`` or None."""
    if _from_env(name) is not None:
        return "env"
    if _from_store(name) is not None:
        return "stored"
    return None


def sources() -> dict[str, str | None]:
    """The source of every key this assistant knows, by name."""
    return {name: source(name) for name in NAMES}


def view() -> dict[str, dict[str, str | bool | None]]:
    """What the doors show: each name, its source, and whether the
    environment and the store each hold it. Never the value."""
    seen: dict[str, dict[str, str | bool | None]] = {}
    for name in NAMES:
        env = _from_env(name) is not None
        stored = _from_store(name) is not None
        seen[name] = {
            "source": "env" if env else "stored" if stored else None,
            "env": env,
            "stored": stored,
        }
    return seen


def store(name: str, value: str) -> None:
    """Keep a key: in the keyring when this machine has one that answers,
    in the keys file when it does not; in tests, in the process only."""
    value = value.strip()
    if _in_process():
        _STORED[name] = value
        return
    ring = _keyring()
    if ring is not None:
        try:
            ring.set_password(SERVICE, name, value)
            return
        except Exception:  # noqa: BLE001 — a locked or absent daemon: the file keeps it
            pass
    _write_file(name, value)


def adopt(name: str) -> bool:
    """Copy the environment's key into the store; False if it is not there."""
    value = _from_env(name)
    if value is None:
        return False
    store(name, value)
    return True


def keys_file() -> Path:
    # Not from heare.config: the config reads its keys from here.
    return Path.home() / ".heare" / "keys.json"


def _from_env(name: str) -> str | None:
    return (os.environ.get(name) or "").strip() or None


def _from_store(name: str) -> str | None:
    if _in_process():
        return _STORED.get(name) or None
    ring = _keyring()
    if ring is not None:
        try:
            value = (ring.get_password(SERVICE, name) or "").strip()
        except Exception:  # noqa: BLE001 — no Keychain is "no key", not a crash
            value = ""
        if value:
            return value
    # Read even when a keyring exists: a key the file took while the
    # keyring would not answer must not be lost when it answers again.
    return (_read_file().get(name) or "").strip() or None


def _keyring():
    """The system keyring, or None when there is none that works here.

    keyring picks a backend that always fails when it finds nothing (a
    server with no desktop session); asking it would only raise."""
    try:
        import keyring
        from keyring.backends import fail

        ring = keyring.get_keyring()
    except Exception:  # noqa: BLE001 — a broken keyring install is no keyring
        return None
    if isinstance(ring, fail.Keyring) or ring.priority <= 0:
        return None
    return ring


def _read_file() -> dict[str, str]:
    try:
        data = json.loads(keys_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, str)}


def _write_file(name: str, value: str) -> None:
    path = keys_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_file()
    data[name] = value
    fresh = path.with_name(path.name + ".new")
    # Created 0600 rather than chmod-ed after: the key is never readable by
    # another user, not even between the write and the chmod.
    fd = os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(data, out, indent=1)
    os.chmod(fresh, 0o600)
    fresh.replace(path)


def _in_process() -> bool:
    # Named "file" for history: it was the test switch before there was a
    # real file, and it still means "this process only, never the disk".
    return os.environ.get("HEARE_KEYS_BACKEND", "").strip().lower() == "file"
