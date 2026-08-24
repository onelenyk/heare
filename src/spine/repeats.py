"""When you keep saying you mean to, and never quite do.

This is the one thing this project has already thrown away once. There
was a proactive ticker: every N minutes it woke up and decided whether to
speak, and `heartbeats` — the table it wrote to, empty from birth — is
its gravestone. It was unbearable for a reason that has nothing to do
with how well it chose its words: **the trigger was a clock**. An
assistant that speaks on a schedule is an alarm with opinions.

So the trigger here is not time passing. It is a conversation ending,
which is the only moment there is new material *and* nobody is talking.
And what it looks for is not a good moment to say something — it is the
one thing a machine that has heard everything can know and a person
cannot: that you have said "треба" about the same thing three times this
week.

Where the model is allowed
--------------------------
One question: which intentions repeat across these summaries. Everything
else is a condition. The model will find intentions in an empty room if
you let it — asked "what repeats", it will always produce a list — so
what restrains it is not a careful prompt but arithmetic it does not
participate in:

* three mentions, not two;
* on at least two different days, so one talkative afternoon is not a
  pattern;
* phrased about oneself, in the future — «треба», «хочу», «маю». What a
  colleague said is recorded in a summary as faithfully as what you
  said, and «колезі треба оновити сертифікат» clears every count there
  is.

Every one of those rejects far more than it admits, and that is the
intended ratio. Silence is the correct answer almost every time, and the
cost of the two errors is nothing like symmetric: a missed remark is
never noticed, and a wrong one is the reason the feature was deleted.

Delivery is not here
--------------------
Nothing in this file speaks. It hands the engine one sentence, and the
engine's existing machinery does the rest: `judge` for not-into-silence
and not-at-night and not-mid-sentence, `dedupe_key` for said-once, `ask`
for the model's veto, `trust` for being brushed off. That is the whole
reason this step is a hundred lines and not a subsystem — the delivery
was built from the other side, for the watcher, and an observation that
becomes an intent inherits all of it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

logger = logging.getLogger("spine.repeats")

# The table. Additive and spine-only, like `role_sessions` and `intents`
# before it: CREATE TABLE IF NOT EXISTS is invisible to every reader that
# does not ask for it, so it needs no migration and does not touch the
# schema version the daemon shares with the older store. A column added
# later goes in `init` behind a PRAGMA check, the way
# `intents.expires_ts` did.
SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    said_ts REAL,
    dismissed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts DESC);
"""

# The thresholds. All three, together, are the feature.
MIN_MENTIONS = 3  # twice is a coincidence
MIN_DAYS = 2  # one talkative afternoon is not a pattern

# How far back a pass looks. Long enough to hold a week's worth of
# meaning to do something, short enough that what you sorted out on
# Monday is not still being counted on Friday.
LOOK_BACK_S = 5 * 24 * 3600.0

# Below this there is not enough material for three mentions across two
# days to be possible, and asking anyway spends a model call to be told
# no.
ENOUGH_SUMMARIES = 3

# At most one a day, from the first day, because this is the feature that
# was once removed for being unbearable. It is a ceiling on *noticing*,
# not on speaking: the engine may still decide never to say it.
ONE_A_DAY_S = 24 * 3600.0

# How much two sentences have to have in common to be the same
# intention. Crude on purpose — the same crude measure `reaction_to`
# uses — because the alternative is saying the same thing twice in
# different words, which is exactly what makes an assistant tiresome.
SAME_ENOUGH = 0.6

# Said of oneself, about something not yet done. The plan names three
# words; these are those three and the nearest forms of them.
WANTS = frozenset(
    {
        "треба",
        "потрібно",
        "хочу",
        "хочеться",
        "маю",
        "мушу",
        "планую",
        "збираюсь",
        "збираюся",
    }
)

# The same wanting, with someone else doing it. Only the forms that would
# otherwise *pass* the check above are listed: «користувач хоче» already
# fails it (that is «хоче», not «хочу»), but «колезі треба» does not, and
# it is not your intention.
NOT_MINE = frozenset(
    {"йому", "їй", "їм", "хоче", "хочуть", "мусить", "планує", "планують",
     "збирається", "збираються", "потребує"}
)
NOT_MINE_STEMS = ("колег", "колез", "клієнт", "замовник", "начальник",
                  "керівник", "дружин", "сусід")

# What the dedupe key looks like. The engine never parses it — it takes
# it from `look` and hands it back on the way out — so the format lives
# in exactly one file.
KEY_PREFIX = "repeat:"

