"""Ukrainian time expressions, in both directions.

Reading: «вчора», «у вівторок», «на тих вихідних», «три дні тому» become
a half-open range of local time, so a search can be narrowed to the day
someone actually means. Saying: a timestamp becomes the phrase a person
would use for it — «вчора», «у вівторок», «21 серпня» — because an
answer that quotes something back without saying when it was said is
worth very little.

Three properties, all deliberate:

* **Pure, and `now` is passed in.** Every rule about what «минулого
  тижня» means is then a table of cases in a test file, which is the
  only affordable way to get this right: the failures here are silent
  and off by one day, and nobody notices them from a log.

* **Local time, on purpose.** `transcripts.ts` is `time.time()`, and a
  person saying «вчора» means the day they lived through, not a UTC
  window. Naive `datetime.timestamp()` interprets in the machine's zone,
  which is exactly what is wanted — so the naivety here is the feature,
  not an oversight to be tidied up later.

* **`None` means "they did not say when".** Not "no results" and not
  "today". A search that guessed a range from a question with no time in
  it would answer «нічого не знайшла» about something said last month.

The phrase that matched is carried back on the `Span` so the caller can
cut it out of the query before searching. Left in, «вчора» is one more
word for full-text search to rank on, and it appears in no transcript at
all — a search term guaranteed to match nothing, dragging the real terms
down with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

# Numbers as they are spoken. Beyond ten, people say the digits or stop
# being precise («десь місяць тому»), so the list ends where speech does.
WORD_NUMBERS: dict[str, int] = {
    "один": 1,
    "одну": 1,
    "два": 2,
    "дві": 2,
    "три": 3,
    "чотири": 4,
    "п'ять": 5,
    "шість": 6,
    "сім": 7,
    "вісім": 8,
    "дев'ять": 9,
    "десять": 10,
}

# Weekday forms as they are actually said — «у вівторок», «в середу»,
# «у п'ятницю». Matched as whole alternatives rather than by stem: the
# stem of «середа» is «серед», which is also the preposition "among",
# and one preposition in a sentence would silently move the whole search
# to a Wednesday.
WEEKDAYS: dict[str, int] = {
    "понеділок": 0, "понеділка": 0, "понеділки": 0,
    "вівторок": 1, "вівторка": 1, "вівторки": 1,
    "середа": 2, "середу": 2, "середи": 2,
    "четвер": 3, "четверга": 3,
    "п'ятниця": 4, "п'ятницю": 4, "п'ятниці": 4,
    "субота": 5, "суботу": 5, "суботи": 5,
    "неділя": 6, "неділю": 6, "неділі": 6,
}

MONTHS: dict[str, int] = {
    "січень": 1, "січня": 1, "січні": 1,
    "лютий": 2, "лютого": 2, "лютому": 2,
    "березень": 3, "березня": 3, "березні": 3,
    "квітень": 4, "квітня": 4, "квітні": 4,
    "травень": 5, "травня": 5, "травні": 5,
    "червень": 6, "червня": 6, "червні": 6,
    "липень": 7, "липня": 7, "липні": 7,
    "серпень": 8, "серпня": 8, "серпні": 8,
    "вересень": 9, "вересня": 9, "вересні": 9,
    "жовтень": 10, "жовтня": 10, "жовтні": 10,
    "листопад": 11, "листопада": 11, "листопаді": 11,
    "грудень": 12, "грудня": 12, "грудні": 12,
}

# For saying a date out loud: «21 серпня», not «21 серпень».
MONTHS_GENITIVE = (
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
)

# For saying a weekday out loud: «у вівторок», «у середу».
WEEKDAYS_SPOKEN = (
    "у понеділок", "у вівторок", "у середу", "у четвер",
    "у п'ятницю", "у суботу", "у неділю",
)

# Whisper writes the typographic apostrophe, keyboards write the straight
# one, and some layouts write a backtick. Three spellings of «п'ять» that
# would each need their own table entry otherwise.
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'", "ʼ": "'"})


@dataclass(frozen=True)
class Span:
    """A half-open range of local time, and the words it came from."""

    start: float
    end: float
    phrase: str


def normalise(text: str) -> str:
    """Lowercase, one apostrophe, single spaces."""
    return re.sub(r"\s+", " ", (text or "").translate(_APOSTROPHES).lower()).strip()


def _span(first: date, last_exclusive: date, phrase: str) -> Span:
    """Days to epoch seconds, in the machine's own zone (see the module
    docstring on why that is deliberate)."""
    return Span(
        start=datetime.combine(first, time.min).timestamp(),
        end=datetime.combine(last_exclusive, time.min).timestamp(),
        phrase=phrase,
    )


def _day(d: date, phrase: str) -> Span:
    return _span(d, d + timedelta(days=1), phrase)


def _week_of(d: date, phrase: str) -> Span:
    monday = d - timedelta(days=d.weekday())
    return _span(monday, monday + timedelta(days=7), phrase)


def _month_of(year: int, month: int, phrase: str) -> Span:
    first = date(year, month, 1)
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return _span(first, nxt, phrase)


def _count(raw: str) -> int:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    return WORD_NUMBERS.get(raw, 1)


_NUM = r"(\d{1,2}|" + "|".join(sorted(WORD_NUMBERS, key=len, reverse=True)) + r")"
_WEEKDAY = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))

# Order matters: «позавчора» contains «вчора», and «минулого тижня» must
# be tried before the bare weekday rule so that «минулого вівторка» is
# not read as this week's Tuesday.
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bпозавчора\b"), "day_before_yesterday"),
    (re.compile(r"\b[ву]чора\b"), "yesterday"),
    (re.compile(r"\bсьогодні\b"), "today"),
    (re.compile(rf"\b{_NUM}\s+дн(?:і|ів|я|ей)?\s+тому\b"), "days_ago"),
    (re.compile(rf"\b{_NUM}\s+тижн(?:і|ів|я|ем)?\s+тому\b"), "weeks_ago"),
    (re.compile(rf"\b{_NUM}\s+місяц(?:і|ів|я|ем)?\s+тому\b"), "months_ago"),
    (re.compile(r"\bтиждень\s+тому\b"), "last_week"),
    (re.compile(r"\bмісяць\s+тому\b"), "last_month"),
    (
        re.compile(r"\b(?:на\s+)?(?:тих|минулих|той|минулі|минулого)\s+вихідн\w*"),
        "last_weekend",
    ),
    (re.compile(r"\b(?:на\s+)?(?:цих|ці|цьому)\s+вихідн\w*"), "this_weekend"),
    (re.compile(r"\bвихідн(?:их|і)\b"), "last_weekend"),
    (
        re.compile(r"\b(?:на\s+)?(?:мину\w+|тому|тім)\s+тижн\w*"),
        "last_week",
    ),
    (re.compile(r"\b(?:на\s+)?(?:цьому|цього|цім)\s+тижн\w*"), "this_week"),
    (re.compile(r"\bмину\w+\s+місяц\w*"), "last_month"),
    (re.compile(r"\b(?:цього|в\s+цьому|у\s+цьому)\s+місяц\w*"), "this_month"),
    (re.compile(r"\bмину\w+\s+рок\w*"), "last_year"),
    (re.compile(rf"\b(?:мину\w+\s+)?(?:[ву]\s+)?({_WEEKDAY})\b"), "weekday"),
    (re.compile(rf"\b(?:[ву]\s+)?({_MONTH})\b"), "month"),
]


def parse_when(text: str, *, now: datetime) -> Span | None:
    """The range of days a Ukrainian time expression names, or None.

    None is the answer for a question with no time in it, and it means
    "search everything" — never "search today".
    """
    haystack = normalise(text)
    if not haystack:
        return None
    today = now.date()

    for pattern, kind in _RULES:
        match = pattern.search(haystack)
        if match is None:
            continue
        phrase = match.group(0)

        if kind == "today":
            return _day(today, phrase)
        if kind == "yesterday":
            return _day(today - timedelta(days=1), phrase)
        if kind == "day_before_yesterday":
            return _day(today - timedelta(days=2), phrase)
        if kind == "days_ago":
            return _day(today - timedelta(days=_count(match.group(1))), phrase)
        if kind == "weeks_ago":
            weeks = _count(match.group(1))
            return _week_of(today - timedelta(weeks=weeks), phrase)
        if kind == "months_ago":
            months = _count(match.group(1))
            total = today.year * 12 + (today.month - 1) - months
            return _month_of(total // 12, total % 12 + 1, phrase)
        if kind == "last_week":
            return _week_of(today - timedelta(weeks=1), phrase)
        if kind == "this_week":
            return _week_of(today, phrase)
        if kind == "last_weekend":
            # The Saturday and Sunday immediately before this week. On a
            # Tuesday that is what everyone means; said on a Sunday it is
            # arguably the weekend before this one, and the two readings
            # cannot both be served — the common one wins.
            monday = today - timedelta(days=today.weekday())
            return _span(monday - timedelta(days=2), monday, phrase)
        if kind == "this_weekend":
            monday = today - timedelta(days=today.weekday())
            saturday = monday + timedelta(days=5)
            return _span(saturday, saturday + timedelta(days=2), phrase)
        if kind == "last_month":
            total = today.year * 12 + (today.month - 1) - 1
            return _month_of(total // 12, total % 12 + 1, phrase)
        if kind == "this_month":
            return _month_of(today.year, today.month, phrase)
        if kind == "last_year":
            return _span(date(today.year - 1, 1, 1), date(today.year, 1, 1), phrase)
        if kind == "weekday":
            wanted = WEEKDAYS[match.group(1)]
            back = (today.weekday() - wanted) % 7
            # «у вівторок» said on a Tuesday means today, not a week ago.
            return _day(today - timedelta(days=back), phrase)
        if kind == "month":
            month = MONTHS[match.group(1)]
            year = today.year if month <= today.month else today.year - 1
            return _month_of(year, month, phrase)
    return None


def strip_when(text: str, span: Span | None) -> str:
    """The query with the time expression taken out.

    Left in, «вчора» is a search term that appears in no transcript and
    matches nothing, while diluting the words that would have.
    """
    if span is None or not span.phrase:
        return text
    cut = re.sub(re.escape(span.phrase), " ", normalise(text))
    # Collapse again: the hole the phrase left has a space on each side,
    # and the double space would reach the FTS tokenizer as an empty term.
    return re.sub(r"\s+", " ", cut).strip()


def said_when(ts: float, *, now: datetime) -> str:
    """When something was said, in the words a person would use.

    Recent days get their own name because that is how they are spoken
    about; past a week, a name stops being a location and a date starts
    being one.
    """
    when = datetime.fromtimestamp(ts)
    days = (now.date() - when.date()).days
    if days == 0:
        return "сьогодні"
    if days == 1:
        return "вчора"
    if days == 2:
        return "позавчора"
    if 3 <= days <= 6:
        return WEEKDAYS_SPOKEN[when.weekday()]
    said = f"{when.day} {MONTHS_GENITIVE[when.month - 1]}"
    if when.year != now.year:
        said += f" {when.year} року"
    return said


__all__ = [
    "MONTHS",
    "MONTHS_GENITIVE",
    "Span",
    "WEEKDAYS",
    "WEEKDAYS_SPOKEN",
    "WORD_NUMBERS",
    "normalise",
    "parse_when",
    "said_when",
    "strip_when",
]
