"""Who is knocking: the person's own key, or a part's own.

The master key is the person's — it opens the whole door. A part gets a
key of its own when it is approved: one secret, shown once, kept here as
a fingerprint. With it a part can do only what a part may do, and
forgetting the part revokes that key the same moment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path


def _read(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _write(path: Path, tokens: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    path.chmod(0o600)


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(path: Path, pid: str) -> str:
    """A new key for one part; the one it had, if any, stops working."""
    token = secrets.token_urlsafe(32)
    tokens = _read(path)
    tokens[pid] = fingerprint(token)
    _write(path, tokens)
    return token


def revoke(path: Path, pid: str) -> bool:
    """Take one part's key away. True if it had one."""
    tokens = _read(path)
    if tokens.pop(pid, None) is None:
        return False
    _write(path, tokens)
    return True


def owner(path: Path, token: str) -> str | None:
    """The pid this token belongs to, if it is any part's token."""
    if not token:
        return None
    want = fingerprint(token)
    for pid, have in _read(path).items():
        if hmac.compare_digest(have, want):
            return pid
    return None
