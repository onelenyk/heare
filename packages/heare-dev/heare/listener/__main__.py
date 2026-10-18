"""The listener, pointed at this app.

    uv run python -m heare.listener [rounds] [agent]

Real speech into the voice channel, the real recogniser, model and
synthesiser, and a second recogniser on what would have left the
speaker. It costs cents and needs the keys, so it is run on purpose and
is not part of the test suite.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from heare.app import build
from heare.channels.voice.channel import build as build_voice
from heare.config import Settings
from heare.listener import scenarios
from heare.listener.tape import Tape


def _plant(db: Path, text: str) -> None:
    """Something the person said a day ago, for the recall question."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO transcripts (ts, text, mode, agent_spoken, source) "
        "VALUES (?, ?, 'spine', 0, 'voice')", (time.time() - 86400, text),
    )
    conn.commit()
    conn.close()


async def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    # A database of its own: nothing said here enters the person's history.
    db = Path(tempfile.mkdtemp(prefix="heare-listen-")) / "heare.db"
    # A folder of its own for an agent to look into.
    work = db.parent / "work"
    work.mkdir()
    (work / "alpha.txt").write_text("перший", encoding="utf-8")
    (work / "beta.md").write_text("# другий", encoding="utf-8")
    agent = sys.argv[2] if len(sys.argv) > 2 else ""
    settings = Settings(db_path=db, executor=agent or "none", executor_cwd=str(work),
                        screens_dir=db.parent / "screens")
    tape = Tape()
    await tape.start()
    core = build(settings)
    _plant(db, scenarios.REMEMBERED)
    voice = build_voice(tape, settings)
    core.add_input(voice)
    core.add_output(voice)
    running = asyncio.create_task(core.run())
    await asyncio.sleep(0.5)
    try:
        summary = await scenarios.run(tape, rounds, recall=True, work=bool(agent))
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await tape.stop()
    return 1 if summary["answers_with_problems"] else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    sys.exit(asyncio.run(main()))
