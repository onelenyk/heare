"""How every process of heare writes its log: a time on each line, and a
warning that repeats written once, with how often it came.

Seven entry points each called ``basicConfig`` their own way, with no time
on a line: a log read the morning after could not say when the voice fell.
"""

from __future__ import annotations

import faulthandler
import logging
import signal
import sys
import time
from heare.core.i18n import _

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE = "%Y-%m-%d %H:%M:%S"
# The same warning again inside this is counted, not written.
QUIET_S = 300.0


class Once(logging.Filter):
    """Lets a repeated record through once per ``quiet_s``; the next one
    that passes says how many were held back. A repeat is the same logger,
    level and message template, whatever its arguments."""

    def __init__(self, quiet_s: float = QUIET_S, clock=time.monotonic) -> None:
        super().__init__()
        self.quiet_s = quiet_s
        self._clock = clock
        self._seen: dict[tuple[str, int, str], tuple[float, int]] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING or record.exc_info:
            return True
        key = (record.name, record.levelno, str(record.msg))
        now = self._clock()
        last = self._seen.get(key)
        if last is not None and now - last[0] < self.quiet_s:
            self._seen[key] = (last[0], last[1] + 1)
            return False
        held = last[1] if last else 0
        self._seen[key] = (now, 0)
        if held:
            record.msg = f"{record.msg} " + _("({n} more times in {m} min)").format(n=held, m=int(self.quiet_s // 60))
        return True


def setup(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format=FORMAT, datefmt=DATE)
    for handler in logging.getLogger().handlers:
        handler.addFilter(Once())
    # `kill -USR1 <pid>` writes where every thread of a stuck process is,
    # into its log. 25 October: the voice hung after a brain restart and
    # nothing could say where.
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, all_threads=True)


def say(text: str) -> None:
    """A line for the person reading the log, not a warning: what the
    process is doing, with when."""
    print(f"{time.strftime(DATE)} {text}", file=sys.stderr, flush=True)
