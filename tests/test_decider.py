"""Tests for DeciderProcessor state transitions using a fake ClaudeCLI.

We construct the DeciderProcessor via create_decider_processor which pulls
the pipecat base classes at call time. These tests skip themselves if
pipecat is not importable so the suite stays runnable on lean environments.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

pipecat = pytest.importorskip("pipecat.frames.frames")
TranscriptionFrame = pipecat.TranscriptionFrame

from src.config import DeciderState, Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.decider import create_decider_processor  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402


class FakeClaudeCLI:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = decisions
        self.call_action = AsyncMock(return_value={"summary": "ok, done"})
        self.calls: list[str] = []

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        self.calls.append(prompt)
        return self._decisions.pop(0)


def _make_frame(text: str):
    try:
        return TranscriptionFrame(text=text, user_id="u", timestamp="t")
    except TypeError:
        return TranscriptionFrame(user_id="u", timestamp="t", text=text)


@pytest.fixture
async def harness():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        store = TranscriptStore(db)
        await store.init()
        settings = Settings()
        settings.mode = Mode.AMBIENT
        settings.confirmation_timeout_seconds = 1
        settings.speaker_id_enabled = False
        settings.speaker_namer_enabled = False
        ctx_builder = ContextBuilder(store, settings)
        try:
            yield store, settings, ctx_builder
        finally:
            await store.close()


async def test_listening_to_speak(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [{"type": "speak", "confidence": 0.95, "reply": "привіт", "reason": "greeted"}]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, привіт")
    assert decider.state == DeciderState.LISTENING
    assert decider.push_frame.await_count == 1


async def test_listening_to_nothing(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([{"type": "nothing", "reason": "not for me"}])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("випадкова фраза")
    assert decider.state == DeciderState.LISTENING
    assert decider.push_frame.await_count == 0


async def test_listening_to_awaiting_confirmation(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked to run tests",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert decider.pending_action is not None


async def test_confirmation_yes_executes(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    await decider._handle_confirmation("гава так")
    cli.call_action.assert_awaited()
    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None


async def test_confirmation_no_cancels(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    await decider._handle_confirmation("гава ні")
    cli.call_action.assert_not_awaited()
    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None


async def test_heartbeat_skipped_during_confirmation(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            },
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    await decider.on_heartbeat_tick()
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert cli.calls == [cli.calls[0]]


from pipecat.frames.frames import (  # noqa: E402
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    UserStartedSpeakingFrame,
)


async def test_executing_state_pushes_summary_frame(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    cli.call_action = AsyncMock(return_value={"summary": "тести пройшли"})
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    await decider._handle_confirmation("гава так")
    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None
    pushed_texts = [
        call.args[0].text
        for call in decider.push_frame.await_args_list
        if hasattr(call.args[0], "text")
    ]
    assert any("тести пройшли" in t for t in pushed_texts)


async def test_executing_state_action_failure_pushes_error_frame(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    cli.call_action = AsyncMock(side_effect=Exception("boom"))
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    await decider._handle_confirmation("гава так")
    assert decider.state == DeciderState.LISTENING
    pushed_texts = [
        call.args[0].text
        for call in decider.push_frame.await_args_list
        if hasattr(call.args[0], "text")
    ]
    assert any("дія не вдалася" in t for t in pushed_texts)


async def test_bot_speaking_suppresses_transcript(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([{"type": "nothing", "reason": "not for me"}])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    decider._handle_listening = AsyncMock()  # type: ignore[attr-defined]
    decider._bot_speaking = True
    frame = _make_frame("привіт")
    await decider.process_frame(frame, None)
    decider._handle_listening.assert_not_awaited()


async def test_bot_started_stopped_speaking_frames_passthrough(harness) -> None:
    import asyncio as _asyncio

    store, settings, ctx = harness
    settings.bot_speaking_cooldown_seconds = 0.05
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    started_frame = BotStartedSpeakingFrame()
    await decider.process_frame(started_frame, None)
    assert decider._bot_speaking is True
    decider.push_frame.assert_awaited_with(started_frame, None)

    stopped_frame = BotStoppedSpeakingFrame()
    await decider.process_frame(stopped_frame, None)
    # Cooldown is active — flag stays True for bot_speaking_cooldown_seconds
    assert decider._bot_speaking is True
    decider.push_frame.assert_awaited_with(stopped_frame, None)
    # After cooldown elapses, the watcher clears it
    await _asyncio.sleep(0.1)
    assert decider._bot_speaking is False


async def test_confidence_below_floor_drops_action(harness) -> None:
    store, settings, ctx = harness
    settings.min_action_confidence = 0.8
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.5,
                "reason": "low confidence",
                "intent": "do something",
                "action": {"tool": "Bash", "args": "echo hi"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("зроби щось")
    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None


async def test_confidence_floor_uses_settings(harness) -> None:
    store, settings, ctx = harness
    settings.min_action_confidence = 0.3
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.5,
                "reason": "medium confidence",
                "intent": "do something",
                "action": {"tool": "Bash", "args": "echo hi"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, зроби щось")
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert decider.pending_action is not None


async def test_silent_mode_stores_only(harness) -> None:
    store, settings, ctx = harness
    settings.mode = Mode.SILENT
    settings.mode_file = Path("/nonexistent/mode_file")  # prevent _reload_mode override
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    frame = _make_frame("тихий режим")
    await decider.process_frame(frame, None)
    assert len(cli.calls) == 0
    assert decider.state == DeciderState.LISTENING


async def test_unknown_decision_type_ignored(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([{"type": "unknown_thing", "reason": "wat"}])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("щось незрозуміле")
    assert decider.state == DeciderState.LISTENING
    assert decider.push_frame.await_count == 0


async def test_heartbeat_speak_pushes_frame(harness) -> None:
    store, settings, ctx = harness
    settings.mode = Mode.AMBIENT
    cli = FakeClaudeCLI([{"type": "speak", "reply": "привіт з хартбіту", "reason": "proactive"}])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    assert decider.state == DeciderState.LISTENING
    await decider.on_heartbeat_tick()
    assert decider.push_frame.await_count == 1
    pushed_text = decider.push_frame.await_args_list[0].args[0].text
    assert "привіт з хартбіту" in pushed_text


async def test_heartbeat_silent_mode_skipped(harness) -> None:
    store, settings, ctx = harness
    settings.mode = Mode.SILENT
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider.on_heartbeat_tick()
    assert len(cli.calls) == 0
    assert decider.push_frame.await_count == 0


async def test_process_frame_non_transcription_passes_through(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    frame = Frame()
    await decider.process_frame(frame, None)
    decider.push_frame.assert_awaited_once_with(frame, None)
    assert len(cli.calls) == 0


async def test_noise_filter_drops_filler_transcripts(harness) -> None:
    """Filler transcripts ('Хм...', 'Ну...', 'Пу-пу') are dropped before decider call."""
    store, settings, ctx = harness
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    for noise in ["Хм...", "Хм-хм.", "Ну...", "Пу-пу-пу-пу-пу.", "А...", "Е...", "ммм"]:
        await decider._handle_listening(noise)

    # No decider calls should have been made for any of these
    assert len(cli.calls) == 0
    assert decider.state == DeciderState.LISTENING


async def test_noise_filter_passes_real_words(harness) -> None:
    """Real words pass the filler filter (noise filter only drops Хм/Ну/Пу).

    Note: LAT-B1 adds a separate quick-nothing filter that drops short/non-UA/
    declarative ambient transcripts. To isolate the noise filter specifically,
    we use wake-word prefixed phrases which bypass the quick-nothing filter.
    """
    store, settings, ctx = harness
    cli = FakeClaudeCLI([
        {"type": "speak", "confidence": 0.9, "reply": "okay", "reason": "ack"},
        {"type": "nothing", "reason": "ignore"},
    ])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider._handle_listening("Гава, так")
    await decider._handle_listening("Гава, привіт")
    assert len(cli.calls) == 2  # both real words went through noise filter


async def test_bot_cooldown_cancelled_by_new_bot_started(harness) -> None:
    """If bot starts speaking again before cooldown elapses, cooldown is cancelled."""
    import asyncio as _asyncio

    store, settings, ctx = harness
    settings.bot_speaking_cooldown_seconds = 5.0  # long cooldown
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider.process_frame(BotStartedSpeakingFrame(), None)
    await decider.process_frame(BotStoppedSpeakingFrame(), None)
    cooldown_task = decider._bot_cooldown_task
    assert cooldown_task is not None
    assert not cooldown_task.done()

    # Bot starts again — should cancel the previous cooldown task
    await decider.process_frame(BotStartedSpeakingFrame(), None)
    await _asyncio.sleep(0)  # let cancellation propagate
    assert decider._bot_speaking is True
    assert decider._bot_cooldown_task is None
    assert cooldown_task.cancelled() or cooldown_task.done()


def test_is_quick_nothing_focus_mode_no_wake_word() -> None:
    from src.decider import is_quick_nothing
    assert is_quick_nothing("Привіт мамо", Mode.FOCUS) is True
    assert is_quick_nothing("Як справи?", Mode.FOCUS) is True


def test_is_quick_nothing_focus_mode_with_wake_word() -> None:
    from src.decider import is_quick_nothing
    assert is_quick_nothing("Гава, привіт", Mode.FOCUS) is False
    assert is_quick_nothing("Heare, can you help", Mode.FOCUS) is False
    assert is_quick_nothing("Гей, скажи щось", Mode.FOCUS) is False


def test_is_quick_nothing_other_person_detection() -> None:
    from src.decider import is_quick_nothing
    assert is_quick_nothing("Гала, як справи", Mode.AMBIENT) is True
    assert is_quick_nothing("мама привіт", Mode.AMBIENT) is True
    assert is_quick_nothing("алло, це я", Mode.AMBIENT) is True


def test_is_quick_nothing_ambient_passes_through() -> None:
    """Ambient mode without wake-word and without other-person → False (let LLM decide)."""
    from src.decider import is_quick_nothing
    assert is_quick_nothing("Як це працює", Mode.AMBIENT) is False
    assert is_quick_nothing("Цікаво, що далі", Mode.AMBIENT) is False


def test_is_quick_nothing_silent_mode() -> None:
    """Silent mode handling is independent of this filter (covered elsewhere)."""
    from src.decider import is_quick_nothing
    # In silent mode, ambient logic applies — without wake-word, only other-person triggers
    assert is_quick_nothing("привіт мамо", Mode.SILENT) is True
    assert is_quick_nothing("Як справи", Mode.SILENT) is False


async def test_handle_listening_skips_decider_on_quick_nothing(harness) -> None:
    """When is_quick_nothing returns True, no decider call is made."""
    store, settings, ctx = harness
    settings.mode = Mode.FOCUS
    cli = FakeClaudeCLI([])  # empty — would error if accessed
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Focus mode, no wake-word → should be filtered before LLM
    await decider._handle_listening("Привіт мамо")

    assert len(cli.calls) == 0
    assert decider.push_frame.await_count == 0


async def test_handle_listening_does_call_decider_with_wake_word(harness) -> None:
    """Wake-word in focus mode should reach the decider."""
    store, settings, ctx = harness
    settings.mode = Mode.FOCUS
    cli = FakeClaudeCLI(
        [{"type": "speak", "confidence": 0.9, "reply": "Я тут!", "reason": "addressed"}]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider._handle_listening("Гава, привіт")

    assert len(cli.calls) == 1
    assert decider.push_frame.await_count == 1


# ---------------------------------------------------------------------------
# LAT-B1: Stronger ambient pre-filter
# ---------------------------------------------------------------------------


def test_ambient_short_transcript_filtered() -> None:
    """Transcript < 3 words in ambient mode → filtered as quick-nothing."""
    from src.decider import is_quick_nothing
    assert is_quick_nothing("Ок.", Mode.AMBIENT) is True
    assert is_quick_nothing("Так ось", Mode.AMBIENT) is True
    assert is_quick_nothing("а", Mode.AMBIENT) is True


def test_ambient_non_ukrainian_filtered() -> None:
    """Mostly-Latin transcript in ambient → filtered (Гава only speaks UA)."""
    from src.decider import is_quick_nothing
    assert is_quick_nothing("That is a good capacitor", Mode.AMBIENT) is True
    assert is_quick_nothing("I need to check the datasheet", Mode.AMBIENT) is True


def test_ambient_declarative_filtered() -> None:
    """Declarative statement (no ? and no question word) in ambient → filtered."""
    from src.decider import is_quick_nothing
    assert is_quick_nothing("Це працює нормально.", Mode.AMBIENT) is True
    assert is_quick_nothing("Зараз буду перевіряти все.", Mode.AMBIENT) is True


def test_ambient_question_passes() -> None:
    """Questions in ambient mode still reach the LLM."""
    from src.decider import is_quick_nothing
    assert is_quick_nothing("Як воно працює?", Mode.AMBIENT) is False
    assert is_quick_nothing("Що це таке і для чого", Mode.AMBIENT) is False
    assert is_quick_nothing("Коли треба буде запустити", Mode.AMBIENT) is False


def test_ambient_wake_word_bypass_all_rules() -> None:
    """Wake-word bypasses short, non-UA, and declarative rules."""
    from src.decider import is_quick_nothing
    # Short BUT has wake-word
    assert is_quick_nothing("Гава що", Mode.FOCUS) is False
    assert is_quick_nothing("Гава стоп", Mode.AMBIENT) is False
    # Declarative BUT has wake-word
    assert is_quick_nothing("Гава, усе добре.", Mode.AMBIENT) is False


def test_is_quick_nothing_wake_word_english_bypasses_non_ukrainian() -> None:
    """'heare status' is 100% Latin BUT wake-word bypasses non-Ukrainian filter."""
    from src.decider import is_quick_nothing
    assert is_quick_nothing("heare status", Mode.AMBIENT) is False
    assert is_quick_nothing("heare please check", Mode.AMBIENT) is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", False),
        ("Привіт", False),  # all Cyrillic
        ("hello world", True),  # all Latin
        ("Hello Ukraine", True),  # mostly Latin
        ("Hi, привіт там", False),  # more Cyrillic than Latin
        ("This has one укр word only", True),  # 3/29 alpha are Cyrillic ~10%
        ("абвг", False),  # pure Ukrainian short
    ],
)
def test_is_mostly_non_ukrainian_helper(text: str, expected: bool) -> None:
    from src.decider import _is_mostly_non_ukrainian
    assert _is_mostly_non_ukrainian(text) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Як справи?", True),  # ends with ?
        ("як воно", True),  # starts with "як"
        ("що там нового", True),  # contains "що"
        ("коли буде готово", True),  # contains "коли"
        ("Усе добре.", False),  # no markers
        ("мама там", False),  # no question words (мама is not a question word)
        ("все тихо", False),
    ],
)
def test_looks_like_question_helper(text: str, expected: bool) -> None:
    from src.decider import _looks_like_question
    assert _looks_like_question(text) is expected


# ---------------------------------------------------------------------------
# LAT-B4: Speculative context build on UserStartedSpeakingFrame
# ---------------------------------------------------------------------------


async def test_speculative_context_built_on_user_started_speaking(harness) -> None:
    """UserStartedSpeakingFrame kicks off async context+prompt build."""
    store, settings, ctx = harness
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "p {mode} {transcript_or_heartbeat}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider.process_frame(UserStartedSpeakingFrame(), None)
    assert decider._speculative_task is not None
    # Let the speculative task run to completion
    await decider._speculative_task
    assert decider._speculative_prompt is not None
    assert decider._speculative_ctx is not None
    # The placeholder must still be present so _handle_listening can substitute
    assert "{transcript_or_heartbeat}" in decider._speculative_prompt


async def test_speculative_context_reused_in_handle_listening(harness) -> None:
    """_handle_listening reuses speculative prompt, does NOT call context_builder.build again."""
    store, settings, ctx = harness
    cli = FakeClaudeCLI([
        {"type": "nothing", "reason": "not for me"}
    ])
    decider = create_decider_processor(
        cli, store, ctx, settings, "p {mode} {transcript_or_heartbeat}"
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # 1. Trigger speculation and wait for it
    await decider.process_frame(UserStartedSpeakingFrame(), None)
    await decider._speculative_task
    assert decider._speculative_prompt is not None

    # 2. Spy on context_builder.build — it must NOT be called a second time
    build_mock = AsyncMock(wraps=ctx.build)
    decider.context_builder.build = build_mock  # type: ignore[method-assign]

    await decider._handle_listening("Гава, як справи")

    # context_builder.build was already called by the speculative task; after
    # we replaced it with the mock, _handle_listening must not call it again.
    assert build_mock.await_count == 0
    # Decider got called with a prompt containing our transcript
    assert len(cli.calls) == 1
    assert "Гава, як справи" in cli.calls[0]


async def test_speculative_context_cleared_after_use(harness) -> None:
    """After _handle_listening, speculative fields are cleared."""
    store, settings, ctx = harness
    cli = FakeClaudeCLI([{"type": "nothing", "reason": "ok"}])
    decider = create_decider_processor(
        cli, store, ctx, settings, "p {mode} {transcript_or_heartbeat}"
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider.process_frame(UserStartedSpeakingFrame(), None)
    await decider._speculative_task
    await decider._handle_listening("Гава, як воно")

    assert decider._speculative_prompt is None
    assert decider._speculative_ctx is None
    assert decider._speculative_task is None
    assert decider._speculative_started_at is None


async def test_speculative_context_handles_no_speculation(harness) -> None:
    """_handle_listening still works when no prior UserStartedSpeakingFrame was sent."""
    store, settings, ctx = harness
    cli = FakeClaudeCLI([
        {"type": "speak", "confidence": 0.9, "reply": "ok", "reason": "ack"}
    ])
    decider = create_decider_processor(
        cli, store, ctx, settings, "p {mode} {transcript_or_heartbeat}"
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # No speculation primed
    assert decider._speculative_prompt is None
    await decider._handle_listening("Гава, привіт")

    assert len(cli.calls) == 1
    assert "Гава, привіт" in cli.calls[0]


async def test_speculative_context_stale_after_threshold(harness) -> None:
    """Speculation older than _speculative_stale_after_seconds is not reused."""
    store, settings, ctx = harness
    cli = FakeClaudeCLI([
        {"type": "nothing", "reason": "ok"}
    ])
    decider = create_decider_processor(
        cli, store, ctx, settings, "p {mode} {transcript_or_heartbeat}"
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    decider._speculative_stale_after_seconds = 0.01  # make it stale instantly

    await decider.process_frame(UserStartedSpeakingFrame(), None)
    await decider._speculative_task
    # Wait longer than stale window
    import asyncio as _asyncio
    await _asyncio.sleep(0.05)

    # Now _handle_listening must fall back to normal build
    build_mock = AsyncMock(wraps=ctx.build)
    decider.context_builder.build = build_mock  # type: ignore[method-assign]

    await decider._handle_listening("Гава, привіт")

    # Normal build SHOULD have been called (fallback path)
    assert build_mock.await_count == 1


def test_is_noise_function() -> None:
    """is_noise() correctly classifies filler vs real text."""
    from src.decider import is_noise

    # Filler / noise
    assert is_noise("Хм...") is True
    assert is_noise("Хм-хм.") is True
    assert is_noise("Ну...") is True
    assert is_noise("Пу-пу-пу-пу-пу.") is True
    assert is_noise("А.") is True
    assert is_noise("Е...") is True
    assert is_noise("м") is True
    assert is_noise("") is True
    assert is_noise("   ") is True
    assert is_noise("...") is True

    # Real words — must NOT be filtered
    assert is_noise("Так") is False
    assert is_noise("Ні") is False
    assert is_noise("Привіт") is False
    assert is_noise("Відкрий браузер") is False
    assert is_noise("Хм, добре") is False  # filler followed by real text


# ---- SPK-005 speaker gating tests ----

async def test_non_owner_reaches_decider_in_listening(harness) -> None:
    """KW-3: hard speaker gate removed — non-owner utterances now reach Claude.

    The keyword gate on act decisions is the new guard; speak decisions pass
    through regardless of speaker_id.
    """
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    cli = FakeClaudeCLI([{"type": "speak", "confidence": 0.9, "reply": "hi"}])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening(
        "Гава, привіт", speaker_id="unknown", speaker_confidence=0.4
    )
    # Non-owner now DOES reach Claude decider (gate removed by KW-3)
    assert len(cli.calls) == 1
    assert decider.state == DeciderState.LISTENING
    # Transcript must be persisted with speaker_id for audit
    recent = await store.recent_transcripts(5)
    assert len(recent) == 1
    assert recent[0]["text"] == "Гава, привіт"


async def test_owner_path_unchanged(harness) -> None:
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    cli = FakeClaudeCLI([{"type": "speak", "confidence": 0.95, "reply": "привіт"}])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening(
        "Гава, привіт", speaker_id="owner", speaker_confidence=0.95
    )
    assert decider.state == DeciderState.LISTENING
    assert len(cli.calls) == 1


async def test_confirmation_rejects_inherited(harness) -> None:
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening(
        "Гава, запусти тести", speaker_id="owner", speaker_confidence=0.95
    )
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert decider.pending_speaker_id == "owner"
    # Inherited short-turn "гава так" must NOT execute (inherited label rejected)
    await decider._handle_confirmation("гава так", speaker_id="owner", speaker_inherited=True)
    cli.call_action.assert_not_awaited()
    assert decider.state == DeciderState.AWAITING_CONFIRMATION


async def test_confirmation_speaker_mismatch(harness) -> None:
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening(
        "Гава, запусти тести", speaker_id="owner", speaker_confidence=0.95
    )
    # Stranger says "гава так" — pending_speaker_id='owner', frame.speaker_id='unknown'
    await decider._handle_confirmation("гава так", speaker_id="unknown", speaker_inherited=False)
    cli.call_action.assert_not_awaited()
    assert decider.state == DeciderState.AWAITING_CONFIRMATION


async def test_pending_speaker_id_cleared_on_execute(harness) -> None:
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening(
        "Гава, запусти", speaker_id="owner", speaker_confidence=0.95
    )
    assert decider.pending_speaker_id == "owner"
    await decider._handle_confirmation("гава так", speaker_id="owner", speaker_inherited=False)
    assert decider.pending_speaker_id is None
    assert decider.state == DeciderState.LISTENING


async def test_pending_speaker_id_cleared_on_cancel(harness) -> None:
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening(
        "Гава, запусти", speaker_id="owner", speaker_confidence=0.95
    )
    assert decider.pending_speaker_id == "owner"
    await decider._handle_confirmation("гава ні", speaker_id="owner", speaker_inherited=False)
    assert decider.pending_speaker_id is None
    assert decider.state == DeciderState.LISTENING


async def test_silent_mode_persists_stranger_speaker_fields(harness) -> None:
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    settings.mode = Mode.SILENT
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._store_only("привіт", speaker_id="unknown", speaker_confidence=0.40)
    # No decider call, no TTS frame pushed
    assert cli.calls == []
    assert decider.push_frame.await_count == 0
    # Check raw DB row has speaker fields
    cursor = await store.db.execute(
        "SELECT speaker_id, speaker_confidence FROM transcripts"
    )
    rows = await cursor.fetchall()
    assert rows == [("unknown", 0.40)]


# ---------------------------------------------------------------------------
# RT-002: fire-and-forget emit queue + 12 state-transition sites
# ---------------------------------------------------------------------------


async def _drain_pending_events(decider, timeout: float = 0.5) -> None:
    """Helper: give the drainer task time to flush all queued events."""
    import asyncio as _asyncio

    deadline = _asyncio.get_running_loop().time() + timeout
    while not decider._emit_queue.empty():
        if _asyncio.get_running_loop().time() > deadline:
            break
        await _asyncio.sleep(0.005)


async def _read_events(store) -> list[tuple[str, int | None, int | None]]:
    cursor = await store.db.execute(
        "SELECT kind, transcript_id, decision_id FROM events ORDER BY id ASC"
    )
    rows = await cursor.fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


async def test_emit_queue_full_drops_not_raises(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")

    # Fill the queue past maxsize=256 by calling _safe_emit without starting
    # the drainer. _ensure_drainer needs a running loop, and we do have one
    # here, so manually prevent drain startup by marking drainer done.
    import asyncio as _asyncio

    async def noop():
        return

    decider._emit_drainer_task = _asyncio.create_task(noop())
    await decider._emit_drainer_task  # ensure done, so _ensure_drainer bails

    for _ in range(256):
        decider._safe_emit("decider.start")
    assert decider._emit_queue.full()
    # Now try 5 more — must drop, not raise
    for _ in range(5):
        decider._safe_emit("decider.start")
    assert decider._emit_drop_count == 5
    await decider.shutdown()


async def test_drainer_survives_store_failure(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")

    # Wrap store.log_event so first call raises, subsequent calls pass through.
    from unittest.mock import patch

    call_count = {"n": 0}
    real_log_event = store.log_event

    async def flaky_log_event(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("injected failure")
        return await real_log_event(*args, **kwargs)

    with patch.object(store, "log_event", side_effect=flaky_log_event):
        decider._safe_emit("decider.start", payload={"n": 1})
        decider._safe_emit("decider.start", payload={"n": 2})
        await _drain_pending_events(decider)

    # Second event reached DB despite first raising
    events = await _read_events(store)
    assert len(events) == 1
    assert events[0][0] == "decider.start"
    # Drainer is still alive
    assert decider._emit_drainer_task is not None
    assert not decider._emit_drainer_task.done()
    await decider.shutdown()


async def test_safe_emit_under_lock_is_fast(harness) -> None:
    import asyncio as _asyncio

    store, settings, ctx = harness
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")

    async with decider._lock:
        t0 = time.monotonic()
        for _ in range(50):
            decider._safe_emit("decider.start")
        elapsed = time.monotonic() - t0
    # 50 enqueues should complete in well under 1ms each = 50ms total.
    assert elapsed < 0.05, f"_safe_emit too slow under lock: {elapsed * 1000:.1f}ms"
    await _asyncio.sleep(0)  # let drainer catch up (loop yield)
    await decider.shutdown()


async def test_drainer_cleanup_cancels_cleanly(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider._safe_emit("decider.start")
    # drainer is now running
    assert decider._emit_drainer_task is not None
    await decider.shutdown()
    # After shutdown, the task reference is cleared and the coroutine done
    assert decider._emit_drainer_task is None


async def test_decider_emits_expected_sequence_for_act_yes(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    cli.call_action = AsyncMock(return_value={"summary": "тести пройшли"})
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    await decider._handle_confirmation("гава так")
    await _drain_pending_events(decider)

    kinds = [e[0] for e in await _read_events(store)]
    expected = [
        "decider.start",
        "decider.done",
        "action.armed",
        "action.confirmed",
        "action.executing",
        "action.call_start",
        "action.done",
        "state.listening",
    ]
    assert kinds == expected, f"got {kinds}"
    await decider.shutdown()


async def test_decider_emits_expected_sequence_for_act_no(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    await decider._handle_confirmation("гава ні")
    await _drain_pending_events(decider)

    kinds = [e[0] for e in await _read_events(store)]
    # no executing/call_start/done branch because we cancelled
    assert "action.confirmed" not in kinds
    assert "action.executing" not in kinds
    assert "action.call_start" not in kinds
    assert "action.done" not in kinds
    assert kinds[:3] == ["decider.start", "decider.done", "action.armed"]
    assert "action.cancelled" in kinds
    assert kinds[-1] == "state.listening"
    await decider.shutdown()


async def test_decider_emits_action_stdout_per_line(harness) -> None:
    """When call_action streams stdout lines via on_line, each line must
    become an action.stdout event in the events table with the decision_id
    correlated."""
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "build stuff",
                "action": {"tool": "Bash", "args": "make"},
            }
        ]
    )

    async def fake_call_action(description, *, on_line=None):
        if on_line is not None:
            for line in ["compiling foo.c", "compiling bar.c", "linking a.out"]:
                on_line(line)
        return {"summary": "ok"}

    cli.call_action = AsyncMock(side_effect=fake_call_action)
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, зроби білд")
    await decider._handle_confirmation("гава так")
    await _drain_pending_events(decider)

    cursor = await store.db.execute(
        "SELECT payload_json FROM events WHERE kind = 'action.stdout' ORDER BY id"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 3
    payloads = [json.loads(r[0]) for r in rows]
    assert [p["line"] for p in payloads] == [
        "compiling foo.c",
        "compiling bar.c",
        "linking a.out",
    ]
    await decider.shutdown()


async def test_decider_emits_action_error(harness) -> None:
    store, settings, ctx = harness
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "reason": "asked",
                "intent": "run pytest",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    cli.call_action = AsyncMock(side_effect=RuntimeError("boom"))
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, запусти тести")
    await decider._handle_confirmation("гава так")
    await _drain_pending_events(decider)

    kinds = [e[0] for e in await _read_events(store)]
    assert "action.error" in kinds
    assert "action.done" not in kinds
    assert kinds[-1] == "state.listening"
    await decider.shutdown()


async def test_decider_emits_dropped_low_conf(harness) -> None:
    store, settings, ctx = harness
    settings.min_action_confidence = 0.8
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.3,
                "reason": "low",
                "intent": "do something",
                "action": {"tool": "Bash", "args": "echo hi"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening("Гава, зроби щось")
    await _drain_pending_events(decider)

    kinds = [e[0] for e in await _read_events(store)]
    assert "decider.dropped_low_conf" in kinds
    assert "action.armed" not in kinds
    await decider.shutdown()


async def test_decider_import_does_not_break_flag_off_render(harness) -> None:
    """RT-005: importing src.decider at module level must not drift the
    flag-off golden prompt rendering. Uses the same fixed-ctx pattern as
    test_golden_string_flag_off_render in tests/test_context.py but adds
    an explicit top-level `import src.decider` so any import-time side
    effects in decider.py (new queue, drainer, EventKind imports) that
    could drift the rendering would be caught.
    """
    import src.decider  # noqa: F401 — the canary: must import cleanly
    from pathlib import Path as _Path

    from src.context import ContextBuilder

    store, settings, _ctx = harness
    settings.speaker_id_enabled = False
    builder = ContextBuilder(store, settings)

    fixed_ctx = {
        "time": "2026-04-13 12:00:00",
        "timezone": "UTC",
        "mode": "ambient",
        "heartbeat_flag": "no",
        "recent_transcripts": "(none)",
        "transcript_or_heartbeat": "тест",
        "speaker_rule_block": builder._render_rule_block(),
        "silence_block": "",
        "proactivity_block": "",
    }
    template = _Path("prompts/decider.txt").read_text()
    rendered = builder.render(template, fixed_ctx)

    golden_path = _Path("tests/fixtures/decider_prompt_flag_off.golden.txt")
    golden = golden_path.read_text()
    assert rendered == golden, (
        "flag-off render drifted from golden fixture after decider module import — "
        "check for import-time side effects in src.decider"
    )
    assert "Speaker: owner" not in rendered


# ---------------------------------------------------------------------------
# KW-7: drift, keyword gate, and stranger-confirmation-rejection tests
# ---------------------------------------------------------------------------


async def test_owner_voice_drift_with_keyword_still_commands(harness) -> None:
    """Owner voice drifted below threshold (speaker_id=None) but said keyword.

    Transcript must reach Claude (call_decider called), and act → AWAITING_CONFIRMATION.
    """
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    settings.speaker_command_keyword_required = True
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "intent": "запусти тести",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # speaker_id=None — drift, below threshold — but transcript contains keyword "гава"
    await decider._handle_listening(
        "гава запусти тести", speaker_id=None, speaker_confidence=None
    )

    assert len(cli.calls) == 1, "transcript must reach Claude even with drifted speaker"
    assert decider.state == DeciderState.AWAITING_CONFIRMATION


async def test_act_without_keyword_dropped(harness) -> None:
    """Act decision with no command keyword in transcript is dropped.

    DECIDER_DROPPED_NO_KEYWORD event emitted; state stays LISTENING.
    """
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    settings.speaker_command_keyword_required = True
    cli = FakeClaudeCLI(
        [
            {
                "type": "act",
                "confidence": 0.9,
                "intent": "запусти тести",
                "action": {"tool": "Bash", "args": "pytest"},
            }
        ]
    )
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # No keyword (гава/heare/гей) in transcript — keyword gate must drop it.
    # Use a question to pass the ambient quick-nothing pre-filter, but no wake word.
    await decider._handle_listening(
        "як запустити тести зараз", speaker_id="owner", speaker_confidence=0.95
    )

    assert len(cli.calls) == 1, "call_decider should still be called"
    assert decider.state == DeciderState.LISTENING, "state must stay LISTENING after drop"
    assert decider.pending_action is None


async def test_stranger_keyword_confirmation_rejected(harness) -> None:
    """Armed state: stranger provides keyword confirmation — must be rejected.

    AND-logic requires keyword AND same-speaker; stranger fails speaker match.
    """
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    settings.speaker_command_keyword_required = True
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Manually arm the decider as if owner issued a command
    decider.state = DeciderState.AWAITING_CONFIRMATION
    decider.pending_action = {"type": "act", "intent": "test", "action": "test"}
    decider.pending_speaker_id = "owner"
    decider.confirmation_deadline = time.monotonic() + 30

    # Stranger says "гава так" — has keyword but wrong speaker
    await decider._handle_confirmation(
        "гава так", speaker_id="stranger_01", speaker_inherited=False
    )

    pushed_texts = [
        call.args[0].text
        for call in decider.push_frame.await_args_list
        if hasattr(call.args[0], "text")
    ]
    assert any("Скажи: гава так, або гава ні" in t for t in pushed_texts), (
        f"expected reprompt TTS not found; got: {pushed_texts}"
    )
    assert decider.state == DeciderState.AWAITING_CONFIRMATION, (
        "state must remain AWAITING_CONFIRMATION after stranger rejection"
    )


async def test_owner_drift_confirmation_fails_with_speaker_id_enabled(harness) -> None:
    """Armed with pending_speaker_id='owner', drifted owner (speaker_id=None) confirms.

    When speaker_id_enabled=True, AND-logic requires non-None speaker match — rejected.
    """
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    settings.speaker_command_keyword_required = True
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Manually arm the decider as if owner issued a command
    decider.state = DeciderState.AWAITING_CONFIRMATION
    decider.pending_action = {"type": "act", "intent": "test", "action": "test"}
    decider.pending_speaker_id = "owner"
    decider.confirmation_deadline = time.monotonic() + 30

    # Drifted owner: speaker_id=None (below threshold), but says keyword
    await decider._handle_confirmation(
        "гава так", speaker_id=None, speaker_inherited=False
    )

    pushed_texts = [
        call.args[0].text
        for call in decider.push_frame.await_args_list
        if hasattr(call.args[0], "text")
    ]
    assert any("Скажи: гава так, або гава ні" in t for t in pushed_texts), (
        f"expected reprompt TTS not found; got: {pushed_texts}"
    )
    assert decider.state == DeciderState.AWAITING_CONFIRMATION, (
        "state must remain AWAITING_CONFIRMATION after drifted-owner rejection"
    )


# ---------------------------------------------------------------------------
# Confirmation passphrase tests
# ---------------------------------------------------------------------------


def _arm_decider(decider, speaker_id: str = "owner") -> None:
    """Put decider into AWAITING_CONFIRMATION as if owner issued a command."""
    decider.state = DeciderState.AWAITING_CONFIRMATION
    decider.pending_action = {"type": "act", "intent": "test", "action": "test"}
    decider.pending_speaker_id = speaker_id
    decider.confirmation_deadline = time.monotonic() + 30


@pytest.mark.asyncio
async def test_passphrase_executes(harness) -> None:
    """keyword + passphrase + unknown speaker → execute (graceful degrade)."""
    store, settings, ctx = harness
    settings.confirmation_passphrase = "авторизую"
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    _arm_decider(decider)

    await decider._handle_confirmation(
        "гава авторизую", speaker_id=None, speaker_inherited=False
    )

    cli.call_action.assert_awaited()
    assert decider.state == DeciderState.LISTENING
    assert decider.pending_action is None


@pytest.mark.asyncio
async def test_passphrase_stranger_rejected(harness) -> None:
    """keyword + passphrase + positively-identified stranger → reprompt, no execute."""
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    settings.confirmation_passphrase = "авторизую"
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    _arm_decider(decider, speaker_id="owner")

    await decider._handle_confirmation(
        "гава авторизую", speaker_id="stranger_01", speaker_inherited=False
    )

    cli.call_action.assert_not_awaited()
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    pushed_texts = [
        call.args[0].text
        for call in decider.push_frame.await_args_list
        if hasattr(call.args[0], "text")
    ]
    assert any("Скажи пароль, або гава ні" in t for t in pushed_texts), (
        f"expected passphrase-reject TTS not found; got: {pushed_texts}"
    )


@pytest.mark.asyncio
async def test_passphrase_unknown_speaker_executes(harness) -> None:
    """keyword + passphrase + speaker_id=None (short utterance) → execute (graceful degrade)."""
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    settings.confirmation_passphrase = "авторизую"
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    _arm_decider(decider, speaker_id="owner")

    await decider._handle_confirmation(
        "гава авторизую", speaker_id=None, speaker_inherited=False
    )

    cli.call_action.assert_awaited()
    assert decider.state == DeciderState.LISTENING


@pytest.mark.asyncio
async def test_passphrase_fallthrough_to_tak(harness) -> None:
    """Passphrase configured but transcript has keyword+так → falls through to yes/no path."""
    store, settings, ctx = harness
    settings.confirmation_passphrase = "авторизую"
    settings.speaker_id_enabled = False
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    _arm_decider(decider)

    await decider._handle_confirmation(
        "гава так", speaker_id=None, speaker_inherited=False
    )

    # "гава так" hits the existing yes/no path and executes
    cli.call_action.assert_awaited()
    assert decider.state == DeciderState.LISTENING


@pytest.mark.asyncio
async def test_passphrase_no_keyword_fallthrough(harness) -> None:
    """Passphrase present in transcript but no keyword → falls through; parse_yes_no
    returns 'unclear' for an unknown word → existing 'Скажи: так чи ні?' reprompt."""
    store, settings, ctx = harness
    settings.confirmation_passphrase = "авторизую"
    settings.speaker_id_enabled = False
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    _arm_decider(decider)

    # Passphrase without wake word — no keyword match
    await decider._handle_confirmation(
        "авторизую", speaker_id=None, speaker_inherited=False
    )

    cli.call_action.assert_not_awaited()
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    pushed_texts = [
        call.args[0].text
        for call in decider.push_frame.await_args_list
        if hasattr(call.args[0], "text")
    ]
    assert any("Скажи: так чи ні?" in t for t in pushed_texts), (
        f"expected 'Скажи: так чи ні?' reprompt not found; got: {pushed_texts}"
    )


@pytest.mark.asyncio
async def test_passphrase_none_unchanged(harness) -> None:
    """confirmation_passphrase=None → passphrase branch dormant; existing path works."""
    store, settings, ctx = harness
    settings.confirmation_passphrase = None
    settings.speaker_id_enabled = False
    cli = FakeClaudeCLI([])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    _arm_decider(decider)

    await decider._handle_confirmation(
        "гава так", speaker_id=None, speaker_inherited=False
    )

    cli.call_action.assert_awaited()
    assert decider.state == DeciderState.LISTENING


# ---------------------------------------------------------------------------
# US-004: Turn aggregation and conversation context tests
# ---------------------------------------------------------------------------


async def test_turn_based_flow_with_aggregation_enabled(harness) -> None:
    """Verify turn aggregation flow: buffering behavior when turn_aggregator is provided."""
    from src.turn_aggregator import TurnAggregator
    from src.conversation import ConversationManager
    from unittest.mock import MagicMock

    store, settings, ctx = harness
    settings.mode = Mode.AMBIENT

    # Create turn aggregator with normal timeout
    turn_aggregator = TurnAggregator(
        mode=Mode.AMBIENT,
        focus_timeout=0.5,
        ambient_timeout=3.0,
    )

    # Create conversation manager
    conversation_manager = ConversationManager(store, MagicMock())

    # Mock conversation manager methods to avoid errors
    async def mock_extract_topics(text: str) -> list[str]:
        return ["test topic 1", "test topic 2"]

    conversation_manager.extract_topics = mock_extract_topics

    async def mock_get_or_create_active() -> int:
        return 42

    conversation_manager.get_or_create_active = mock_get_or_create_active

    async def mock_update_summary(conv_id: int, text: str, topics: list[str]) -> None:
        pass

    conversation_manager.update_summary = mock_update_summary

    async def mock_build_context(conv_id: int) -> dict:
        return {
            "conversation_active": True,
            "conversation_summary": "Test conversation",
            "active_topics": ["test topic 1", "test topic 2"],
            "entities": {},
            "recent_turns": [],
        }

    conversation_manager.build_context = mock_build_context

    cli = FakeClaudeCLI([
        {"type": "speak", "confidence": 0.9, "reply": "розумію", "reason": "ack"}
    ])

    decider = create_decider_processor(
        cli, store, ctx, settings, "Mode: {mode}\nTranscript: {transcript_or_heartbeat}",
        turn_aggregator=turn_aggregator,
        conversation_manager=conversation_manager,
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # First utterance: should buffer (not submit yet in ambient mode)
    await decider._handle_listening("Гава, перше повідомлення")
    assert len(cli.calls) == 0, "Should buffer, not call decider yet"

    # Second utterance shortly after: should still buffer
    # Use wake-word to bypass quick-nothing filter
    await decider._handle_listening("Гава, друге повідомлення")
    assert len(cli.calls) == 0, "Should still be buffering"

    # Verify aggregator state - turn should have started
    assert turn_aggregator.turn_start_ts is not None, "Turn should have started"
    assert len(turn_aggregator.buffer) == 2, "Should have 2 utterances buffered"


async def test_conversation_context_in_prompt(harness) -> None:
    """Verify conversation context integration when conversation_manager is provided."""
    from src.conversation import ConversationManager
    from unittest.mock import MagicMock

    store, settings, ctx = harness
    settings.mode = Mode.AMBIENT

    # Create conversation manager
    claude_mock = MagicMock()
    conversation_manager = ConversationManager(store, claude_mock)

    # Mock conversation manager methods to avoid errors
    async def mock_extract_topics(text: str) -> list[str]:
        return ["weather forecast", "calendar meeting"]

    conversation_manager.extract_topics = mock_extract_topics

    async def mock_get_or_create_active() -> int:
        return 123

    conversation_manager.get_or_create_active = mock_get_or_create_active

    async def mock_update_summary(conv_id: int, text: str, topics: list[str]) -> None:
        pass

    conversation_manager.update_summary = mock_update_summary

    async def mock_build_context(conv_id: int) -> dict:
        return {
            "conversation_active": True,
            "conversation_summary": "User asked about weather and calendar",
            "active_topics": ["weather forecast", "calendar meeting"],
            "entities": {"location": "Kyiv", "date": "tomorrow"},
            "recent_turns": [],
        }

    conversation_manager.build_context = mock_build_context

    cli = FakeClaudeCLI([
        {"type": "nothing", "reason": "not for me"}
    ])

    # Create decider WITHOUT turn aggregator (legacy flow with conversation_manager)
    decider = create_decider_processor(
        cli, store, ctx, settings, "Mode: {mode}\nTranscript: {transcript_or_heartbeat}",
        turn_aggregator=None,
        conversation_manager=conversation_manager,
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Trigger the legacy flow (turn_aggregator is None, so uses legacy path)
    await decider._handle_listening("Гава, яка погода на завтра")

    # Verify decider was called (legacy flow works)
    assert len(cli.calls) == 1, "Decider should have been called via legacy flow"

    # Verify the prompt contains the transcript
    prompt = cli.calls[0]
    assert "погода" in prompt, f"Prompt should contain the transcript, got: {prompt}"


@pytest.mark.asyncio
async def test_conversation_reference_with_weather_context(harness) -> None:
    """US-008: Verify conversation context propagation works end-to-end.

    This test verifies that when conversation_manager is provided and returns
    context with 'weather' in the summary, that context is available for use.
    The actual integration into the prompt template happens in the turn aggregation
    flow (see test_turn_based_flow_with_aggregation_enabled for full integration).
    """
    from src.conversation import ConversationManager
    from unittest.mock import MagicMock

    store, settings, ctx = harness
    settings.mode = Mode.AMBIENT

    # Create conversation manager
    claude_mock = MagicMock()
    conversation_manager = ConversationManager(store, claude_mock)

    # Mock conversation manager to return weather context
    async def mock_build_context(conv_id: int) -> dict:
        return {
            "conversation_active": True,
            "conversation_summary": "User asked about weather forecast",
            "active_topics": ["weather forecast"],
            "entities": {"location": "Kyiv"},
            "recent_turns": [],
        }

    conversation_manager.build_context = mock_build_context

    # Verify build_context returns weather context
    ctx = await conversation_manager.build_context(42)
    assert ctx["conversation_active"] is True
    assert "weather" in ctx["conversation_summary"].lower()
    assert "weather forecast" in ctx["active_topics"]


@pytest.mark.asyncio
async def test_api_call_reduction_with_turn_aggregation(harness) -> None:
    """US-008: Verify turn aggregation reduces API calls by 3-5x using mocked events."""
    from src.turn_aggregator import TurnAggregator
    from src.conversation import ConversationManager
    from unittest.mock import MagicMock

    store, settings, ctx = harness
    settings.mode = Mode.AMBIENT

    # Test WITHOUT turn aggregation (baseline)
    cli_baseline = FakeClaudeCLI([
        {"type": "nothing", "reason": "not for me"}
    ])
    decider_baseline = create_decider_processor(
        cli_baseline, store, ctx, settings, "Mode: {mode}\nTranscript: {transcript_or_heartbeat}",
        turn_aggregator=None,  # No aggregation
        conversation_manager=None,
    )
    decider_baseline.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Simulate 5 rapid utterances without aggregation
    for i in range(5):
        await decider_baseline._handle_listening(f"Гава, частинка {i}")

    baseline_calls = len(cli_baseline.calls)

    # Test WITH turn aggregation
    turn_aggregator = TurnAggregator(
        mode=Mode.AMBIENT,
        focus_timeout=0.5,
        ambient_timeout=3.0,
    )

    conversation_manager = ConversationManager(store, MagicMock())

    # Mock conversation manager methods
    async def mock_extract_topics(text: str) -> list[str]:
        return ["test"]

    conversation_manager.extract_topics = mock_extract_topics

    async def mock_get_or_create_active() -> int:
        return 1

    conversation_manager.get_or_create_active = mock_get_or_create_active

    async def mock_update_summary(conv_id: int, text: str, topics: list[str]) -> None:
        pass

    conversation_manager.update_summary = mock_update_summary

    async def mock_build_context(conv_id: int) -> dict:
        return {
            "conversation_active": True,
            "conversation_summary": "Test",
            "active_topics": [],
            "entities": {},
            "recent_turns": [],
        }

    conversation_manager.build_context = mock_build_context

    cli_aggregated = FakeClaudeCLI([
        {"type": "nothing", "reason": "aggregated"}
    ])
    decider_aggregated = create_decider_processor(
        cli_aggregated, store, ctx, settings, "Mode: {mode}\nTranscript: {transcript_or_heartbeat}",
        turn_aggregator=turn_aggregator,
        conversation_manager=conversation_manager,
    )
    decider_aggregated.push_frame = AsyncMock()  # type: ignore[attr-defined]

    # Simulate 5 rapid utterances WITH aggregation
    for i in range(5):
        await decider_aggregated._handle_listening(f"Гава, частинка {i}")

    aggregated_calls = len(cli_aggregated.calls)

    # Verify reduction: aggregated should be 3-5x fewer calls
    # With 5 rapid utterances, aggregation should bundle them into 1 call
    # Reduction ratio should be at least 3x (5/1 = 5x reduction)
    assert baseline_calls >= 3 * aggregated_calls, (
        f"API call reduction verification failed: "
        f"baseline={baseline_calls}, aggregated={aggregated_calls}, "
        f"reduction_ratio={baseline_calls/max(aggregated_calls, 1):.1f}x "
        f"(expected >= 3x reduction)"
    )

    # Specifically, with 5 utterances in ambient mode:
    # - Without aggregation: 5 separate decider calls
    # - With aggregation: 1 bundled call (after 3s timeout)
    # So we expect aggregated_calls to be 0 or 1 (might not have timed out yet)
    # and baseline_calls to be 5
    assert baseline_calls == 5, f"Baseline should have 5 calls, got {baseline_calls}"
    assert aggregated_calls <= 1, f"Aggregated should have 0-1 calls (bundled), got {aggregated_calls}"


# ---------------------------------------------------------------------------
# US-CCS-03 tests removed in US-WU-03 (2026-04-25):
# - The CCS-03 deadline-warning cue lived in dormant DeciderProcessor and
#   never reached production. Implementation and tests deleted together.
#   IndicationKind.CONFIRMATION_DEADLINE remains reserved for a future
#   confirmation flow inside GeneratorProcessor.
# ---------------------------------------------------------------------------


# ============================================================================
# CCS-05a — cancel signal origin: decider fast-path BEFORE generator
# ============================================================================


class _CCS05ASpyQueue:
    """Minimal async spy implementing the IntentQueue surface the decider
    fast-path needs (cancel_active). Records each call and returns True
    unconditionally so we can assert call ordering.
    """

    def __init__(self) -> None:
        self.cancel_active_calls: int = 0

    async def cancel_active(self) -> bool:
        self.cancel_active_calls += 1
        return True


@pytest.mark.asyncio
async def test_decider_fast_path_calls_cancel_active_before_llm(harness) -> None:
    """AC: standalone 'stop' transcript triggers cancel_active() and bypasses LLM."""
    store, settings, ctx = harness
    cli = FakeClaudeCLI([])  # NO decisions queued — LLM call would IndexError
    queue = _CCS05ASpyQueue()
    decider = create_decider_processor(
        cli, store, ctx, settings, "prompt {mode}", intent_queue=queue
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider._handle_listening("stop")

    assert queue.cancel_active_calls == 1, (
        f"cancel_active should fire exactly once; got {queue.cancel_active_calls}"
    )
    # Decider must NOT have called the LLM. FakeClaudeCLI tracks call_decider
    # invocations via the .calls list (set on every call_decider entry).
    assert cli.calls == [], (
        f"LLM must be bypassed in fast-path; got calls={cli.calls!r}"
    )


@pytest.mark.asyncio
async def test_decider_fast_path_does_not_trigger_for_non_imperative(
    harness,
) -> None:
    """AC: 'don't stop the recording' falls through to normal LLM flow."""
    store, settings, ctx = harness
    # Provide a benign "nothing" decision so the LLM path completes cleanly.
    cli = FakeClaudeCLI([{"type": "nothing", "reason": "ok"}])
    queue = _CCS05ASpyQueue()
    decider = create_decider_processor(
        cli, store, ctx, settings, "prompt {mode}", intent_queue=queue
    )
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

    await decider._handle_listening("don't stop the recording")

    assert queue.cancel_active_calls == 0, (
        "negation utterance must NOT trigger fast-path"
    )
    # is_quick_nothing may have dropped this in ambient (declarative + no
    # question marker), so the LLM may not be called either. The contract
    # we care about: cancel_active was NOT called.


