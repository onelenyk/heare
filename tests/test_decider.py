"""Tests for DeciderProcessor state transitions using a fake ClaudeCLI.

We construct the DeciderProcessor via create_decider_processor which pulls
the pipecat base classes at call time. These tests skip themselves if
pipecat is not importable so the suite stays runnable on lean environments.
"""
from __future__ import annotations

import tempfile
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
    await decider._handle_confirmation("так")
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
    await decider._handle_confirmation("ні")
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
    await decider._handle_confirmation("так")
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
    await decider._handle_confirmation("так")
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

async def test_non_owner_dropped_in_listening(harness) -> None:
    store, settings, ctx = harness
    settings.speaker_id_enabled = True
    cli = FakeClaudeCLI([{"type": "speak", "confidence": 0.9, "reply": "hi"}])
    decider = create_decider_processor(cli, store, ctx, settings, "prompt {mode}")
    decider.push_frame = AsyncMock()  # type: ignore[attr-defined]
    await decider._handle_listening(
        "Гава, привіт", speaker_id="unknown", speaker_confidence=0.4
    )
    # Non-owner must NOT reach Claude decider
    assert cli.calls == []
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
    # Inherited short-turn "так" must NOT execute
    await decider._handle_confirmation("так", speaker_id="owner", speaker_inherited=True)
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
    # Stranger says "так" — pending_speaker_id='owner', frame.speaker_id='unknown'
    await decider._handle_confirmation("так", speaker_id="unknown", speaker_inherited=False)
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
    await decider._handle_confirmation("так", speaker_id="owner", speaker_inherited=False)
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
    await decider._handle_confirmation("ні", speaker_id="owner", speaker_inherited=False)
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
