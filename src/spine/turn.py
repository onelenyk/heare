"""One clock decides when a user turn has finished.

A turn ends because the words stopped coming, not because time passed
in some unrelated timer. `TurnAssembler` owns that single deadline:
every fragment (and every speech-start) pushes it back, and the turn
closes only once `hold_s` has elapsed with no speech in progress and
no new fragment. See docs/findings/two-clocks.md for why a second,
independent clock (a silence-based turn-stop strategy racing a
transcript debounce) produces double answers.

No pipecat, no SDK — just stdlib.
"""

from __future__ import annotations

import re
import time
from typing import Callable

# Words a thought hangs on: conjunctions, prepositions and the fillers
# people say while the next clause arrives. A fragment ending here is
# not an utterance, it is the first half of one.
_CONTINUATION_WORDS: frozenset[str] = frozenset(
    {
        # Only words a Ukrainian sentence cannot end on. Pronouns and
        # adverbs are deliberately absent: «Зроби це.» and «Прийду
        # потім.» are whole sentences.
        "і", "й", "та", "але", "бо", "що", "щоб", "якщо", "тому",
        "тобто", "або", "чи", "ну", "типу", "значить", "наприклад",
        "для", "від", "до", "з", "із", "зі", "на", "в", "у", "під",
        "над", "при", "без", "про", "по", "за", "як", "коли", "хоча",
        "and", "but", "because", "that", "if", "when", "for", "with",
    }
)

_TERMINAL = ".!?…"
_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)


def sounds_unfinished(text: str) -> bool:
    """True when the words themselves say the thought is still coming.

    Whisper punctuates: a fragment that ends without a terminal mark, or
    trails off in an ellipsis, or leans on a conjunction, is a pause for
    breath — not a finished turn.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.endswith("…") or stripped.endswith("..."):
        return True
    words = _WORD_RE.findall(stripped.lower())
    if words and words[-1] in _CONTINUATION_WORDS:
        # Even punctuated: «...і.» is Whisper marking a pause, not an end.
        return True
    # Whisper punctuates finished speech; a fragment without a closing
    # mark is one that was cut off by a breath.
    return stripped[-1] not in _TERMINAL


class TurnAssembler:
    """Joins transcript fragments into one user turn.

    One clock: after a fragment arrives, the turn stays open for `hold_s`.
    New speech (speech_started) or another fragment while open keeps it
    open — the deadline restarts when the next fragment lands. The turn
    closes only when `hold_s` passes with no speech in progress and no new
    fragment; poll() then returns the fragments joined with spaces.
    `clock` is injectable for tests (defaults to time.monotonic).
    """

    def __init__(
        self,
        hold_s: float = 1.3,
        clock: Callable[[], float] = time.monotonic,
        continuation_hold_s: float | None = None,
    ) -> None:
        self._hold_s = hold_s
        # A pause in the middle of a thought sounds different from a pause
        # at the end of one, and the words say which it is: a fragment
        # left hanging on «і», «тому що», «типу» — or with no closing
        # punctuation at all — is a person still thinking, and gets the
        # longer wait. Otherwise "коли я говорю і ще не завершив думку"
        # is answered mid-sentence.
        self._continuation_hold_s = (
            continuation_hold_s if continuation_hold_s is not None
            else hold_s * 2.0
        )
        self._clock = clock
        self._fragments: list[str] = []
        self._deadline: float | None = None
        self._speaking = False
        # Timestamp of the most recent speech_started() call that has not
        # yet been followed by a transcript() — used only to detect a dead
        # STT (defensive close after 10*hold_s of silence from it).
        self._speech_started_at: float | None = None

    def speech_started(self) -> None:
        """VAD START: user is speaking.

        While speaking, an open turn must not close, however far past its
        deadline. If no turn is open yet, this is remembered so the turn
        that the next transcript() opens behaves normally (i.e. is not
        immediately eligible to close).
        """
        self._speaking = True
        self._speech_started_at = self._clock()
        if self._fragments:
            # Keep an already-open turn alive; the real deadline reset
            # happens when the fragment for this speech lands.
            self._deadline = self._clock() + self._hold_s

    def transcript(self, text: str, *, speech_resumed: bool = False) -> None:
        """STT produced a fragment.

        Also marks speech as no longer in progress — an utterance always
        ends with a transcript (or with nothing, if it transcribed to
        silence), and this is the only signal available for that. Except
        when the caller saw a newer speech start after this fragment's
        utterance already ended (`speech_resumed=True`): then the flag
        belongs to that newer speech and a slow STT round-trip must not
        clear it — that would close the turn mid-sentence and mute the
        microphone on the rest of what the user is saying.
        """
        if not speech_resumed:
            self._speaking = False
            self._speech_started_at = None
        stripped = text.strip()
        if not stripped:
            # A cough, a doorbell, dead air — clears speaking above but
            # must not open or extend a turn.
            return
        self._fragments.append(stripped)
        hold = (
            self._continuation_hold_s
            if sounds_unfinished(stripped)
            else self._hold_s
        )
        self._deadline = self._clock() + hold

    def poll(self) -> str | None:
        """Return the joined turn text once it has closed, else None.

        Closes when: speech is not in progress, a deadline is set, and
        the clock has reached it. Also defends against a dead STT: if
        speech_started() fired but no transcript() followed for
        10*hold_s, the turn is force-closed with whatever fragments it
        has (or simply reset to the empty/closed state if it has none),
        so `open` cannot get stuck True forever.
        """
        now = self._clock()

        if (
            self._speech_started_at is not None
            and now - self._speech_started_at >= 10 * self._hold_s
        ):
            # STT died mid-utterance: stop waiting on it. Fragments are
            # only ever appended together with a deadline (see
            # transcript()), so if there are fragments here their
            # deadline is already <= now (it was set no later than
            # speech_started_at + hold_s); nothing left to fix up but the
            # speaking flag that was keeping the turn open.
            self._speaking = False
            self._speech_started_at = None

        if self._speaking:
            return None
        if self._deadline is None:
            return None
        if now < self._deadline:
            return None

        if not self._fragments:
            self.reset()
            return None

        text = " ".join(self._fragments)
        self.reset()
        return text

    @property
    def open(self) -> bool:
        """A turn is currently accumulating (fragments held, not yet closed)."""
        return bool(self._fragments) or self._speaking

    def reset(self) -> None:
        """Drop all held state; equivalent to a fresh assembler."""
        self._fragments = []
        self._deadline = None
        self._speaking = False
        self._speech_started_at = None
