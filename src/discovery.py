"""Capability discovery — local CapabilityIndex + remote marketplace + MCP registry.

Remote results are cached for 24h under ``~/.heare/cache/discovery/<intent_hash>.json``
with HMAC integrity validation keyed off ``~/.heare/identity.json``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import time
import unicodedata
from pathlib import Path
from .capability_index import CapabilityIndex, IndexEntry

logger = logging.getLogger("heare.discovery")

DEFAULT_CACHE_DIR = Path.home() / ".heare" / "cache" / "discovery"
CACHE_TTL_SECONDS = 86400
REMOTE_TIMEOUT_SECONDS = 2.5

_HMAC_KEY_CACHE: bytes | None = None
_HMAC_KEY_RESOLVED = False


def _normalize_intent(intent: str) -> str:
    nfkc = unicodedata.normalize("NFKC", intent)
    return " ".join(nfkc.lower().split())


def _intent_hash(intent: str) -> str:
    return hashlib.sha256(_normalize_intent(intent).encode("utf-8")).hexdigest()[:32]


def _identity_path(settings) -> Path:
    path = getattr(settings, "identity_file", None) if settings is not None else None
    if path is None:
        return Path.home() / ".heare" / "identity.json"
    return Path(path)


def _hmac_key(settings=None) -> bytes | None:
    """Derive a stable HMAC key from ~/.heare/identity.json. None when missing."""
    global _HMAC_KEY_CACHE, _HMAC_KEY_RESOLVED
    if _HMAC_KEY_RESOLVED:
        return _HMAC_KEY_CACHE
    path = _identity_path(settings)
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        _HMAC_KEY_RESOLVED = True
        _HMAC_KEY_CACHE = None
        return None
    seed_parts = [str(data.get(k, "")) for k in ("name", "creature", "generated_at")]
    seed = "|".join(seed_parts)
    if not seed.strip("|"):
        _HMAC_KEY_RESOLVED = True
        _HMAC_KEY_CACHE = None
        return None
    _HMAC_KEY_CACHE = hashlib.sha256(seed.encode("utf-8")).digest()
    _HMAC_KEY_RESOLVED = True
    return _HMAC_KEY_CACHE


def _reset_hmac_key_cache() -> None:
    global _HMAC_KEY_CACHE, _HMAC_KEY_RESOLVED
    _HMAC_KEY_CACHE = None
    _HMAC_KEY_RESOLVED = False


def _cache_file(cache_dir: Path, intent_hash: str) -> Path:
    return cache_dir / f"{intent_hash}.json"


def _ensure_cache_dir(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_dir.chmod(0o700)


def _entry_to_dict(entry: IndexEntry) -> dict:
    return dataclasses.asdict(entry)


def _entry_from_dict(data: dict) -> IndexEntry:
    return IndexEntry(**data)


def _canonical_payload(ts: int, entries: list[dict]) -> bytes:
    return json.dumps({"ts": ts, "entries": entries}, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_cache(
    cache_dir: Path,
    intent_hash: str,
    *,
    settings=None,
    max_age_seconds: int = CACHE_TTL_SECONDS,
) -> list[IndexEntry] | None:
    path = _cache_file(cache_dir, intent_hash)
    if not path.exists():
        return None
    key = _hmac_key(settings)
    if key is None:
        return None
    try:
        raw = json.loads(path.read_text())
        ts = int(raw["ts"])
        entries_raw = raw["entries"]
        sig = str(raw["hmac"])
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("discovery cache unreadable %s: %s", path, exc)
        try:
            path.unlink()
        except OSError:
            pass
        return None

    expected = hmac.new(key, _canonical_payload(ts, entries_raw), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        logger.warning("discovery cache HMAC rejected for %s — deleting", path)
        try:
            path.unlink()
        except OSError:
            pass
        return None

    if time.time() - ts > max_age_seconds:
        return None

    try:
        return [_entry_from_dict(e) for e in entries_raw]
    except (TypeError, ValueError) as exc:
        logger.warning("discovery cache entries malformed %s: %s", path, exc)
        try:
            path.unlink()
        except OSError:
            pass
        return None


def _write_cache(
    cache_dir: Path,
    intent_hash: str,
    entries: list[IndexEntry],
    *,
    settings=None,
) -> None:
    key = _hmac_key(settings)
    if key is None:
        return
    _ensure_cache_dir(cache_dir)
    ts = int(time.time())
    entries_raw = [_entry_to_dict(e) for e in entries]
    sig = hmac.new(key, _canonical_payload(ts, entries_raw), hashlib.sha256).hexdigest()
    payload = {"hmac": sig, "ts": ts, "entries": entries_raw}
    path = _cache_file(cache_dir, intent_hash)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    os.replace(tmp, path)


async def _fetch_marketplace(intent: str, settings, timeout: float) -> list[IndexEntry]:
    from . import marketplace
    return await marketplace.fetch_skill_candidates(intent, settings=settings, timeout=timeout)


async def _fetch_mcp_registry(intent: str, settings, timeout: float) -> list[IndexEntry]:
    from . import marketplace
    return await marketplace.fetch_mcp_candidates(intent, settings=settings, timeout=timeout)


async def discover_capability_local(
    intent: str,
    capability_index: CapabilityIndex,
    top_k: int = 3,
) -> list[IndexEntry]:
    return capability_index.query(intent, top_k=top_k)


async def discover_capability_remote(
    intent: str,
    *,
    settings,
    cache_dir: Path | None = None,
    timeout: float = REMOTE_TIMEOUT_SECONDS,
) -> list[IndexEntry]:
    cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    _ensure_cache_dir(cache_dir)
    intent_hash = _intent_hash(intent)

    cached = _read_cache(cache_dir, intent_hash, settings=settings)
    if cached is not None:
        return cached

    try:
        async with asyncio.timeout(timeout):
            results = await asyncio.gather(
                _fetch_marketplace(intent, settings, timeout),
                _fetch_mcp_registry(intent, settings, timeout),
                return_exceptions=True,
            )
    except asyncio.TimeoutError:
        logger.warning("discovery remote fetch timed out after %.2fs (intent=%r)", timeout, intent)
        return []

    entries: list[IndexEntry] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("discovery remote fetch error: %s", r)
            continue
        entries.extend(r)

    try:
        _write_cache(cache_dir, intent_hash, entries, settings=settings)
    except OSError as exc:
        logger.warning("discovery cache write failed: %s", exc)

    return entries


__all__ = [
    "discover_capability_local",
    "discover_capability_remote",
    "DEFAULT_CACHE_DIR",
    "CACHE_TTL_SECONDS",
    "REMOTE_TIMEOUT_SECONDS",
]
