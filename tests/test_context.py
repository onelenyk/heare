"""Tests for src/context.py ContextBuilder."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.config import load_settings
from src.context import ContextBuilder
from src.storage import TranscriptStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        s = TranscriptStore(db)
        await s.init()
        try:
            yield s
        finally:
            await s.close()


async def test_build_shape_on_transcript(store: TranscriptStore) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("привіт, що робиш?", heartbeat=False)
    assert set(result.keys()) == {
        "time",
        "timezone",
        "mode",
        "heartbeat_flag",
        "recent_transcripts",
        "transcript_or_heartbeat",
    }
    assert result["heartbeat_flag"] == "no"
    assert "привіт" in result["transcript_or_heartbeat"]


async def test_build_shape_on_heartbeat(store: TranscriptStore) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build(None, heartbeat=True)
    assert result["heartbeat_flag"] == "yes"
    assert "heartbeat" in result["transcript_or_heartbeat"]


async def test_recent_transcripts_rendering(store: TranscriptStore) -> None:
    await store.log_transcript("один", "ambient")
    await store.log_transcript("два", "ambient")
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("три", heartbeat=False)
    assert "один" in result["recent_transcripts"]
    assert "два" in result["recent_transcripts"]


async def test_render_real_decider_template(store: TranscriptStore) -> None:
    """prompts/decider.txt contains literal { and } from the JSON example.
    render() must substitute named placeholders without choking on braces."""
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("привіт", heartbeat=False)
    template = Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    rendered = ctx.render(template, result)
    assert "{mode}" not in rendered
    assert "{transcript_or_heartbeat}" not in rendered
    assert '"type": "nothing" | "speak" | "act"' in rendered
    assert "привіт" in rendered


def test_render_with_template() -> None:
    import asyncio

    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "heare.db"
            s = TranscriptStore(db)
            await s.init()
            try:
                settings = load_settings()
                ctx = ContextBuilder(s, settings)
                result = await ctx.build("hi", heartbeat=False)
                template = "mode={mode} input={transcript_or_heartbeat}"
                rendered = ctx.render(template, result)
                assert "mode=" in rendered
                assert "hi" in rendered
            finally:
                await s.close()

    asyncio.run(_run())
