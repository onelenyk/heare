"""US-005 — mode drives turn timeout, indication sound, prompt addendum."""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.config import load_settings
from src.pipeline.language_state import LanguageState
from src.pipeline.session_state import SessionState
from src.store.context import ContextBuilder
from src.store.storage import TranscriptStore
from src.voice.indication.core import Indication


# --- turn_aggregator reads profile.turn_timeout ----------------------------






# --- indication sound policy via profile -----------------------------------


def test_indication_sound_policy_per_mode() -> None:
    from src.voice.indication.core import IndicationLevel
    from src.agent.modes import MODE_PROFILES

    allows = Indication._mode_allows_sound

    # SILENT: nothing.
    for lvl in IndicationLevel:
        assert allows(MODE_PROFILES["silent"], lvl) is False
    # AMBIENT: everything.
    for lvl in IndicationLevel:
        assert allows(MODE_PROFILES["ambient"], lvl) is True
    # FOCUS: only attention/error/input_waiting.
    assert allows(MODE_PROFILES["focus"], IndicationLevel.ATTENTION) is True
    assert allows(MODE_PROFILES["focus"], IndicationLevel.ERROR) is True
    assert allows(MODE_PROFILES["focus"], IndicationLevel.INPUT_WAITING) is True
    assert allows(MODE_PROFILES["focus"], IndicationLevel.INFO) is False


def test_indication_sound_policy_legacy_mode_enum_still_works() -> None:
    """Existing callers/tests pass a Mode enum — must keep prior behavior."""
    from src.config import Mode
    from src.voice.indication.core import IndicationLevel

    allows = Indication._mode_allows_sound
    assert allows(Mode.SILENT, IndicationLevel.ERROR) is False
    assert allows(Mode.FOCUS, IndicationLevel.INFO) is False
    assert allows(Mode.FOCUS, IndicationLevel.ERROR) is True
    assert allows(Mode.AMBIENT, IndicationLevel.INFO) is True


# --- prompt addendum + mode list -------------------------------------------


async def _ctx_with_mode(mode: str):
    with tempfile.TemporaryDirectory() as tmp:
        store = TranscriptStore(Path(tmp) / "h.db")
        await store.init()
        try:
            settings = load_settings()
            cb = ContextBuilder(store, settings)
            cb.set_session_state(SessionState(LanguageState(), initial_mode=mode))
            result = await cb.build_for_generator("hi", persona="p")
            return result.get("mode_block", "")
        finally:
            await store.close()


async def test_prompt_mode_block_present_and_lists_modes() -> None:
    block = await _ctx_with_mode("meeting")
    assert "note-taker" in block.lower()  # meeting addendum
    for m in ("ambient", "focus", "silent", "assistant", "meeting"):
        assert m in block
    assert "set_mode" in block


async def test_prompt_mode_block_renders_in_system_prompt() -> None:
    from src.agent.llm.context_injector import render_native_system_prompt

    out = render_native_system_prompt(
        persona="",
        context={"mode_block": "Mode: meeting. passive note-taker."},
        language="en",
    )
    assert "Mode: meeting. passive note-taker." in out
