"""Tests for src/tts_cache.py — in-memory PCM cache for fixed phrases."""
from __future__ import annotations

import asyncio

from src.voice.tts.cache import TTSCache


async def test_tts_cache_get_miss() -> None:
    cache = TTSCache()
    assert cache.get("never seen") is None


async def test_tts_cache_get_hit() -> None:
    cache = TTSCache()
    pcm = b"\x00\x01" * 100
    cache.put("hello", pcm)
    assert cache.get("hello") == pcm


async def test_tts_cache_has() -> None:
    cache = TTSCache()
    assert cache.has("x") is False
    cache.put("x", b"abc")
    assert cache.has("x") is True


async def test_tts_cache_len() -> None:
    cache = TTSCache()
    assert len(cache) == 0
    cache.put("a", b"1")
    cache.put("b", b"2")
    assert len(cache) == 2


async def test_tts_cache_warmup_calls_synthesizer_once_per_missing() -> None:
    cache = TTSCache()
    calls: list[str] = []

    async def fake_synth(text: str) -> bytes:
        calls.append(text)
        return text.encode() * 10

    await cache.warmup(["hi", "bye", "go"], fake_synth)

    assert sorted(calls) == ["bye", "go", "hi"]
    assert cache.has("hi")
    assert cache.has("bye")
    assert cache.has("go")
    assert cache.get("hi") == b"hi" * 10


async def test_tts_cache_warmup_skips_existing_entries() -> None:
    cache = TTSCache()
    cache.put("hi", b"already-here")
    calls: list[str] = []

    async def fake_synth(text: str) -> bytes:
        calls.append(text)
        return b"new"

    await cache.warmup(["hi", "bye"], fake_synth)

    # "hi" was skipped (already cached), only "bye" called
    assert calls == ["bye"]
    assert cache.get("hi") == b"already-here"  # untouched
    assert cache.get("bye") == b"new"


async def test_tts_cache_warmup_skips_failed_synth() -> None:
    """If synthesizer raises for one phrase, others still get cached."""
    cache = TTSCache()

    async def flaky_synth(text: str) -> bytes:
        if text == "boom":
            raise RuntimeError("network down")
        return text.encode()

    await cache.warmup(["a", "boom", "b"], flaky_synth)

    assert cache.has("a")
    assert cache.has("b")
    assert not cache.has("boom")  # failed → not cached


async def test_tts_cache_warmup_skips_empty_pcm() -> None:
    cache = TTSCache()

    async def empty_synth(text: str) -> bytes:
        return b""

    await cache.warmup(["x"], empty_synth)
    assert not cache.has("x")  # empty PCM treated as miss


async def test_tts_cache_warmup_runs_concurrently() -> None:
    """All missing phrases must synth in parallel — a sequential implementation
    deadlocks here because the barrier requires N callers to converge."""
    cache = TTSCache()
    phrases = ["a", "b", "c", "d"]
    barrier = asyncio.Barrier(len(phrases))

    async def fake_synth(text: str) -> bytes:
        await barrier.wait()
        return text.encode()

    await asyncio.wait_for(cache.warmup(phrases, fake_synth), timeout=1.0)

    for p in phrases:
        assert cache.has(p)


def test_fixed_phrases_list_exposed() -> None:
    """FIXED_PHRASES was a module-level constant in src/voice/tts/phrases.
    That module was removed during refactoring — warmup phrases are now
    passed dynamically to TTSCache.warmup()."""
    from src.voice.tts.cache import TTSCache

    cache = TTSCache()
    assert len(cache) == 0
