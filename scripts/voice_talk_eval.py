"""Whole talks, as the recogniser would hand them over: no punctuation, fillers,
misheard names, broken sentences, and talk in the room not meant for it.

Manual and paid, on the same terms as ``voice_eval``: the real model, one core
for one talk, and nothing touches ``~/.heare``. No microphone: this tests what
is done with a transcript, not the hearing. Each turn of
``tests/heare/evals/voice_talks.jsonl`` takes ``voice_eval``'s checks, plus
``room`` (not addressed) and ``silent`` (it must say nothing).
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from heare.config import Settings  # noqa: E402
from heare.core import Message, i18n  # noqa: E402
from heare.model.chat import Chat  # noqa: E402
from voice_eval import _assemble, _load, _score  # noqa: E402

TALKS = Path(__file__).resolve().parents[1] / "tests/heare/evals/voice_talks.jsonl"
TURN_S = 45.0
ROOM_S = 15.0


async def say(core, queue: asyncio.Queue, turn: dict) -> tuple[list[str], str, str | None, float, bool]:
    room = bool(turn.get("room"))
    started = time.monotonic()
    core.hear(Message(text=turn["say"], channel="voice", addressed=not room))
    verbs: list[str] = []
    said, failed = "", None
    end = started + (ROOM_S if room else TURN_S)
    thinking = False
    while time.monotonic() < end:
        try:
            event = await asyncio.wait_for(queue.get(), 0.2)
        except TimeoutError:
            if room and thinking and core.state.phase == "idle":
                return verbs, said, failed, time.monotonic() - started, False
            continue
        if event.kind == "verb":
            verbs.append(str(event.data.get("name", "")))
        elif event.kind == "phase" and event.data.get("phase") == "thinking":
            thinking = True
        elif event.kind == "said":
            said, failed = str(event.data.get("text", "")), event.data.get("failed")
            return verbs, said, failed, time.monotonic() - started, False
    return verbs, said, failed, time.monotonic() - started, not room


async def talk(case: dict, chat: Chat, settings: Settings, root: Path) -> tuple[int, int]:
    core, _ = _assemble(case, chat, settings, root)
    queue = core.state.subscribe()
    running = asyncio.create_task(core.run(), name="voice-talk")
    await asyncio.sleep(0)
    good = 0
    print(f"\n── {case['id']}")
    try:
        for turn in case["turns"]:
            verbs, said, failed, seconds, late = await say(core, queue, turn)
            problems = _score(turn, verbs, said, failed, late)
            if turn.get("silent") and said:
                problems.append("spoke when it should have kept quiet")
            good += not problems
            mark = "ok  " if not problems else "FAIL"
            marks = " [room]" if turn.get("room") else ""
            print(f"{mark} {seconds:4.1f}s ти{marks}: {turn['say']}")
            print(f"           він: {' '.join(said.split()) or '(мовчить)'}"
                  f"{'  ← ' + ','.join(dict.fromkeys(verbs)) if verbs else ''}")
            for problem in problems:
                print(f"           - {problem}")
            await asyncio.sleep(0.3)
    finally:
        core.state.unsubscribe(queue)
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    return good, len(case["turns"])


async def run() -> int:
    settings = Settings()
    i18n.choose(lambda: str(settings.language))
    chat = Chat(settings)
    good = total = 0
    for case in _load(TALKS):
        with tempfile.TemporaryDirectory(prefix="heare-voice-talk-") as folder:
            done, count = await talk(case, chat, settings, Path(folder))
        good, total = good + done, total + count
    print(f"\n{good}/{total} turns ok")
    print(json.dumps({"ok": good, "of": total}))
    return 0 if good == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
