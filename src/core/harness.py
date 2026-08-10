"""Drive the pipeline from text and measure what comes out.

No microphone, no speaker, no ears. The transport is built with its
devices disabled, a probe stage sits in front of the output, and a turn
is started by appending a user message with ``run_llm=True`` — the same
entry the text-injection path uses in the daemon today.

What this can prove: the model answers, the tools run, TTS produces
audio, and how long each takes. What it cannot prove: anything acoustic.
Echo cancellation and barge-in need a real room.

    uv run python -m src.core.harness "скажи котра година"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field

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
    tools: list[str] = field(default_factory=list)
    # Start of each audible utterance, ms after the prompt. A gap of more
    # than GAP seconds between audio frames starts a new one.
    utterances: list[float] = field(default_factory=list)
    interjected_at: float | None = None
    _last_audio: float = 0.0

    @property
    def to_first_text_ms(self) -> float | None:
        return None if self.first_text_at is None else (self.first_text_at - self.started) * 1000

    @property
    def to_first_audio_ms(self) -> float | None:
        return None if self.first_audio_at is None else (self.first_audio_at - self.started) * 1000


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


async def run_turns(
    prompts: list[str],
    *,
    timeout: float = 45.0,
    split: bool = True,
    interject: str = "",
    interject_at: float = 5.0,
) -> list[Turn]:
    from pipecat.frames.frames import EndFrame, LLMMessagesAppendFrame
    from pipecat.pipeline.runner import PipelineRunner

    from src.core import tools as core_tools
    from src.core.pipeline import build
    from src.core.settings import load_settings
    from src.core.state import State

    settings = load_settings()
    from src.memory.sqlite_backend import SQLiteBackend

    memory = SQLiteBackend(db_path=settings.db_path)
    await memory.initialize()

    # Two probes: LLMTextFrame is consumed by TTS, so text has to be
    # observed before it and audio after it.
    turn_ref: dict = {}
    task, _llm = await build(
        settings,
        State(),
        memory,
        audio=False,
        split=split,
        post_llm_stages=[_make_probe(turn_ref)],
        extra_stages=[_make_probe(turn_ref)],
    )

    # Record which tools actually ran, without touching the tool code.
    calls: list[str] = []
    for name, t in core_tools.REGISTRY.items():
        original = t.fn

        def wrap(fn=original, n=name):
            async def spy(*a, **kw):
                calls.append(n)
                return await fn(*a, **kw)

            return spy

        t.fn = wrap()

    runner = PipelineRunner(handle_sigint=False)
    runner_task = asyncio.create_task(runner.run(task))
    await asyncio.sleep(0.5)  # let the pipeline reach a running state

    results: list[Turn] = []
    for prompt in prompts:
        calls.clear()
        turn = Turn(prompt=prompt, started=time.monotonic())
        turn_ref["turn"] = turn
        await task.queue_frames(
            [
                LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": prompt}], run_llm=True
                )
            ]
        )
        # Keep the turn open for the whole window: a delegated job answers
        # in a second utterance, and cutting off at the first one would
        # measure exactly the thing this design is meant to change.
        deadline = time.monotonic() + timeout
        interjected = False
        while time.monotonic() < deadline:
            if (
                interject
                and not interjected
                and time.monotonic() - turn.started >= interject_at
            ):
                interjected = True
                logger.info("interjecting at %.1fs: %s", interject_at, interject)
                turn.interjected_at = (time.monotonic() - turn.started) * 1000
                await task.queue_frames(
                    [
                        LLMMessagesAppendFrame(
                            messages=[{"role": "user", "content": interject}],
                            run_llm=True,
                        )
                    ]
                )
            await asyncio.sleep(0.05)
        turn.tools = list(calls)
        results.append(turn)
        turn_ref["turn"] = None

    await task.queue_frames([EndFrame()])
    try:
        await asyncio.wait_for(runner_task, timeout=10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        runner_task.cancel()
    await memory.close()
    return results


def _report(turns: list[Turn]) -> int:
    ok = True
    print("\n" + "─" * 66)
    for t in turns:
        print(f"  “{t.prompt}”")
        text_ms = t.to_first_text_ms
        audio_ms = t.to_first_audio_ms
        print(f"    first token   {f'{text_ms:.0f} ms' if text_ms else 'NONE'}")
        print(f"    first audio   {f'{audio_ms:.0f} ms' if audio_ms else 'NONE'}")
        spoken = ", ".join(f"{u:.0f} ms" for u in t.utterances) or "—"
        print(f"    utterances    {len(t.utterances)}  at {spoken}")
        print(f"    audio         {t.audio_bytes} bytes")
        print(f"    tools         {', '.join(t.tools) or '—'}")
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
    p.add_argument(
        "--window",
        type=float,
        default=20.0,
        help="seconds to keep each turn open (a delegated job answers late)",
    )
    p.add_argument(
        "--single",
        action="store_true",
        help="control run: one agent holding every tool inline",
    )
    p.add_argument("--interject", default="", help="say this mid-work")
    p.add_argument("--interject-at", type=float, default=5.0)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    prompts = args.prompts or [
        "Привіт. Скажи одним реченням, як ти себе почуваєш.",
        "Виконай команду echo hello і скажи, що вона повернула.",
    ]
    print("mode:", "single agent (control)" if args.single else "voice + hands")
    return _report(
        asyncio.run(
            run_turns(
                prompts,
                timeout=args.window,
                split=not args.single,
                interject=args.interject,
                interject_at=args.interject_at,
            )
        )
    )


if __name__ == "__main__":
    sys.exit(main())
