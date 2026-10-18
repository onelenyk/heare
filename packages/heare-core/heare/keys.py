"""Where a secret comes from: the environment, then the macOS Keychain.

The same store the old app writes (service ``heare``), read only. A key
can be asked for by name and is never listed, logged or returned through
the API.

Two sources are explicit: a key may already be in the environment, or it
may be kept in the store. A caller can ask which one is in force and copy
the environment's value into the store (``adopt``) without the value ever
leaving this module.
"""

from __future__ import annotations

import os

SERVICE = "heare"
NAMES = (
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENCODE_API_KEY",
    "ZAI_API_KEY",
)

# The test backend keeps keys in the process only, so a test can tell
# "set in the environment" from "stored" and never writes the Keychain.
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
    """Keep a key. In the Keychain; in tests, in the process only — a
    test must never write the developer's own Keychain."""
    value = value.strip()
    if _in_a_file():
        _STORED[name] = value
        return
    import keyring

    keyring.set_password(SERVICE, name, value)


def adopt(name: str) -> bool:
    """Copy the environment's key into the store; False if it is not there."""
    value = _from_env(name)
    if value is None:
        return False
    store(name, value)
    return True


def _from_env(name: str) -> str | None:
    return (os.environ.get(name) or "").strip() or None


def _from_store(name: str) -> str | None:
    if _in_a_file():
        return _STORED.get(name) or None
    try:
        import keyring

        value = keyring.get_password(SERVICE, name)
    except Exception:  # noqa: BLE001 — no Keychain is "no key", not a crash
        return None
    return (value or "").strip() or None


def _in_a_file() -> bool:
    return os.environ.get("HEARE_KEYS_BACKEND", "").strip().lower() == "file"
