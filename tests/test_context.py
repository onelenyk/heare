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
        "speaker_rule_block",
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
    # JSON-literal braces in the example schemas must survive rendering
    # without being treated as format placeholders.
    assert '{"t":"n"}' in rendered
    assert '"t":"s"' in rendered
    assert '"r":' in rendered
    assert "привіт" in rendered


def test_decider_prompt_has_length_constraints() -> None:
    """LAT-B2: prompt must enforce MAX 8 words reply and MAX 5 words intent."""
    template = Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    assert "MAX 8 words" in template, "reply length constraint missing"
    assert "MAX 5 words" in template, "intent length constraint missing"


def test_decider_prompt_mandates_strict_key_order() -> None:
    """LAT-B2: prompt must declare strict field ordering (t first, r second)."""
    template = Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    assert "FIELD ORDER IS STRICT" in template
    # Must mention that 't' is first and 'r' follows it
    assert '"t"' in template
    assert '"r"' in template
    # The ordering section must explicitly name both keys in order
    strict_section = template[template.index("FIELD ORDER IS STRICT"):]
    assert strict_section.index('"t"') < strict_section.index('"r"')


async def test_build_returns_speaker_rule_block_empty_when_flag_off(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    settings.speaker_id_enabled = False
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("x", heartbeat=False)
    assert result["speaker_rule_block"] == ""


async def test_build_returns_speaker_rule_block_when_flag_on(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    settings.speaker_id_enabled = True
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("x", heartbeat=False)
    assert "Speaker: owner" in result["speaker_rule_block"]


async def test_build_keeps_placeholder_literal_with_keep_placeholders(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build(
        "x", heartbeat=False, keep_placeholders=["speaker_rule_block"]
    )
    assert result["speaker_rule_block"] == "{speaker_rule_block}"


async def test_build_keeps_transcript_placeholder_literal(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build(
        "x", heartbeat=False, keep_placeholders=["transcript_or_heartbeat"]
    )
    assert result["transcript_or_heartbeat"] == "{transcript_or_heartbeat}"


async def test_golden_string_flag_off_render(store: TranscriptStore) -> None:
    """Flag-off rendering must be byte-stable across runs.

    First run captures the rendered output (with a fixed ctx) into
    tests/fixtures/decider_prompt_flag_off.golden.txt. Subsequent runs
    re-render and diff against the golden.
    """
    settings = load_settings()
    settings.speaker_id_enabled = False
    ctx = ContextBuilder(store, settings)
    # Fixed deterministic context — do NOT call ctx.build() because it
    # renders the current time, which breaks byte-stability.
    fixed_ctx = {
        "time": "2026-04-13 12:00:00",
        "timezone": "UTC",
        "mode": "ambient",
        "heartbeat_flag": "no",
        "recent_transcripts": "(none)",
        "transcript_or_heartbeat": "тест",
        "speaker_rule_block": ctx._render_rule_block(),
    }
    template = (
        Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    )
    rendered = ctx.render(template, fixed_ctx)

    golden_path = (
        Path(__file__).parent / "fixtures" / "decider_prompt_flag_off.golden.txt"
    )
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    if not golden_path.exists():
        golden_path.write_text(rendered)
    else:
        expected = golden_path.read_text()
        assert rendered == expected, (
            "flag-off rendered prompt drifted from golden. "
            f"Delete {golden_path} to regenerate if the drift is intentional."
        )

    # When flag is off, the rendered output must NOT contain the Speaker rule
    assert "Speaker: owner" not in rendered


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
