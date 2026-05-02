"""Tests for capability discovery (US-004)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src import discovery
from src.capability_index import IndexEntry


@dataclass
class _FakeSettings:
    identity_file: Path = field(default_factory=lambda: Path("/nonexistent"))
    marketplace_url: str = "https://skillsmp.com"
    mcp_registry_url: str = ""


def _write_identity(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "name": "Heare",
        "creature": "owl",
        "vibe": "calm",
        "emoji": "🦉",
        "tagline": "stays sharp",
        "generated_at": "2026-05-01T00:00:00Z",
    }))


@pytest.fixture(autouse=True)
def _reset_hmac_cache():
    discovery._reset_hmac_key_cache()
    yield
    discovery._reset_hmac_key_cache()


@pytest.fixture
def settings_with_identity(tmp_path: Path) -> _FakeSettings:
    identity_file = tmp_path / "identity.json"
    _write_identity(identity_file)
    return _FakeSettings(identity_file=identity_file)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "discovery"


@pytest.mark.asyncio
async def test_local_discovery_returns_index_results():
    fake_entries = [
        IndexEntry(source="builtin", name="weather", description="weather lookup"),
    ]

    class FakeIndex:
        def query(self, intent: str, top_k: int = 3):
            assert intent == "weather kyiv"
            return fake_entries

    out = await discovery.discover_capability_local("weather kyiv", FakeIndex(), top_k=3)
    assert out == fake_entries


@pytest.mark.asyncio
async def test_remote_discovery_returns_empty_when_marketplace_stubbed(
    settings_with_identity: _FakeSettings, cache_dir: Path
):
    out = await discovery.discover_capability_remote(
        "weather kyiv",
        settings=settings_with_identity,
        cache_dir=cache_dir,
    )
    assert out == []


@pytest.mark.asyncio
async def test_remote_discovery_timeout_returns_empty_logs_warning(
    monkeypatch, caplog, settings_with_identity: _FakeSettings, cache_dir: Path
):
    async def slow(*args, **kwargs):
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(discovery, "_fetch_marketplace", slow)
    monkeypatch.setattr(discovery, "_fetch_mcp_registry", slow)

    caplog.set_level(logging.WARNING, logger="heare.discovery")
    start = time.monotonic()
    out = await discovery.discover_capability_remote(
        "long timeout intent",
        settings=settings_with_identity,
        cache_dir=cache_dir,
        timeout=0.2,
    )
    elapsed = time.monotonic() - start
    assert out == []
    assert elapsed < 1.0
    assert any("timed out" in r.message for r in caplog.records)


def test_cache_dir_is_0700(cache_dir: Path):
    discovery._ensure_cache_dir(cache_dir)
    mode = cache_dir.stat().st_mode & 0o777
    assert mode == 0o700


def test_intent_hash_stable_across_normalization():
    a = discovery._intent_hash("weather kyiv")
    b = discovery._intent_hash("weather  KYIV")
    c = discovery._intent_hash("WEATHER\tkyiv")
    assert a == b == c


@pytest.mark.asyncio
async def test_cache_hit_returns_cached_entries(
    monkeypatch, settings_with_identity: _FakeSettings, cache_dir: Path
):
    intent = "weather kyiv"
    entries = [IndexEntry(source="skill", name="weather-skill", description="cached")]
    discovery._write_cache(
        cache_dir, discovery._intent_hash(intent), entries, settings=settings_with_identity
    )

    mp = AsyncMock(return_value=[])
    mcp = AsyncMock(return_value=[])
    monkeypatch.setattr(discovery, "_fetch_marketplace", mp)
    monkeypatch.setattr(discovery, "_fetch_mcp_registry", mcp)

    out = await discovery.discover_capability_remote(
        intent, settings=settings_with_identity, cache_dir=cache_dir
    )
    assert out == entries
    assert mp.call_count == 0
    assert mcp.call_count == 0


@pytest.mark.asyncio
async def test_cache_tamper_rejected_and_deleted(
    monkeypatch, caplog, settings_with_identity: _FakeSettings, cache_dir: Path
):
    intent = "weather kyiv"
    entries = [IndexEntry(source="skill", name="weather-skill", description="cached")]
    discovery._write_cache(
        cache_dir, discovery._intent_hash(intent), entries, settings=settings_with_identity
    )
    cache_path = discovery._cache_file(cache_dir, discovery._intent_hash(intent))
    raw = json.loads(cache_path.read_text())
    raw["entries"] = [
        {
            "source": "skill",
            "name": "evil",
            "description": "tampered",
            "args_schema": None,
            "network_required": False,
            "popularity_score": None,
            "install_url": None,
            "schema_version": 1,
            "checksum": None,
        }
    ]
    cache_path.write_text(json.dumps(raw))

    mp = AsyncMock(return_value=[])
    mcp = AsyncMock(return_value=[])
    monkeypatch.setattr(discovery, "_fetch_marketplace", mp)
    monkeypatch.setattr(discovery, "_fetch_mcp_registry", mcp)

    caplog.set_level(logging.WARNING, logger="heare.discovery")
    out = await discovery.discover_capability_remote(
        intent, settings=settings_with_identity, cache_dir=cache_dir
    )
    assert out == []
    assert mp.call_count == 1
    assert mcp.call_count == 1
    # The cache file should have been deleted (tampered) — but a fresh write
    # may have re-created it with []. So check that it was at least rejected
    # by inspecting the log.
    assert any("HMAC rejected" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_cache_expired_falls_through_to_fetch(
    monkeypatch, settings_with_identity: _FakeSettings, cache_dir: Path
):
    intent = "weather kyiv"
    entries = [IndexEntry(source="skill", name="weather-skill", description="cached")]
    discovery._ensure_cache_dir(cache_dir)
    intent_hash = discovery._intent_hash(intent)
    key = discovery._hmac_key(settings_with_identity)
    assert key is not None
    ts = int(time.time()) - 90000
    entries_raw = [
        {
            "source": e.source,
            "name": e.name,
            "description": e.description,
            "args_schema": e.args_schema,
            "network_required": e.network_required,
            "popularity_score": e.popularity_score,
            "install_url": e.install_url,
            "schema_version": e.schema_version,
            "checksum": e.checksum,
        }
        for e in entries
    ]
    sig = hmac.new(key, discovery._canonical_payload(ts, entries_raw), hashlib.sha256).hexdigest()
    cache_path = discovery._cache_file(cache_dir, intent_hash)
    cache_path.write_text(json.dumps({"hmac": sig, "ts": ts, "entries": entries_raw}))

    mp = AsyncMock(return_value=[])
    mcp = AsyncMock(return_value=[])
    monkeypatch.setattr(discovery, "_fetch_marketplace", mp)
    monkeypatch.setattr(discovery, "_fetch_mcp_registry", mcp)

    out = await discovery.discover_capability_remote(
        intent, settings=settings_with_identity, cache_dir=cache_dir
    )
    assert out == []
    assert mp.call_count == 1
    assert mcp.call_count == 1


@pytest.mark.asyncio
async def test_no_identity_skips_cache_gracefully(
    monkeypatch, tmp_path: Path, cache_dir: Path
):
    settings = _FakeSettings(identity_file=tmp_path / "missing.json")

    fetched = [IndexEntry(source="skill", name="x", description="y")]

    mp = AsyncMock(return_value=fetched)
    mcp = AsyncMock(return_value=[])
    monkeypatch.setattr(discovery, "_fetch_marketplace", mp)
    monkeypatch.setattr(discovery, "_fetch_mcp_registry", mcp)

    out = await discovery.discover_capability_remote(
        "no identity intent", settings=settings, cache_dir=cache_dir
    )
    assert out == fetched
    cache_path = discovery._cache_file(cache_dir, discovery._intent_hash("no identity intent"))
    assert not cache_path.exists()
