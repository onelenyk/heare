"""The listener, pointed at the app in ``src/``: the bar the new one must clear.

    uv run python -m tests.e2e.listen_old

Not a pytest module on purpose. It pays for the recogniser and the model,
and it reads the real keys, which the suite's conftest forbids.

Everything is the running assistant's own wiring (``_build_loop``) with
one substitution: the device is tape. The database is a temporary file,
so nothing said here enters the person's own history.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from heare.listener import scenarios
from heare.listener.tape import Tape

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 2


async def main() -> int:
    from src.config import load_env, load_settings
    from src.spine.main import _build_loop, _close_loop

    load_env()
    settings = load_settings()
    settings.db_path = Path(tempfile.mkdtemp(prefix="heare-listen-")) / "heare.db"
    tape = Tape()
    await tape.start()
    loop = await _build_loop(
        settings, audio=tape, voice="",
        hold_s=float(getattr(settings, "spine_turn_hold_seconds", 0.8)),
        full=True, without="engine,roles,watcher,repeats,memory,hear_all",
    )
    running = asyncio.create_task(loop.run())
    await asyncio.sleep(0.5)
    try:
        summary = await scenarios.run(tape, ROUNDS)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await tape.stop()
        await _close_loop(loop)

    return 1 if summary["answers_with_problems"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
