"""Drive the main pipeline from text and measure what comes out.

The same instrument as ``src/core/harness.py``, pointed at
``build_pipeline`` — the daemon with all of its stages, tools, modes and
persistence. Devices are never opened: a turn is started by appending a
user message with ``run_llm=True``, and two probe stages record what the
model said and what the speaker would have played.

Until this existed, no test walked the path from a person speaking to the
assistant answering. The suite covered what was written; the failures
lived in what was assembled.

    uv run python -m src.pipeline.harness "скажи котра година"
    uv run python -m src.pipeline.harness --window 30 --interject "стоп" ...

What it proves: the model answers, tools run, TTS produces audio, and how
long each takes. What it cannot prove: anything acoustic.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("harness")

# Silence longer than this separates one utterance from the next.
GAP = 1.5


@dataclass
class Turn:
    prompt: str
    started: float
    first_text_at: float | None = None
    first_audio_at: float | None = None
    text: str = ""
    audio_bytes: int = 0
    utterances: list[float] = field(default_factory=list)
    interjected_at: float | None = None
    _last_audio: float = 0.0

    @property
    def to_first_text_ms(self) -> float | None:
        if self.first_text_at is None:
            return None
        return (self.first_text_at - self.started) * 1000

    @property
    def to_first_audio_ms(self) -> float | None:
        if self.first_audio_at is None:
            return None
        return (self.first_audio_at - self.started) * 1000


def _make_probe(turn_ref: dict):
    from pipecat.frames.frames import LLMTextFrame, TTSAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class Probe(FrameProcessor):
        """Observe-only: records timings, forwards everything unchanged."""

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            turn = turn_ref.get("turn")
            if turn is not None:
                if isinstance(frame, LLMTextFrame):
                    if turn.first_text_at is None:
                        turn.first_text_at = time.monotonic()
                    turn.text += frame.text
                elif isinstance(frame, TTSAudioRawFrame):
                    now = time.monotonic()
                    if turn.first_audio_at is None:
                        turn.first_audio_at = now
                    if now - turn._last_audio > GAP:
                        turn.utterances.append((now - turn.started) * 1000)
                    turn._last_audio = now
                    turn.audio_bytes += len(frame.audio)
            await self.push_frame(frame, direction)

    return Probe()


def _load_env() -> None:
    """Put ~/.heare/.env into the environment, as the launcher does."""
    import os

    home = Path(os.environ.get("HEARE_HOME", Path.home() / ".heare"))
    for candidate in (home / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text("utf-8", "replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def _build_daemon(
    *,
    scratch_db: bool = False,
    overrides: dict | None = None,
    audio_probes: list | None = None,
    post_stt_stages: list | None = None,
    post_llm_stages: list | None = None,
    pre_output_stages: list | None = None,
):
    """Assemble the daemon's pipeline with the devices left shut.

    Shared with src/pipeline/room.py, which needs the same daemon with
    different stages watching it.
    """
    from src.agent.identity import load_identity, render_persona
    from src.config import load_settings
    from src.memory.factory import create_memory_backend
    from src.pipeline.build import build_pipeline
    from src.state import State
    from src.store.context import ContextBuilder
    from src.store.conversation import ConversationManager
    from src.store.storage import TranscriptStore

    # The daemon is normally started by the menubar app, which loads
    # ~/.heare/.env first; load_settings only reads os.environ. Without
    # this the harness dies inside the STT client constructor with a
    # message about OPENAI_API_KEY, which is a confusing way to say
    # "GROQ_API_KEY was never in the environment".
    _load_env()

    settings = load_settings()

    if scratch_db:
        # Scenarios get their own database. Three reasons, and the third
        # is the one that bit: concurrent runs deadlocked on "database is
        # locked"; test conversations were being written into the real
        # history; and the accumulated context made runs differ from each
        # other — a jellyfish panel left on screen days ago sent the
        # worker off to take a screenshot when asked about disk space.
        import tempfile

        scratch = Path(tempfile.mkdtemp(prefix="heare-room-"))
        settings.db_path = scratch / "room.db"
        logger.info("scenario database: %s", settings.db_path)

    # Scenarios need to be able to switch one thing off and re-measure —
    # a claim like "the canceller is eating the interruption" is worth
    # nothing until the same run has been made with it disabled.
    for key, value in (overrides or {}).items():
        setattr(settings, key, value)

    store = TranscriptStore(settings.db_path)
    await store.init()

    state = State(settings.db_path)
    await state.init()
    # The daemon persists mute across restarts, so a session that ended
    # with a muted microphone silently mutes every later test run too:
    # input_mute_gate drops the audio before STT and the only symptom is
    # that nothing was ever heard. Simulated microphones are never muted.
    if state.get_bool("mute_mic"):
        logger.info("mute_mic was on in the saved state — clearing it for this run")
        state.set_cache_only("mute_mic", "0")

    conversation_manager = ConversationManager(store)
    memory_backend = create_memory_backend(settings)
    await memory_backend.initialize()

    context_builder = ContextBuilder(
        store,
        settings,
        conversation_manager,
        memory_backend=memory_backend,
    )

    # An existing identity if the daemon has one, a plain stand-in
    # otherwise — this runs without network bootstrap on purpose.
    identity = load_identity(settings.identity_file) or {
        "name": "heare",
        "emoji": "🎧",
    }
    template = Path(__file__).resolve().parents[2] / "prompts" / "persona.txt"
    persona = render_persona(
        template.read_text() if template.exists() else "{name}", identity
    )

    built = await build_pipeline(
        settings,
        store,
        context_builder,
        persona=persona,
        state=state,
        conversation_manager=conversation_manager,
        memory_backend=memory_backend,
        audio=False,
        audio_probes=audio_probes,
        post_stt_stages=post_stt_stages,
        post_llm_stages=post_llm_stages,
        pre_output_stages=pre_output_stages,
    )
    return built[0], memory_backend


async def _build(turn_ref: dict):
    """The text harness's own wiring: a probe either side of TTS."""
    return await _build_daemon(
        post_llm_stages=[_make_probe(turn_ref)],
        pre_output_stages=[_make_probe(turn_ref)],
    )


