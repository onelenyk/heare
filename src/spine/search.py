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
import re
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Sequence
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

# A line that is itself a question about what was said is not an answer
# to one. Observed live: asked «що я казав про бекапи?» a second time, it
# quoted the *first* asking back — thirty minutes old, so past the
# freshness bound, and a perfect lexical match precisely because it was
# the same question. Recognised the same way the query is: strip the
# recall scaffolding and count what is left. «Дока, що я казав про
# бекапи?» keeps two words — the assistant's name and the subject being
# asked about. A line that said something keeps a sentence.
QUESTION_KEEPS_WORDS = 2

# Over-fetch before de-duplicating and dropping the too-short: a user
# line and the reply restating it are both good matches and often
# near-identical, so the top three rows are frequently fewer than three
# distinct things said.
_OVERFETCH = 8


def strip_names(text: str, names: Sequence[str]) -> str:
    """Drop the assistant's own name from a query.

    Its name is the one word guaranteed to be in the corpus: you have to
    say it to be heard, so it sits at the head of a large share of every
    line the person ever spoke. That is the definition of a stopword,
    and nobody declared it one — so «докер компоуз» searched the real
    database and came back with «Привіт, привіт докер!» from July, while
    the sentence about Docker Compose four days old never placed. The
    terms go into FTS joined by OR; one worthless term that matches
    hundreds of short rows beats the one that matters, because bm25
    rewards short documents.

    `names` is the wake table — the spellings Whisper actually produces
    («докер», «доко», «доку»), already enumerated in `wake_phrases.py`
    because the gate has the same problem from the other side.

    Exact words only, for the same reason the gate matches that way:
    «доку» is a listed variant and «документ» is not the assistant.
    """
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    if not wanted:
        return text
    kept = [w for w in re.split(r"(\W+)", text) if w.strip().lower() not in wanted]
    return "".join(kept).strip()


@dataclass(frozen=True)
class Fragment:
    """One thing that was said, and when."""

    ts: float
    text: str
    agent_spoken: bool


# How recently something must have been said to still be part of the
# conversation rather than something to recall. The turn asking the
# question is written down before the model is asked, so without this
# every search answers itself.
NOT_YET_A_MEMORY_S = 90.0

# How long after a line the assistant's answer to it still counts as the
# same exchange. Generous: a reply that needed a tool takes tens of
# seconds, and nothing else the assistant says in between is addressed
# to a different question.
CLEAN_REPLY_WITHIN_S = 120.0


def _clean_copy(persist: Any, ts: float, text: str) -> tuple[str, bool]:
    """The assistant's answer to this line, when it is the better copy.

    Returns the line and who said it — swapping the text without the
    attribution would have the assistant's own sentence read back as
    «ти казав», which is a lie told in the person's own ear.

    The module docstring says the reply is «frequently the only
    searchable copy of what the person said». Live on 24 August that
    turned out to be exactly backwards for the case it matters most in.
    Asked «що там було з докір компоузом», the search found the person's
    line and could not find the assistant's — because the assistant had
    written «Docker Compose», and Cyrillic «компоуз» and Latin «Compose»
    share no token. The clean copy is clean *by being in another script*,
    which is precisely what puts it out of reach of the question.

    So it is not searched for. It is fetched by asking what came next.

    Whether it is the better answer is decided by length, and that is a
    heuristic: a short reply is an acknowledgement («Зрозумів —
    шістнадцять гігабайт»), and nobody asks to be reminded of an
    acknowledgement, while the line that explains what was wrong with
    the build is always the longer of the two. Both live cases agree,
    which is two, not many.
    """
    fetch = getattr(persist, "reply_to", None)
    if fetch is None:
        return text, False
    answer = fetch(ts, CLEAN_REPLY_WITHIN_S)
    if answer is None:
        return text, False
    reply = (answer[1] or "").strip()
    if len(reply) < MIN_CHARS or len(reply) <= len(text):
        return text, False
    return reply, True


def find(
    persist: Any,
    query: str,
    *,
    now: datetime,
    when: str = "",
    room: bool = False,
    limit: int = MAX_FRAGMENTS,
    names: Sequence[str] = (),
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
    terms = _sanitize_fts_query(strip_names(strip_when(query, span), names))
    # The question is already on disk by the time this runs — the turn is
    # persisted before the model is even asked, so FTS finds it and
    # ranks it first, being the freshest and shortest match there is.
    # Observed live, twice, word for word: «що я казав про бекапи?» came
    # back answered with «Сьогодні ти казав: Дока, що я казав про
    # бекапи?». What you are saying now is not yet a memory.
    fresh = now.timestamp() - NOT_YET_A_MEMORY_S
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
        until=min(span.end, fresh) if span else fresh,
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
        if not agent_spoken and _is_a_question_about_memory(text):
            continue
        seen.add(key)
        said, spoken_by_it = text.strip(), bool(agent_spoken)
        if not spoken_by_it:
            said, spoken_by_it = _clean_copy(persist, float(ts), said)
        found.append(Fragment(float(ts), said, spoken_by_it))
        if len(found) >= limit:
            break
    return found


def _is_a_question_about_memory(text: str) -> bool:
    """Whether this line was someone asking rather than someone saying.

    The same stripping the query goes through, applied to the candidate:
    take out the time words and the stopwords and see what is left. «Дока,
    що я казав про бекапи?» leaves "бекапи" — one word, which is the
    subject of the question, not a thing anybody said about it. A line
    that actually said something keeps a sentence.

    Only user lines are tested. The assistant's replies quote back and
    restate, which is exactly what makes them the clean version of a
    mangled transcript — the property this verb was built on.
    """
    remains = _sanitize_fts_query(strip_when(text, None))
    if remains is None:
        return True
    # The sanitizer emits an FTS expression — quoted terms plus prefix
    # stems of each. Only the whole words count; the stems are the same
    # words again with their endings cut off.
    words = re.findall(r'"([^"]+)"(?!\*)', remains)
    return len(words) <= QUESTION_KEEPS_WORDS


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


__all__ = [
    "FRAGMENT_CHARS",
    "MAX_FRAGMENTS",
    "Fragment",
    "find",
    "shorten",
    "spoken",
    "strip_names",
]
