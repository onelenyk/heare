"""One-time codes: a way in for a machine or a window that has no key yet.

Eight characters from an alphabet without look-alikes, five minutes, one
use. The brain keeps only the hash: the code itself lives on the trusted
screen and in the person's hand, never on disk. Refusing is cheap, and the
server counts refusals as well as takes.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 8
TTL_S = 300.0


def fingerprint(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def _read(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): float(v) for k, v in data.items()
            if isinstance(v, (int, float))}


def _write(path: Path, codes: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(codes, indent=2), encoding="utf-8")
    path.chmod(0o600)


def make(path: Path, now: float | None = None) -> str:
    """A fresh code; only its hash and its death are kept."""
    now = time.time() if now is None else now
    code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    alive = {k: v for k, v in _read(path).items() if v > now}
    alive[fingerprint(code)] = now + TTL_S
    _write(path, alive)
    return code


def redeem(path: Path, code: str, now: float | None = None) -> bool:
    """Take a code once. A stale one is dropped where it lies."""
    now = time.time() if now is None else now
    codes = _read(path)
    key = fingerprint(code)
    if key not in codes:
        return False
    alive = codes[key] > now
    codes.pop(key, None)
    _write(path, {k: v for k, v in codes.items() if v > now})
    return alive
