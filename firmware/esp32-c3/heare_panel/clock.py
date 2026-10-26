"""The panel's only clock: MicroPython's 30-bit millisecond counter.

There is no wall clock on the panel and no NTP. Everything the panel times —
a reconnect backoff, a permission deadline, the ALLOW blink — is a duration
measured from a ``ticks_ms`` reading, so the only question this module answers
is "how many milliseconds passed between these two readings?".

``time.ticks_ms`` wraps at ``1 << 30``. ``elapsed`` therefore compares as a
signed distance inside that ring: near the wrap a later reading is smaller
than an earlier one, and a plain subtraction would report a huge negative
number. The formula is the one MicroPython's own ``ticks_diff`` uses.

Tests inject a fake with a ``ticks_ms`` method, so no test waits for time to
pass:
``clock.now_ms(fake)``.
"""

try:
    import time
except ImportError:
    time = None

# MicroPython's ``time.ticks_ms()`` counts in a 30-bit ring.
TICKS_PERIOD = 1 << 30
_HALF_PERIOD = TICKS_PERIOD // 2


def now_ms(source=None):
    """The current millisecond tick from ``source`` (``time`` by default)."""
    return (source or time).ticks_ms()


def elapsed(start, now):
    """Milliseconds from ``start`` to ``now``, correct across the 30-bit wrap.

    The result is in ``[-2**29, 2**29)``; a reading "in the future" by less
    than half a period is a negative duration, which is what a caller wants
    when it subtracts the wrong way round.
    """
    return (now - start + _HALF_PERIOD) % TICKS_PERIOD - _HALF_PERIOD