async def run_turns(
    prompts: list[str],
    *,
    timeout: float = 20.0,
    interject: str = "",
    interject_at: float = 5.0,
) -> list[Turn]:
    from pipecat.frames.frames import EndFrame, LLMMessagesAppendFrame
    from pipecat.pipeline.runner import PipelineRunner

    turn_ref: dict = {}
    task, memory_backend = await _build(turn_ref)

    runner = PipelineRunner(handle_sigint=False)
    runner_task = asyncio.create_task(runner.run(task))
    await asyncio.sleep(0.5)  # let the pipeline reach a running state

    results: list[Turn] = []
    for prompt in prompts:
        turn = Turn(prompt=prompt, started=time.monotonic())
        turn_ref["turn"] = turn
        await task.queue_frames(
            [
                LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": prompt}], run_llm=True
                )
            ]
        )
        # The window stays open for its full length: delegated or slow
        # work answers in a later utterance, and stopping at the first
        # one would measure exactly the thing worth watching.
        deadline = time.monotonic() + timeout
        interjected = False
        while time.monotonic() < deadline:
            if (
                interject
                and not interjected
                and time.monotonic() - turn.started >= interject_at
            ):
                interjected = True
                turn.interjected_at = (time.monotonic() - turn.started) * 1000
                logger.info("interjecting at %.1fs: %s", interject_at, interject)
                await task.queue_frames(
                    [
                        LLMMessagesAppendFrame(
                            messages=[{"role": "user", "content": interject}],
                            run_llm=True,
                        )
                    ]
                )
            await asyncio.sleep(0.05)
        results.append(turn)
        turn_ref["turn"] = None

    await task.queue_frames([EndFrame()])
    try:
        await asyncio.wait_for(runner_task, timeout=10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        runner_task.cancel()
    await memory_backend.close()
    return results


def _report(turns: list[Turn]) -> int:
    ok = True
    print("\n" + "─" * 66)
    for t in turns:
        text_ms = t.to_first_text_ms
        audio_ms = t.to_first_audio_ms
        print(f"  “{t.prompt}”")
        print(f"    first token   {f'{text_ms:.0f} ms' if text_ms else 'NONE'}")
        print(f"    first audio   {f'{audio_ms:.0f} ms' if audio_ms else 'NONE'}")
        spoken = ", ".join(f"{u:.0f} ms" for u in t.utterances) or "—"
        print(f"    utterances    {len(t.utterances)}  at {spoken}")
        print(f"    audio         {t.audio_bytes} bytes")
        if t.interjected_at is not None:
            print(f"    interjected   {t.interjected_at:.0f} ms")
        print(f"    said          {t.text.strip()[:220] or '—'}")
        if not t.audio_bytes:
            ok = False
        print()
    print("─" * 66)
    print("ROUND TRIP OK" if ok else "NO AUDIO — the turn died somewhere")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("prompts", nargs="*", help="what to say to it")
    p.add_argument("--window", type=float, default=20.0, help="seconds per turn")
    p.add_argument("--interject", default="", help="say this mid-turn")
    p.add_argument("--interject-at", type=float, default=5.0)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    prompts = args.prompts or ["Привіт. Скажи одним реченням, як ти себе почуваєш."]
    return _report(
        asyncio.run(
            run_turns(
                prompts,
                timeout=args.window,
                interject=args.interject,
                interject_at=args.interject_at,
            )
        )
    )


if __name__ == "__main__":
    sys.exit(main())
