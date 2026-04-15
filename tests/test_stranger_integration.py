"""End-to-end integration tests for the speaker security gate.

Exercises AudioBufferProcessor → SpeakerTaggerProcessor → DeciderProcessor
with a mocked `speaker_id.embed` and a FakeClaudeCLI. The goal is to prove
that no synthetic stranger audio can cause the decider to transition to
EXECUTING, regardless of how cleverly the stranger times their utterance
around an owner's confirmation prompt.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

pipecat = pytest.importorskip("pipecat.frames.frames")
TranscriptionFrame = pipecat.TranscriptionFrame
AudioRawFrame = pipecat.AudioRawFrame
UserStartedSpeakingFrame = pipecat.UserStartedSpeakingFrame
UserStoppedSpeakingFrame = pipecat.UserStoppedSpeakingFrame

from src.config import DeciderState, Mode, Settings  # noqa: E402
from src.context import ContextBuilder  # noqa: E402
from src.decider import create_decider_processor  # noqa: E402
from src.speaker_gallery import SpeakerGallery  # noqa: E402
from src.speaker_processor import create_speaker_processors  # noqa: E402
from src.storage import TranscriptStore  # noqa: E402


class FakeClaudeCLI:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = decisions
        self.call_action = AsyncMock(return_value={"summary": "ok"})
        self.calls: list[str] = []

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        self.calls.append(prompt)
        return self._decisions.pop(0) if self._decisions else {"type": "nothing"}


def _owner_vec() -> np.ndarray:
    v = np.zeros(192, dtype=np.float32)
    v[0] = 1.0
    return v


def _stranger_vec() -> np.ndarray:
    v = np.zeros(192, dtype=np.float32)
    v[1] = 1.0
    return v


@pytest.fixture
async def rig(monkeypatch):
    """Assemble buffer + tagger + decider with a mocked embed + fake CLI."""
    import src.speaker_id as sid

    # Default: every embed returns the owner vector; tests override per-call
    # by mutating this dict before dispatching audio.
    state = {"next_vector": _owner_vec()}
    monkeypatch.setattr(
        sid, "embed", lambda pcm, sr, model: state["next_vector"].copy()
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "heare.db"
        gallery_path = Path(tmp) / "speakers.json"
        store = TranscriptStore(db_path)
        await store.init()
        gallery = SpeakerGallery(gallery_path)
        gallery.enroll_owner(_owner_vec(), label="owner")

        settings = Settings()
        settings.speaker_id_enabled = True
        settings.mode = Mode.AMBIENT
        settings.confirmation_timeout_seconds = 1

        ctx_builder = ContextBuilder(store, settings)

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
        decider = create_decider_processor(
            cli, store, ctx_builder, settings, "prompt {mode} {speaker_rule_block}"
        )
        decider.push_frame = AsyncMock()  # type: ignore[attr-defined]

        buffer, tagger = create_speaker_processors(
            settings, gallery, model=MagicMock(), sample_rate=16000
        )
        tagger.push_frame = AsyncMock()  # type: ignore[attr-defined]

        try:
            yield {
                "store": store,
                "gallery": gallery,
                "settings": settings,
                "cli": cli,
                "decider": decider,
                "buffer": buffer,
                "tagger": tagger,
                "state": state,
            }
        finally:
            await buffer.close()
            await store.close()


async def _run_turn_through_pipeline(
    rig: dict,
    text: str,
    duration_ms: float = 800.0,
) -> TranscriptionFrame:
    """Simulate one turn: User*SpeakingFrame + audio + TranscriptionFrame."""
    import asyncio

    sample_rate = 16000
    n_samples = int(sample_rate * duration_ms / 1000.0)
    audio_bytes = (np.zeros(n_samples, dtype=np.int16)).tobytes()

    prev_completed = rig["buffer"].latest_completed_turn_id()
    await rig["buffer"].process_frame(UserStartedSpeakingFrame(), None)
    await rig["buffer"].process_frame(
        AudioRawFrame(audio=audio_bytes, sample_rate=sample_rate, num_channels=1),
        None,
    )
    await rig["buffer"].process_frame(UserStoppedSpeakingFrame(), None)

    # Wait for a NEW completed turn id — not just non-None, since earlier
    # turns in the same rig may have already populated that field.
    for _ in range(30):
        current = rig["buffer"].latest_completed_turn_id()
        if current is not None and current != prev_completed:
            break
        await asyncio.sleep(0.01)

    frame = TranscriptionFrame(text=text, user_id="u", timestamp="t")
    frame.finalized = True
    await rig["tagger"].process_frame(frame, None)
    return frame


async def test_stranger_in_listening_act_gated_no_keyword(rig) -> None:
    """Scenario 1: stranger speaks while decider is LISTENING, no command keyword.

    KW-3: hard non-owner gate removed. The transcript reaches Claude, but the
    keyword gate blocks the act decision because the transcript lacks the
    command keyword. No EXECUTING transition, state stays LISTENING, transcript
    persisted with speaker_id=None for audit.
    """
    rig["state"]["next_vector"] = _stranger_vec()
    # Transcript with question word (bypasses quick-nothing filter) but no command keyword
    # (no гава/heare/гей) — keyword gate will drop the act decision
    frame = await _run_turn_through_pipeline(rig, "що там з файлами?")
    # Tagger identified this turn as non-owner (orthogonal to owner centroid)
    assert frame.speaker_id is None
    assert frame.speaker_confidence < 0.75

    # Hand to decider via process_frame path
    decider = rig["decider"]
    await decider._handle_listening(
        frame.text,
        speaker_id=frame.speaker_id,
        speaker_confidence=frame.speaker_confidence,
    )
    # KW-3: Claude IS called (hard gate removed), but act is dropped by keyword gate
    assert len(rig["cli"].calls) == 1
    assert decider.state == DeciderState.LISTENING
    # Audit trail: the transcript was stored with speaker_id=None
    recent = await rig["store"].recent_transcripts(5)
    assert len(recent) == 1


async def test_stranger_interrupts_confirmation_never_executes(rig) -> None:
    """Scenario 2: owner arms AWAITING_CONFIRMATION, stranger says 'так'.

    Decider must reject the stranger's confirmation via the speaker
    mismatch gate. Pending action must remain pending, no EXECUTING,
    and the owner must be re-prompted ('Скажи: так чи ні?').
    """
    decider = rig["decider"]

    # Step 1: owner arms the confirmation
    rig["state"]["next_vector"] = _owner_vec()
    owner_frame = await _run_turn_through_pipeline(
        rig, "Гава, запусти тести", duration_ms=800.0
    )
    assert owner_frame.speaker_id == "owner"
    await decider._handle_listening(
        owner_frame.text,
        speaker_id="owner",
        speaker_confidence=owner_frame.speaker_confidence,
    )
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert decider.pending_speaker_id == "owner"

    # Step 2: stranger says "так" — long enough to NOT inherit
    rig["state"]["next_vector"] = _stranger_vec()
    stranger_frame = await _run_turn_through_pipeline(
        rig, "так", duration_ms=600.0
    )
    assert stranger_frame.speaker_id is None  # orthogonal = no gallery match
    await decider._handle_confirmation(
        stranger_frame.text,
        speaker_id=stranger_frame.speaker_id,
        speaker_inherited=False,
    )
    # The confirmation must NOT have executed
    rig["cli"].call_action.assert_not_awaited()
    # Decider must stay pending so real owner can still confirm
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
    assert decider.pending_action is not None


async def test_short_turn_in_confirmation_never_executes(rig) -> None:
    """Scenario 3: owner arms, then a short <400ms turn appears with owner
    label via inheritance. Decider must refuse inherited confirmations.
    """
    decider = rig["decider"]

    rig["state"]["next_vector"] = _owner_vec()
    owner_frame = await _run_turn_through_pipeline(
        rig, "Гава, запусти тести", duration_ms=800.0
    )
    await decider._handle_listening(
        owner_frame.text,
        speaker_id="owner",
        speaker_confidence=owner_frame.speaker_confidence,
    )
    assert decider.state == DeciderState.AWAITING_CONFIRMATION

    # Short-turn "так" — voiced duration below 400ms threshold
    short_frame = await _run_turn_through_pipeline(
        rig, "так", duration_ms=300.0
    )
    assert short_frame.speaker_inherited is True
    assert short_frame.speaker_id == "owner"  # inherited from prev turn
    await decider._handle_confirmation(
        short_frame.text,
        speaker_id=short_frame.speaker_id,
        speaker_inherited=True,
    )
    # Inherited confirmation must NOT execute
    rig["cli"].call_action.assert_not_awaited()
    assert decider.state == DeciderState.AWAITING_CONFIRMATION
