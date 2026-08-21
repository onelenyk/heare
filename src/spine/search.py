"""«Що я казав про...» — search over the conversations it actually had.

`recall` searches `memories`: facts someone decided to store. This
searches `transcripts`: what was said, whether or not anyone thought it
worth keeping at the time. They are different questions and the second
one is the reason the microphone was ever left on.

Three decisions carry the quality of the answer.

**It searches the assistant's replies too.** A user line is whatever
Whisper made of the room — the wake table in `wake_phrases.py` exists
because «Дока» arrives as «докер», «доко», «доку». The reply to that
line is clean text that usually restates the same thing correctly, so
the assistant's own words are frequently the only searchable copy of
what the person said. Nothing here filters on `agent_spoken`; the answer
just says who it was.

**Addressed speech only, unless asked.** `source='overheard'` is the
room: kept for a week, never a turn, and written down only because
someone switched `hear_all` on. Folding it into an ordinary search would
answer a question about your own conversation with something a colleague
said near the desk, and there would be no way to tell from the answer.
Widening is a deliberate argument, never a fallback when the first
search finds nothing.

**A missing `source` is not the room.** Every row written before that
column existed reads NULL, and on this machine that is most of the
database — 3 592 of 4 412 rows the day this was written, including
every line the spine itself has ever logged. A filter written as
`source = 'voice'` would have passed its tests and returned almost
nothing against the real thing.

What the old rows cannot tell you
---------------------------------
2 853 rows carry `mode='ambient'`: everything the deleted engine's
microphone heard, addressed and overheard in one stream. Reading them
side by side, «Здарова, докера!» (answered) sits four seconds from
«Таня там продавила Дашу» (a video call nobody was talking to it
through), with nothing in the row to tell them apart — the engine that
knew the difference was deleted on 17 August, and `source='overheard'`
only starts on the 21st. They are searched by default anyway: excluding
a whole channel on a guess would throw away three months of the person's
own words to avoid quoting a colleague. From here on the distinction is
recorded, and this paragraph stops applying to anything new.

Summaries are deliberately not in the index
-------------------------------------------
`conversations.summary` is clean, short, and about exactly the right
thing, which makes putting it in here tempting. It stays out because of
what the answer is for: this verb quotes something back and says when it
was said. A summary is the model's paraphrase of an hour, and returning
it in the same list would quote a paraphrase as though the person had
said it — the one failure `summary.py` refuses to risk when it declines
to summarise a short conversation. Summaries earn their keep as the
input to step 4 (repeated intentions), where nothing is quoted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# The stopword list and the OR-with-prefix-stems trick belong to memory
# search and are hand-maintained (see the comment on `_STOPWORDS`). A
# second copy here would be a second thing to remember to extend, and
# the one that stopped being extended would quietly return nothing for
# ordinary spoken questions — the exact failure that list was built to
# stop.
from src.memory.sqlite_backend import _sanitize_fts_query
from src.spine.when import parse_when, said_when, strip_when

logger = logging.getLogger("spine.search")

# Three, because this is spoken. A fourth fragment is not more recall,
# it is a paragraph read aloud at someone who asked a question.
MAX_FRAGMENTS = 3

# How much of a fragment is said. Long enough to carry the thing that
# was decided, short enough that it is still an answer and not a
# recital.
FRAGMENT_CHARS = 180

# Shorter than this, a fragment is not a memory. bm25 rewards short
# documents, and the shortest lines in a voice transcript are the wake
# word and «Дякую.» — searching the real database for «докер» answered
# «12 серпня ти казав: Докер.», beating the sentence that came four
# seconds later and actually said something.
MIN_CHARS = 12

# Over-fetch before de-duplicating and dropping the too-short: a user
# line and the reply restating it are both good matches and often
# near-identical, so the top three rows are frequently fewer than three
# distinct things said.
_OVERFETCH = 8


@dataclass(frozen=True)
class Fragment:
    """One thing that was said, and when."""

    ts: float
    text: str
    agent_spoken: bool


def find(
    persist: Any,
    query: str,
    *,
    now: datetime,
    when: str = "",
    room: bool = False,
    limit: int = MAX_FRAGMENTS,
) -> list[Fragment]:
    """Fragments matching `query`, newest-relevant first.

    `persist` is injected rather than imported: the whole path is then
    playable against a fake in a test, and this module knows nothing
    about SQLite.

    `when` is the person's own words about time, when they said any
    («вчора», «минулого тижня»). It falls back to the query itself,
    because a model rephrasing a question into `query` keeps the time
    word in it far more often than it fills in a second argument.
    """
    span = parse_when(when or query, now=now)
    terms = _sanitize_fts_query(strip_when(query, span))
    if terms is None:
        # Nothing but stopwords survived — «що я казав учора» is entirely
        # scaffolding once the time expression is removed. With a range
        # that is still a real question, answered by the rows themselves;
        # without one it is not a question at all.
        if span is None:
            return []
        logger.info("search: no terms left, falling back to the range alone")

    rows = persist.search_transcripts(
        terms,
        since=span.start if span else None,
        until=span.end if span else None,
        include_room=room,
        limit=limit + _OVERFETCH,
        now=now.timestamp(),
    )

    found: list[Fragment] = []
    seen: set[str] = set()
    for ts, text, agent_spoken in rows:
        key = " ".join((text or "").lower().split())
        if len(key) < MIN_CHARS or key in seen:
            continue
        seen.add(key)
        found.append(Fragment(float(ts), text.strip(), bool(agent_spoken)))
        if len(found) >= limit:
            break
    return found


def shorten(text: str, limit: int = FRAGMENT_CHARS) -> str:
    """Cut to a word boundary, so a quote never ends mid-syllable."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return f"{cut}…"


def spoken(fragments: list[Fragment], *, now: datetime) -> str:
    """The answer, as one thing a voice can say.

    `SpineLoop._run_tool` speaks what a verb returns verbatim — the
    string is not handed back to the model to phrase. So the sentence
    has to be finished here, and it has to name when: quoting something
    back without saying when it was said is the difference between a
    memory and a coincidence.
    """
    if not fragments:
        return "Не пригадую, щоб ми про це говорили."

    first, *rest = fragments
    when = said_when(first.ts, now=now)
    lead = when[0].upper() + when[1:]
    verb = "прозвучало" if first.agent_spoken else "ти казав"
    answer = f"{lead} {verb}: {shorten(first.text)}"
    if not answer.endswith((".", "!", "?", "…")):
        answer += "."

    # The other two are named by their day only. Said in full they stop
    # being an answer and start being a transcript read out loud.
    #
    # Newest first, not best-match first: the ranking that chose them is
    # invisible to the listener, so «12 серпня… ще 10 серпня і 13 серпня»
    # sounds like the dates came out shuffled.
    others: list[str] = []
    for fragment in sorted(rest, key=lambda f: f.ts, reverse=True):
        said = said_when(fragment.ts, now=now)
        if said != when and said not in others:
            others.append(said)
    if others:
        answer += f" Ще про це було {' і '.join(others)}."
    return answer


__all__ = ["FRAGMENT_CHARS", "MAX_FRAGMENTS", "Fragment", "find", "shorten", "spoken"]
