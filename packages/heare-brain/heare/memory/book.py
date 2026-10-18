"""Memory in the form the core asks for, on the database the old app wrote.

Every turn of both sides is written down as it happens — by this part,
never by a model deciding to. What was said before is found by the
search the old app arrived at one incident at a time (the time taken
from the question, word endings, freshness, no answering a question with
the question), and comes back as one sentence ready to be said.

The file is ``~/.heare/heare.db``, unchanged: everything said to the old
app is remembered by the new one.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Sequence

from heare.memory import search
from heare.memory.persist import SpinePersistence

# How a channel is recorded. The old rows say «voice» and «typed».
_SOURCES = {"voice": "voice", "text": "typed", "screen": "typed"}


class Book:
    def __init__(self, db_path: str | Path, names: Sequence[str] = ()) -> None:
        """names: the assistant's own name in every spelling. A question
        usually keeps it, and it is the one word that matches every row."""
        self._store = SpinePersistence(db_path)
        self._names = tuple(names)
        self._turn: int | None = None

    async def keep(self, role: str, text: str, channel: str) -> None:
        source = _SOURCES.get(channel, channel or "voice")
        if role == "user":
            self._turn = await asyncio.to_thread(
                self._store.log_user_turn, text, 1, source
            )
        elif self._turn is not None:
            await asyncio.to_thread(
                self._store.log_agent_reply, text, self._turn, source
            )

    async def recall(self, query: str, when: str = "") -> str:
        """Off the event loop: synchronous SQLite over thousands of rows."""
        now = datetime.now()
        fragments = await asyncio.to_thread(
            search.find, self._store, query, now=now, when=when, names=self._names,
        )
        return search.spoken(fragments, now=now)

    async def recent(self, n: int = 20) -> list[dict]:
        """The last exchanges, oldest first, for a screen that has just
        opened: ``[{"user": …, "agent": …}]``."""
        return await asyncio.to_thread(self._store.recent_exchanges, n)

    async def always(self) -> str:
        return ""

    def close(self) -> None:
        self._store.close()
