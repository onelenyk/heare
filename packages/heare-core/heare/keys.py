"""Where a secret comes from: the environment, then the macOS Keychain.

The same store the old app writes (service ``heare``), read only. A key
can be asked for by name and is never listed, logged or returned through
the API.
"""

from __future__ import annotations

import os

SERVICE = "heare"


def get(name: str) -> str | None:
    """The key, or None. The environment wins; then the Keychain."""
    value = os.environ.get(name)
    if value is not None:
        return value.strip() or None
    if _in_a_file():
        return None
    try:
        import keyring

        value = keyring.get_password(SERVICE, name)
    except Exception:  # noqa: BLE001 — no Keychain is "no key", not a crash
        return None
    return (value or "").strip() or None


def store(name: str, value: str) -> None:
    """Keep a key. In the Keychain; in tests, in the process only — a
    test must never write the developer's own Keychain."""
    value = value.strip()
    if _in_a_file():
        os.environ[name] = value
        return
    import keyring

    keyring.set_password(SERVICE, name, value)


def _in_a_file() -> bool:
    return os.environ.get("HEARE_KEYS_BACKEND", "").strip().lower() == "file"