# A negative instruction is not enough to get silence out of a model
# asked to find patterns, so the prompt says outright that nothing is the
# usual answer, and the format leaves it somewhere to put that.
PROMPT = (
    "Ось підсумки розмов однієї людини з голосовим асистентом за кілька "
    "днів. Кожен рядок починається з номера розмови.\n\n"
    "{body}\n\n"
    "Які наміри ця людина називає повторно — те, що вона сама про себе "
    "казала, що треба, хоче або має зробити, і назвала не в одній "
    "розмові?\n\n"
    "Кожен такий намір — окремим рядком, рівно в такому вигляді:\n"
    "намір її ж словами | номери розмов через кому\n"
    "Приклад: треба переписати збірку | 12, 15, 19\n\n"
    "Пиши від першої особи, так, як казала вона. Чужі наміри, переказані "
    "в розмові, не рахуються.\n"
    "Якщо нічого не повторюється — відповідай рівно: НІЧОГО\n"
    "Це нормальна і найчастіша відповідь."
)

NOTHING = "НІЧОГО"


@dataclass(frozen=True)
class Summary:
    """One closed conversation, as the pass sees it."""

    id: int
    ts: float
    text: str

    @property
    def day(self) -> date:
        return datetime.fromtimestamp(self.ts).date()


@dataclass(frozen=True)
class Candidate:
    """Something the model claims repeats. Not yet believed."""

    text: str
    evidence_ids: tuple[int, ...]


@dataclass(frozen=True)
class Observation:
    id: int
    ts: float
    text: str
    evidence_ids: tuple[int, ...]
    said_ts: float | None
    dismissed: bool


# ── the question, and reading the answer ──────────────────────────────


def render(summaries: list[Summary]) -> str:
    """The summaries as the model sees them: numbered, dated, in order."""
    return "\n".join(
        f"{s.id} ({datetime.fromtimestamp(s.ts).strftime('%d.%m')}): "
        f"{s.text.strip()}"
        for s in summaries
        if s.text and s.text.strip()
    )


def parse(answer: str) -> list[Candidate]:
    """Read the model's lines. Anything unparseable is nothing.

    Deliberately unforgiving about the shape and forgiving about the
    decoration: a line with no evidence is not a claim about repetition,
    it is a guess, and a guess is what this whole file exists to refuse.
    """
    found: list[Candidate] = []
    for raw in (answer or "").splitlines():
        line = raw.strip().lstrip("-–—•*").strip()
        if not line or line.upper().startswith(NOTHING):
            continue
        if "|" not in line:
            continue
        text, _, evidence = line.partition("|")
        ids = tuple(dict.fromkeys(int(n) for n in re.findall(r"\d+", evidence)))
        text = text.strip()
        if text and ids:
            found.append(Candidate(text=text, evidence_ids=ids))
    return found


# ── the thresholds ────────────────────────────────────────────────────


def _words(text: str) -> list[str]:
    return [w.strip(".,!?;:()[]«»\"'—–…").lower() for w in (text or "").split()]


def is_mine(text: str) -> bool:
    """Said about oneself, about something not done yet.

    Both halves matter. Without the first, "the build is broken" counts
    as an intention; without the second, what a colleague announced in a
    meeting you described counts as yours.
    """
    words = set(_words(text))
    if not words & WANTS:
        return False
    if words & NOT_MINE:
        return False
    return not any(w.startswith(NOT_MINE_STEMS) for w in words)


def believable(candidate: Candidate, summaries: list[Summary]) -> bool:
    """Whether the arithmetic holds. The model does not take part in this.

    Evidence it invented is the failure mode to expect: asked which
    conversations something appeared in, a model that has decided on an
    intention will supply numbers to go with it. Only ids that were
    actually in the prompt count, which turns a fabricated pattern into a
    short list and a short list into silence.
    """
    if not is_mine(candidate.text):
        return False
    known = {s.id: s for s in summaries}
    cited = [known[i] for i in set(candidate.evidence_ids) if i in known]
    if len(cited) < MIN_MENTIONS:
        return False
    return len({s.day for s in cited}) >= MIN_DAYS


# How to say "the third time in three days" out loud. Only the counts
# that can actually occur are spelled — the threshold is three mentions
# over two days, and past a handful the number stops being the point.
_TIMES = {3: "втретє", 4: "вчетверте", 5: "вп'яте", 6: "вшосте"}
_DAYS = {2: "два дні", 3: "три дні", 4: "чотири дні", 5: "п'ять днів"}