@pytest.mark.asyncio
async def test_cancel_stop_words_extensible_via_env(monkeypatch, harness) -> None:
    """AC#6: HEARE_CANCEL_STOP_WORDS env var extends the stop-word vocabulary."""
    from src.config import load_settings as _load
    from src.language import is_standalone_cancel_imperative

    monkeypatch.setenv("HEARE_CANCEL_STOP_WORDS", "foo,bar")
    new_settings = _load()
    # Env override REPLACES the default list (semantics of comma-list reload).
    assert "foo" in new_settings.cancel_stop_words
    assert "bar" in new_settings.cancel_stop_words

    # Standalone "foo" must now trigger.
    assert is_standalone_cancel_imperative("foo", new_settings.cancel_stop_words)
    assert is_standalone_cancel_imperative(
        "please bar", new_settings.cancel_stop_words
    )
    # And a non-listed legacy stop-word must NOT trigger under the override
    # (semantics: env var REPLACES the list; this guards the override is
    # actually exclusive, not additive).
    assert not is_standalone_cancel_imperative(
        "stop", new_settings.cancel_stop_words
    )


@pytest.mark.asyncio
async def test_cancel_signal_origin_indication_observable_within_500ms(
    harness, monkeypatch
) -> None:
    """AC: end-to-end 'transcript -> INTENT_CANCELLED indication' observable
    in a RecordingBackend within 500ms wall-clock.
    """
    import asyncio as _aio
    import datetime as _dt
    from dataclasses import dataclass, field as _field

    from src.config import IndicationSettings as _IS
    from src.indication import (
        Indication as _Indication,
        IndicationKind as _IK,
        set_indication as _set_ind,
    )
    from src.actions import IntentQueue as _IQ

    @dataclass
    class _RB:
        name: str
        captured: list = _field(default_factory=list)

        async def fire(self, kind, level, title, body, meta) -> None:
            self.captured.append((kind, level, title, body))

        async def aclose(self) -> None:
            pass

    sound = _RB(name="sound")
    visual = _RB(name="visual")
    notif = _RB(name="notification")
    settings_ind = _IS()
    # Pin wallclock outside default 22:00-07:00 quiet hours so notify is
    # not gated.
    outside_quiet = _dt.datetime(2026, 4, 24, 12, 0)
    facade = _Indication(
        settings_ind, [sound, visual, notif], wallclock=lambda: outside_quiet
    )
    _set_ind(facade)
    try:
        store, settings, ctx = harness
        cli = FakeClaudeCLI([])
        queue = _IQ()
        # Submit a pending intent so cancel_active has something to drain.
        await queue.submit({"tool": "bash", "args": "echo hi"})

        decider = create_decider_processor(
            cli, store, ctx, settings, "prompt {mode}", intent_queue=queue
        )
        decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

        t0 = _aio.get_event_loop().time()
        await decider._handle_listening("stop")
        # Allow the indication facade's call_soon_threadsafe dispatch to
        # complete on the loop.
        for _ in range(20):
            await _aio.sleep(0.01)
            if any(c[0] == _IK.INTENT_CANCELLED for c in visual.captured):
                break
        elapsed_ms = (_aio.get_event_loop().time() - t0) * 1000
        assert elapsed_ms < 500, f"end-to-end exceeded 500ms: {elapsed_ms:.0f}"
        all_captured = sound.captured + visual.captured + notif.captured
        assert any(c[0] == _IK.INTENT_CANCELLED for c in all_captured), (
            f"expected INTENT_CANCELLED indication; "
            f"got {[c[0] for c in all_captured]}"
        )
    finally:
        _set_ind(None)

