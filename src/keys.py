"""Where the API keys live.

An app a person installs from a menubar should not know what a `.env`
is. Secrets typed into a window go where the system keeps secrets: on
macOS that is the Keychain — encrypted by the login, scoped to the app
that wrote them, visible in Keychain Access, carried along by backups.
`~/.heare/.env` with mode 0600 is what a server-side CLI does, and it is
what this app did until 13 September 2026.

The rule is the one `gh` follows: Keychain first, a file only where
there is no Keychain. Three sources, in the order they win:

  1. the process environment — set explicitly, it wins, so
     ``GROQ_API_KEY=... uv run ...`` still works for development and CI;
  2. the macOS Keychain, service ``heare``, account = the variable name;
  3. ``~/.heare/.env`` — the fallback when there is no Keychain (another
     platform, or ``HEARE_KEYS_BACKEND=file``), loaded into the
     environment by ``load_env`` and therefore already covered by (1).

A `.env` that still holds keys on a machine with a Keychain is migrated
on the first boot: each key is copied in, read back, and only then
removed from the file. Lines that are not keys stay.

The Keychain is reached through ``keyring``, which talks to the Security
framework in-process — never through the ``security`` CLI, which would
put the secret on a command line for the length of the call.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("heare.keys")

SERVICE = "heare"
BACKEND_ENV = "HEARE_KEYS_BACKEND"  # "keychain" | "file"; unset = decide


def key_names() -> list[str]:
    """Every environment-variable name this app treats as a secret."""
    from src.config import api_key_fields

    names = list(api_key_fields().values())
    names.append("SERPER_API_KEY")
    return names


def _keyring():
    import keyring

    return keyring


def backend() -> str:
    """``"keychain"`` or ``"file"`` — where a stored key goes.

    Forced by ``HEARE_KEYS_BACKEND``; otherwise the Keychain on macOS
    when ``keyring`` resolves to its macOS backend, and a file anywhere
    else. Not cached: it is a platform check and an environment read.
    """
    forced = os.environ.get(BACKEND_ENV, "").strip().lower()
    if forced in ("keychain", "file"):
        return forced
    if sys.platform != "darwin":
        return "file"
    try:
        chosen = _keyring().get_keyring()
    except Exception:  # noqa: BLE001 — no backend is "file", not a crash
        return "file"
    return "keychain" if "macOS" in type(chosen).__module__ else "file"


def get(name: str) -> str | None:
    """The key, or None. The environment wins; then the Keychain."""
    value = os.environ.get(name)
    if value is not None:
        return value.strip() or None
    if backend() != "keychain":
        return None
    try:
        value = _keyring().get_password(SERVICE, name)
    except Exception:  # noqa: BLE001
        logger.debug("%s: Keychain read failed", name, exc_info=True)
        return None
    return (value or "").strip() or None


def store(name: str, value: str) -> str:
    """Persist ``value`` and apply it to this process.

    Returns where it went — ``"keychain"`` or ``"file"`` — so the caller
    can say so. A Keychain write is read back before it counts: a write
    that "succeeded" and cannot be read is the shape of bug this app has
    had before, and a key is the worst place to have it.
    """
    value = value.strip()
    if backend() == "keychain":
        kr = _keyring()
        kr.set_password(SERVICE, name, value)
        if (kr.get_password(SERVICE, name) or "") != value:
            raise RuntimeError(f"{name}: the Keychain did not keep the value")
        home = "keychain"
    else:
        from src.config import write_env_updates

        write_env_updates({name: value})
        home = "file"
    os.environ[name] = value
    return home


def where(name: str) -> str | None:
    """``"shell"``, ``"file"``, ``"keychain"`` or None — for the doctor.

    "shell" is any environment value the `.env` file does not explain,
    which after ``load_env`` means the shell profile or the command line.
    """
    in_env = os.environ.get(name)
    if in_env:
        try:
            from dotenv import dotenv_values

            from src.config import ENV_PATH

            if ENV_PATH.exists() and (dotenv_values(ENV_PATH) or {}).get(name) == in_env:
                return "file"
        except Exception:  # noqa: BLE001
            logger.debug("could not read %s", name, exc_info=True)
        return "shell"
    if backend() == "keychain" and get(name):
        return "keychain"
    return None


def migrate_env_file(env_path: Path | None = None) -> list[str]:
    """Move the keys in ``~/.heare/.env`` into the Keychain. Returns the
    names moved.

    Only on a Keychain backend, only for names this app owns, and each
    one is read back from the Keychain before its line leaves the file.
    A key the Keychain already holds with a *different* value is left in
    the file with a warning: the file wins at runtime while it is there,
    and choosing between two secrets is not a boot's decision to make.
    """
    if backend() != "keychain":
        return []
    from src.config import ENV_PATH

    path = env_path or ENV_PATH
    if not path.exists():
        return []
    from dotenv import dotenv_values

    owned = set(key_names())
    in_file = {
        name: value
        for name, value in (dotenv_values(path) or {}).items()
        if name in owned and value
    }
    if not in_file:
        return []

    kr = _keyring()
    moved: list[str] = []
    for name, value in in_file.items():
        try:
            existing = kr.get_password(SERVICE, name)
            if existing and existing != value:
                logger.warning(
                    "%s: the Keychain holds a different value than %s; the "
                    "file's wins while it is there — delete one of them",
                    name,
                    path,
                )
                continue
            if not existing:
                kr.set_password(SERVICE, name, value)
                if kr.get_password(SERVICE, name) != value:
                    raise RuntimeError("read back a different value")
            moved.append(name)
        except Exception:  # noqa: BLE001
            logger.exception("%s: could not move into the Keychain; left in %s", name, path)

    if moved:
        _remove_from_env_file(path, set(moved))
        logger.info(
            "keys moved from %s into the macOS Keychain: %s", path, ", ".join(moved)
        )
    return moved


def _remove_from_env_file(path: Path, names: set[str]) -> None:
    """Drop the assignments in ``names``; keep every other line. A file
    left with nothing but blank lines is removed rather than kept empty."""
    from src.config import _atomic_write

    kept: list[str] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            if stripped.split("=", 1)[0].strip() in names:
                continue
        kept.append(line)
    if any(line.strip() for line in kept):
        _atomic_write(path, "\n".join(kept) + "\n")
    else:
        path.unlink(missing_ok=True)


__all__ = ["SERVICE", "BACKEND_ENV", "backend", "get", "store", "where", "migrate_env_file", "key_names"]
