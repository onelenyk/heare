"""Time expressions in the person's language, in both directions.

Ukrainian first, English beside it; the tables are chosen by the process
language (`heare.core.i18n`), so the same rules keep working for either.

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

from heare.core.i18n import language as _language

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
    "понеділок": 0,
    "понеділка": 0,
    "понеділки": 0,
    "вівторок": 1,
    "вівторка": 1,
    "вівторки": 1,
    "середа": 2,
    "середу": 2,
    "середи": 2,
    "четвер": 3,
    "четверга": 3,
    "п'ятниця": 4,
    "п'ятницю": 4,
    "п'ятниці": 4,
    "субота": 5,
    "суботу": 5,
    "суботи": 5,
    "неділя": 6,
    "неділю": 6,
    "неділі": 6,
}

MONTHS: dict[str, int] = {
    "січень": 1,
    "січня": 1,
    "січні": 1,
    "лютий": 2,
    "лютого": 2,
    "лютому": 2,
    "березень": 3,
    "березня": 3,
    "березні": 3,
    "квітень": 4,
    "квітня": 4,
    "квітні": 4,
    "травень": 5,
    "травня": 5,
    "травні": 5,
    "червень": 6,
    "червня": 6,
    "червні": 6,
    "липень": 7,
    "липня": 7,
    "липні": 7,
    "серпень": 8,
    "серпня": 8,
    "серпні": 8,
    "вересень": 9,
    "вересня": 9,
    "вересні": 9,
    "жовтень": 10,
    "жовтня": 10,
    "жовтні": 10,
    "листопад": 11,
    "листопада": 11,
    "листопаді": 11,
    "грудень": 12,
    "грудня": 12,
    "грудні": 12,
}

# For saying a date out loud: «21 серпня», not «21 серпень».
MONTHS_GENITIVE = (
    "січня",
    "лютого",
    "березня",
    "квітня",
    "травня",
    "червня",
    "липня",
    "серпня",
    "вересня",
    "жовтня",
    "листопада",
    "грудня",
)

# For saying a weekday out loud: «у вівторок», «у середу».
WEEKDAYS_SPOKEN = (
    "у понеділок",
    "у вівторок",
    "у середу",
    "у четвер",
    "у п'ятницю",
    "у суботу",
    "у неділю",
)

# ── English ──────────────────────────────────────────────────────────
#
# The same three tables, said in English. Kept apart rather than merged:
# «may» is a month only when it is one ("in May"), while "may" is a modal
# in every other sentence, and the two languages' weekdays share no
# spelling to fall back on.

EN_WORD_NUMBERS: dict[str, int] = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

EN_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

EN_MONTHS: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

EN_MONTHS_SPOKEN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

EN_WEEKDAYS_SPOKEN = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
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
    return WORD_NUMBERS.get(raw) or EN_WORD_NUMBERS.get(raw, 1)


_NUM = r"(\d{1,2}|" + "|".join(sorted(WORD_NUMBERS, key=len, reverse=True)) + r")"
_WEEKDAY = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))

_EN_NUM = r"(\d{1,2}|" + "|".join(sorted(EN_WORD_NUMBERS, key=len, reverse=True)) + r")"
_EN_WEEKDAY = "|".join(sorted(EN_WEEKDAYS, key=len, reverse=True))
_EN_MONTH = "|".join(sorted(EN_MONTHS, key=len, reverse=True))
# «may» alone is a modal verb far more often than a month; it is reached
# only through "in May", "last May" or "this May".
_EN_MONTH_NO_MAY = "|".join(
    sorted((m for m in EN_MONTHS if m != "may"), key=len, reverse=True)
)

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

# English, in the same order and for the same reasons: a longer phrase
# that contains a shorter one must win first ("last weekend" before
# "last week"), and a full date before the bare month or weekday it
# contains ("12 October" before "October").
_EN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bthe\s+day\s+before\s+yesterday\b"), "day_before_yesterday"),
    (re.compile(r"\byesterday\b"), "yesterday"),
    (re.compile(r"\btoday\b"), "today"),
    (
        re.compile(r"\btomorrow(?:\s+at\s+\d{1,2}(?::\d{2})?(?:\s*(?:am|pm))?)?\b"),
        "tomorrow",
    ),
    # An hour or a minute ago is still today: the span API counts days,
    # and "two hours ago" is said about a conversation had today.
    (re.compile(rf"\b{_EN_NUM}\s+(?:hours?|minutes?)\s+ago\b"), "today"),
    (re.compile(rf"\b{_EN_NUM}\s+days?\s+ago\b"), "days_ago"),
    (re.compile(rf"\b{_EN_NUM}\s+weeks?\s+ago\b"), "weeks_ago"),
    (re.compile(rf"\b{_EN_NUM}\s+months?\s+ago\b"), "months_ago"),
    (re.compile(r"\blast\s+weekend\b"), "last_weekend"),
    (re.compile(r"\bthis\s+weekend\b"), "this_weekend"),
    (re.compile(r"\blast\s+week\b"), "last_week"),
    (re.compile(r"\bthis\s+week\b"), "this_week"),
    (re.compile(r"\blast\s+month\b"), "last_month"),
    (re.compile(r"\bthis\s+month\b"), "this_month"),
    (re.compile(r"\blast\s+year\b"), "last_year"),
    (re.compile(r"\bthis\s+year\b"), "this_year"),
    (
        re.compile(
            rf"\b(?:on\s+(?:the\s+)?)?(?P<dom>\d{{1,2}})(?:st|nd|rd|th)?\s+"
            rf"(?:of\s+)?(?P<mon>{_EN_MONTH})"
            rf"(?:\s+(?P<year>\d{{4}}))?\b"
        ),
        "date",
    ),
    (
        re.compile(
            rf"\b(?:on\s+(?:the\s+)?)?(?P<mon>{_EN_MONTH})\s+"
            rf"(?P<dom>\d{{1,2}})(?:st|nd|rd|th)?"
            rf"(?:\s+(?P<year>\d{{4}}))?\b"
        ),
        "date_month_first",
    ),
    (
        re.compile(rf"\b(?:on\s+)?(?:last\s+|this\s+)?({_EN_WEEKDAY})\b"),
        "weekday",
    ),
    (re.compile(rf"\b(?:in|last|this)\s+({_EN_MONTH})\b"), "month"),
    (re.compile(rf"\b({_EN_MONTH_NO_MAY})\b"), "month"),
]


def parse_when(text: str, *, now: datetime, lang: str | None = None) -> Span | None:
    """The range of days a time expression names, or None.

    The tables are chosen by language: Ukrainian by default, English when
    ``lang`` says so (or when the process's own language does). ``None``
    is the answer for a question with no time in it, and it means "search
    everything" — never "search today".
    """
    haystack = normalise(text)
    if not haystack:
        return None
    chosen = (_language() if lang is None else str(lang)).strip().lower()
    english = chosen == "en"
    rules = _EN_RULES if english else _RULES
    weekdays = EN_WEEKDAYS if english else WEEKDAYS
    months = EN_MONTHS if english else MONTHS
    today = now.date()

    for pattern, kind in rules:
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
        if kind == "tomorrow":
            return _day(today + timedelta(days=1), phrase)
        if kind == "days_ago":
            return _day(today - timedelta(days=_count(match.group(1))), phrase)
        if kind == "weeks_ago":
            weeks = _count(match.group(1))
            return _week_of(today - timedelta(weeks=weeks), phrase)
        if kind == "months_ago":
            months_back = _count(match.group(1))
            total = today.year * 12 + (today.month - 1) - months_back
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
        if kind == "this_year":
            return _span(date(today.year, 1, 1), date(today.year + 1, 1, 1), phrase)
        if kind in ("date", "date_month_first"):
            month = months[match.group("mon")]
            day = int(match.group("dom"))
            raw_year = match.group("year")
            year = (
                int(raw_year)
                if raw_year
                else (
                    today.year
                    if (month, day) <= (today.month, today.day)
                    else today.year - 1
                )
            )
            try:
                return _day(date(year, month, day), phrase)
            except ValueError:
                # "31 February" is a date-shaped sentence, not a date.
                continue
        if kind == "weekday":
            wanted = weekdays[match.group(1)]
            back = (today.weekday() - wanted) % 7
            # said on a Tuesday it means today, not a week ago.
            return _day(today - timedelta(days=back), phrase)
        if kind == "month":
            month = months[match.group(1)]
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


def said_when(ts: float, *, now: datetime, lang: str | None = None) -> str:
    """When something was said, in the words a person would use.

    Recent days get their own name because that is how they are spoken
    about; past a week, a name stops being a location and a date starts
    being one. The language is the process's own, or the explicit ``lang``.
    """
    chosen = (_language() if lang is None else str(lang)).strip().lower()
    when = datetime.fromtimestamp(ts)
    days = (now.date() - when.date()).days
    if chosen == "en":
        if days == 0:
            return "today"
        if days == 1:
            return "yesterday"
        if days == 2:
            return "the day before yesterday"
        if 3 <= days <= 6:
            return EN_WEEKDAYS_SPOKEN[when.weekday()]
        said = f"{when.day} {EN_MONTHS_SPOKEN[when.month - 1]}"
        if when.year != now.year:
            said += f" {when.year}"
        return said
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
    "EN_MONTHS",
    "EN_MONTHS_SPOKEN",
    "EN_WEEKDAYS",
    "EN_WEEKDAYS_SPOKEN",
    "EN_WORD_NUMBERS",
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