def phrase(candidate: Candidate, summaries: list[Summary]) -> str:
    """The remark, with the reason it is one.

    `candidate.text` is the person's own words for the intention, and on
    its own it is not worth saying: read out verbatim it is your own
    sentence handed back to you. What makes it worth a word is the
    arithmetic around it — three times, two days — and until 24 August
    that arithmetic went into a log line and nowhere else.

    Live that day: the pass found «треба переписати збірку», three
    mentions across three days, correctly ignoring a colleague's
    intention in a fourth. The engine raised it, and the model's veto —
    which is shown the intent text and the surroundings, and nothing
    else — was asked whether «треба переписати збірку» was worth saying.
    It said no, and it was right to: nothing in front of it said this
    had ever happened before. The whole restraint of the feature is the
    counting, and the counting did not reach the one place where the
    decision is made.

    Quoted rather than reported, because the intention is in the first
    person — «хочу перейти на інший тариф» cannot be put after «ти
    кажеш, що» without changing who wants it.
    """
    known = {s.id: s for s in summaries}
    cited = [known[i] for i in set(candidate.evidence_ids) if i in known]
    times = _TIMES.get(len(cited), f"{len(cited)} разів")
    days = len({s.day for s in cited})
    return f"{times.capitalize()} за {_DAYS.get(days, f'{days} днів')} " \
           f"чую від тебе: {candidate.text.strip()}"


def sift(candidates: list[Candidate], summaries: list[Summary]) -> list[Candidate]:
    return [c for c in candidates if believable(c, summaries)]


def _content(text: str) -> set[str]:
    """The words that carry the subject, with the wanting taken out.

    «треба полагодити збірку» and «треба полагодити тести» share two
    words out of three and are not the same intention; with the wanting
    removed they share one out of two, and are not.
    """
    return {w for w in _words(text) if len(w) > 3 and w not in WANTS}


def same_thing(a: str, b: str) -> bool:
    first, second = _content(a), _content(b)
    if not first or not second:
        return False
    return len(first & second) / min(len(first), len(second)) >= SAME_ENOUGH


# ── where they are kept ───────────────────────────────────────────────


class ObservationStore:
    """Persistence for what it noticed. Never raises into the caller.

    Every failure here resolves to silence, which is the safe direction
    for this feature specifically: a store that cannot answer means a
    remark not made, and a remark not made is never noticed by anyone.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def init(self) -> None:
        try:
            await self._db.executescript(SCHEMA)
            await self._db.commit()
        except Exception:
            logger.exception("observations: init failed (non-fatal)")

    async def too_soon(self, now: float | None = None) -> bool:
        """Whether anything was noticed within the last day.

        The first guard on the path and the cheapest, so on a day that
        already has its one observation the model is never asked at all.
        A database that cannot answer says yes: the ceiling is the whole
        reason this is tolerable, and it must not be lifted by a fault.
        """
        now = now if now is not None else time.time()
        try:
            cursor = await self._db.execute("SELECT MAX(ts) FROM observations")
            row = await cursor.fetchone()
        except Exception:
            logger.exception("observations: too_soon failed (staying quiet)")
            return True
        last = (row or [None])[0]
        return last is not None and now - float(last) < ONE_A_DAY_S

    async def record(
        self,
        text: str,
        evidence_ids: tuple[int, ...],
        *,
        now: float | None = None,
    ) -> int | None:
        """Write it down, unless it has been noticed before.

        "Said once, never said again" has to hold across wordings, not
        across rows: two passes a week apart over the same repeated
        intention will not phrase it identically, and a unique index
        would let both through. Being waved away is permanent for the
        same reason — a dismissed observation stays in the table
        precisely so that it keeps blocking.
        """
        now = now if now is not None else time.time()
        try:
            cursor = await self._db.execute(
                "SELECT text FROM observations ORDER BY ts DESC LIMIT 200"
            )
            for (earlier,) in await cursor.fetchall():
                if same_thing(text, earlier):
                    logger.info("observations: already noticed — %.60s", text)
                    return None
            cursor = await self._db.execute(
                "INSERT INTO observations (ts, text, evidence_ids, said_ts, "
                "dismissed) VALUES (?, ?, ?, NULL, 0)",
                (now, text[:500], json.dumps(list(evidence_ids))),
            )
            await self._db.commit()
            return cursor.lastrowid or None
        except Exception:
            logger.exception("observations: record failed (non-fatal)")
            return None

    async def mark_said(self, observation_id: int, now: float | None = None) -> None:
        """It actually left the assistant's mouth.

        Not the same as having been noticed: most of what gets this far
        is still refused by the model's veto. Keeping the two apart is
        the only way to tell "it never repeated itself" from "it
        repeated itself and you did not care" after a week of living
        with this.
        """
        try:
            await self._db.execute(
                "UPDATE observations SET said_ts = ? WHERE id = ? AND said_ts IS NULL",
                (now if now is not None else time.time(), observation_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception("observations: mark_said failed (non-fatal)")

    async def dismiss(self, observation_id: int) -> None:
        """Waved away, and that is permanent."""
        try:
            await self._db.execute(
                "UPDATE observations SET dismissed = 1 WHERE id = ?",
                (observation_id,),
            )
            await self._db.commit()
        except Exception:
            logger.exception("observations: dismiss failed (non-fatal)")

    async def recent(self, limit: int = 10) -> list[Observation]:
        try:
            cursor = await self._db.execute(
                "SELECT id, ts, text, evidence_ids, said_ts, dismissed "
                "FROM observations ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
            return [_row_to_observation(r) for r in await cursor.fetchall()]
        except Exception:
            logger.exception("observations: recent failed (non-fatal)")
            return []


def _row_to_observation(row: Any) -> Observation:
    try:
        ids = tuple(int(i) for i in json.loads(row[3] or "[]"))
    except (TypeError, ValueError):
        ids = ()
    return Observation(
        id=int(row[0]),
        ts=float(row[1]),
        text=str(row[2]),
        evidence_ids=ids,
        said_ts=row[4],
        dismissed=bool(row[5]),
    )


# ── the pass ──────────────────────────────────────────────────────────


def detector(cfg_of: Any, stream: Any) -> Any:
    """Build the one model call. Collaborators arrive as arguments, so
    the whole pass plays out in a test with a fake model and this module
    imports nothing of the spine's."""

    async def detect(summaries: list[Summary]) -> list[Candidate]:
        prompt = PROMPT.format(body=render(summaries))
        parts: list[str] = []
        async for chunk in stream(
            [{"role": "user", "content": prompt}], cfg_of(), temperature=0.2
        ):
            parts.append(chunk)
        return parse("".join(parts))

    return detect


