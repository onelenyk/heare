"""Control tokens a model may write into its own text.

Measured 16 November 2026: the brain told the model to call ``delegate`` while
no hands were connected, so ``delegate`` was not in the tool list — and the
model wrote the call into the reply as text:

    <|DSLM|> calls <|DSLM|> invoke name="delegate" <|DSLM|> parameter …

The person saw that in the chat. A tool call belongs to the wire, never to the
words: whatever a model sends on the content channel is shown and spoken, so
control tokens are removed from it here, once, for every provider and every
part. Text that merely looks like a token (``<|`` with no closer within a short
run) is left alone, and a token split across two streamed pieces is still
removed because the opening piece is held until it can be decided.

Only the standard library: the core imports nothing else.
"""

from __future__ import annotations

_OPENERS = ("<|", "<｜")  # the full-width bars are what some models write
_CLOSERS = ("|>", "｜>")
_LONGEST = 64  # a control token is short; a longer run is text that starts like one


def _first(data: str, needles: tuple[str, ...], start: int) -> tuple[int, int]:
    """Where the earliest of *needles* begins at or after *start*, and its size."""
    best, size = -1, 0
    for needle in needles:
        at = data.find(needle, start)
        if at >= 0 and (best < 0 or at < best):
            best, size = at, len(needle)
    return best, size


class Cleaner:
    """Removes ``<|…|>`` runs, holding a partial one for the next piece.

    One instance per stream: ``feed`` as the pieces arrive, ``flush`` when the
    stream ends. The held piece is at most one unclosed opener.
    """

    def __init__(self) -> None:
        self._held = ""

    def feed(self, text: str) -> str:
        """What of *text* is safe to show now; the rest waits for the next piece."""
        data, self._held = self._held + text, ""
        out: list[str] = []
        at = 0
        while True:
            start, size = _first(data, _OPENERS, at)
            if start < 0:
                out.append(data[at:])
                break
            out.append(data[at:start])
            end, close = _first(data, _CLOSERS, start + size)
            if end < 0:
                rest = data[start:]
                if len(rest) <= _LONGEST:
                    self._held = rest
                else:
                    out.append(rest)
                break
            at = end + close
        return "".join(out)

    def flush(self) -> str:
        """Whatever was held back: it never became a token."""
        rest, self._held = self._held, ""
        return rest


def clean(text: str) -> str:
    """One whole string with its control tokens removed."""
    cleaner = Cleaner()
    return cleaner.feed(text) + cleaner.flush()
