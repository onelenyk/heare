"""Marketplace + MCP registry fetchers with hostname/homoglyph/checksum primitives.

Used by ``src.discovery`` to source remote skill / MCP candidates. All errors
are swallowed to ``[]`` — discovery is best-effort and must never raise into
the agent loop.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.parse

import httpx

from .capability_index import IndexEntry

logger = logging.getLogger("heare.marketplace")

DEFAULT_HOSTNAME_ALLOWLIST: tuple[str, ...] = ("skillsmp.com", "github.com", "githubusercontent.com")

_BRANDS: tuple[str, ...] = ("skillsmp", "github", "githubusercontent")
_SUBSTITUTIONS: dict[str, str] = {"1": "i", "0": "o", "5": "s", "3": "e"}


def _is_allowed_hostname(url: str, allowlist: tuple[str, ...]) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.rstrip(".").lower()
    for allowed in allowlist:
        a = allowed.lower()
        if host == a or host.endswith("." + a):
            return True
    return False


def _is_homoglyph_or_lookalike(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname
    except ValueError:
        return True
    if not host:
        return True
    host = host.rstrip(".").lower()
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        return True

    labels = host.split(".")
    if len(labels) < 2:
        return False
    registrable = ".".join(labels[-2:])

    for brand in _BRANDS:
        if brand in labels[:-2] and brand not in registrable:
            return True

    for label in labels:
        substituted = "".join(_SUBSTITUTIONS.get(c, c) for c in label)
        if substituted == label:
            continue
        for brand in _BRANDS:
            if substituted == brand and label != brand:
                return True
    return False


def _verify_checksum(content: bytes, expected_sha256_hex: str) -> bool:
    actual = hashlib.sha256(content).hexdigest()
    return hmac.compare_digest(actual.lower(), expected_sha256_hex.lower())


def _validate_url(url: str, allowlist: tuple[str, ...]) -> bool:
    return _is_allowed_hostname(url, allowlist) and not _is_homoglyph_or_lookalike(url)


def _coerce_entry(raw: dict, source: str, allowlist: tuple[str, ...]) -> IndexEntry | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    description = raw.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    install_url = raw.get("install_url")
    if install_url is not None:
        if not isinstance(install_url, str) or not _validate_url(install_url, allowlist):
            logger.warning("rejecting %s entry %r: bad install_url %r", source, name, install_url)
            return None
    args_schema = raw.get("args_schema") if isinstance(raw.get("args_schema"), dict) else None
    network_required = bool(raw.get("network_required", False))
    pop = raw.get("popularity_score")
    popularity_score = float(pop) if isinstance(pop, (int, float)) else None
    checksum = raw.get("checksum") if isinstance(raw.get("checksum"), str) else None
    return IndexEntry(
        source=source,  # type: ignore[arg-type]
        name=name,
        description=description,
        args_schema=args_schema,
        network_required=network_required,
        popularity_score=popularity_score,
        install_url=install_url,
        checksum=checksum,
    )


def _parse_results(payload: object, source: str, allowlist: tuple[str, ...]) -> list[IndexEntry]:
    if not isinstance(payload, dict):
        logger.warning("%s payload not a dict: %r", source, type(payload).__name__)
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        logger.warning("%s payload missing 'results' list", source)
        return []
    out: list[IndexEntry] = []
    for raw in results:
        entry = _coerce_entry(raw, source, allowlist)
        if entry is not None:
            out.append(entry)
    return out


async def _fetch_json(url: str, timeout: float) -> object | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("fetch %s failed: %s", url, exc)
        return None


async def fetch_skill_candidates(
    query: str, *, settings, timeout: float = 2.5
) -> list[IndexEntry]:
    base = getattr(settings, "marketplace_url", "") or ""
    if not base:
        return []
    url = f"{base.rstrip('/')}/api/search?q={urllib.parse.quote(query)}"
    if not _validate_url(url, DEFAULT_HOSTNAME_ALLOWLIST):
        logger.warning("marketplace_url rejected: %r", base)
        return []
    payload = await _fetch_json(url, timeout)
    if payload is None:
        return []
    return _parse_results(payload, "skill", DEFAULT_HOSTNAME_ALLOWLIST)


async def fetch_mcp_candidates(
    query: str, *, settings, timeout: float = 2.5
) -> list[IndexEntry]:
    base = getattr(settings, "mcp_registry_url", "") or ""
    if not base:
        return []
    url = f"{base.rstrip('/')}/api/search?q={urllib.parse.quote(query)}"
    if not _validate_url(url, DEFAULT_HOSTNAME_ALLOWLIST):
        logger.warning("mcp_registry_url rejected: %r", base)
        return []
    payload = await _fetch_json(url, timeout)
    if payload is None:
        return []
    return _parse_results(payload, "mcp", DEFAULT_HOSTNAME_ALLOWLIST)


__all__ = [
    "DEFAULT_HOSTNAME_ALLOWLIST",
    "fetch_skill_candidates",
    "fetch_mcp_candidates",
]