class Repeats:
    """One more source of `engine.notice()`, and nothing else.

    It owns its window, its store and its question; the engine owns
    whether, when and how any of it is ever said out loud.
    """

    def __init__(
        self,
        *,
        store: ObservationStore,
        summaries: Any,  # async (since_ts) -> [(id, ts, summary)]
        detect: Any,  # async ([Summary]) -> [Candidate]
        look_back_s: float = LOOK_BACK_S,
    ) -> None:
        self._store = store
        self._summaries = summaries
        self._detect = detect
        self._look_back_s = look_back_s

    async def look(self, *, now: float | None = None) -> tuple[str, str] | None:
        """A conversation just ended. Is anything being repeated?

        Returns a dedupe key and a sentence, or nothing — and nothing is
        the expected answer. The guards run cheapest first, so most calls
        cost one indexed query and no model at all.
        """
        now = now if now is not None else time.time()
        if await self._store.too_soon(now):
            return None
        rows = await self._summaries(now - self._look_back_s)
        summaries = [Summary(int(i), float(ts), str(text)) for i, ts, text in rows]
        if len(summaries) < ENOUGH_SUMMARIES:
            return None

        for candidate in sift(await self._detect(summaries), summaries):
            observation_id = await self._store.record(
                candidate.text, candidate.evidence_ids, now=now
            )
            if observation_id is None:
                continue
            logger.info(
                "repeats: %d разів у %d днях — %.60s",
                len(set(candidate.evidence_ids)),
                len({s.day for s in summaries if s.id in candidate.evidence_ids}),
                candidate.text,
            )
            # One a day means one, not one that clears every filter.
            return key(observation_id), phrase(candidate, summaries)
        return None

    async def mark_said(self, dedupe_key: str | None) -> None:
        observation_id = _id_of(dedupe_key)
        if observation_id is not None:
            await self._store.mark_said(observation_id)

    async def dismiss(self, dedupe_key: str | None) -> None:
        """The engine offers every rejected intent; only its own match."""
        observation_id = _id_of(dedupe_key)
        if observation_id is not None:
            await self._store.dismiss(observation_id)


def key(observation_id: int) -> str:
    return f"{KEY_PREFIX}{observation_id}"


def _id_of(dedupe_key: str | None) -> int | None:
    if not dedupe_key or not dedupe_key.startswith(KEY_PREFIX):
        return None
    try:
        return int(dedupe_key[len(KEY_PREFIX):])
    except ValueError:
        return None


__all__ = [
    "Candidate",
    "ENOUGH_SUMMARIES",
    "MIN_DAYS",
    "MIN_MENTIONS",
    "Observation",
    "ObservationStore",
    "PROMPT",
    "Repeats",
    "Summary",
    "believable",
    "detector",
    "is_mine",
    "phrase",
    "key",
    "parse",
    "render",
    "same_thing",
    "sift",
]
