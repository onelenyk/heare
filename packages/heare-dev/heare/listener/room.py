"""The listener in a room where something is playing.

    uv run python -m heare.listener.room

By voice, on tape: real recognition, model and synthesis, and a sense of
the surroundings the test turns by hand — music on, music off. What a
person would notice: while it plays, speech that names nobody is left
alone and the name still gets an answer; when it is quiet again, they
are understood without the name.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from heare.app import build
from heare.channels.voice.channel import build as build_voice
from heare.config import Settings
from heare.listener import ear
from heare.listener.tape import Tape
from heare.surroundings.watch import Surroundings


async def said_nothing(tape: Tape, phrase: str, wait: float = 9.0) -> bool:
    """Say it and listen: True when no sound followed."""
    mark = time.monotonic()
    tape.say(await ear.spoken(phrase))
    await tape.said()
    await asyncio.sleep(wait)
    return not tape.since(mark)


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="heare-room-"))
    settings = Settings(path=tmp / "config.toml", db_path=tmp / "heare.db",
                        executor="none")
    real = Settings()
    for name in ("llm_provider", "deepseek_model", "openrouter_model"):
        if getattr(real, name):
            settings._overrides[name] = getattr(real, name)
    tape = Tape()
    await tape.start()
    core = build(settings)
    voice = build_voice(tape, settings)
    core.add_input(voice)
    core.add_output(voice)
    music: list[str] = []
    import heare.surroundings.watch as watch

    watch.EVERY_S, watch.QUIET_AFTER_S = 0.2, 0.5
    core.add_input(Surroundings(sense=lambda: list(music)))
    running = asyncio.create_task(core.run())
    await asyncio.sleep(0.6)
    found: dict = {}
    try:
        music.append("Music")
        await asyncio.sleep(0.6)
        found["music_no_name_left_alone"] = await said_nothing(
            tape, "Яка столиця Франції?")
        answer = await ear.ask(tape, "Дока, яка столиця Франції?")
        print(answer.line(), flush=True)
        found["music_with_name_answered"] = answer.has("париж")
        # The answer opened the window in which the name is not needed;
        # let it close, so what follows is really speech without the name.
        voice.wake.sleep()
        found["music_no_name_after_answer_left_alone"] = await said_nothing(
            tape, "А яка столиця Італії?")
        music.clear()
        await asyncio.sleep(1.2)
        voice.wake.sleep()
        answer = await ear.ask(tape, "Котра зараз година?")
        print(answer.line(), flush=True)
        found["quiet_no_name_answered"] = bool(answer.text.strip())
        found["overheard_reasons"] = [
            e.data.get("why") for e in core.state.journal if e.kind == "overheard"
        ]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await tape.stop()
    print(json.dumps(found, ensure_ascii=False, indent=1), flush=True)
    return 0 if all(v for k, v in found.items() if k != "overheard_reasons") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
