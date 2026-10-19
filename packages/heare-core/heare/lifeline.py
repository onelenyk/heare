"""A process the app started ends when the app does.

The app runs the brain and the keeper as its own children. If the app is
killed or crashes, nothing tells them: they live on as orphans holding the
ports, and the app started next finds its own children unable to bind. The
app names itself in ``HEARE_PARENT_PID``; a child that sees its parent
change goes too, the way it goes when asked — by SIGTERM to itself.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from collections.abc import Callable

EVERY_S = 2.0


def _leave() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def watch(on_gone: Callable[[], None] = _leave, every: float = EVERY_S) -> bool:
    """Watch the app that started this process, if one did; whether it did."""
    named = os.environ.get("HEARE_PARENT_PID", "").strip()
    if not named.isdigit():
        return False
    parent = int(named)

    def look() -> None:
        while os.getppid() == parent:
            time.sleep(every)
        on_gone()

    threading.Thread(target=look, name="heare-lifeline", daemon=True).start()
    return True
