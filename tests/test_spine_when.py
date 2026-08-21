"""Ukrainian time expressions, as a table of the ways they go wrong.

This is the part of conversation search that is easy to get subtly wrong
and impossible to notice: an off-by-one day makes «вчора» return nothing,
and nothing is exactly what a search legitimately returns when there is
nothing there. Nobody would ever find it from a log.

Every case fixes `now` at Friday, 21 August 2026 — a Friday on purpose,
so that "the most recent Tuesday", "that weekend" and today are three
different answers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.spine.when import Span, normalise, parse_when, said_when, strip_when

# Friday, 21 August 2026.
NOW = datetime(2026, 8, 21, 14, 30)


def days(span: Span) -> tuple[str, str]:
    """The span as the two dates a person would name."""
    return (
        datetime.fromtimestamp(span.start).strftime("%Y-%m-%d"),
        datetime.fromtimestamp(span.end).strftime("%Y-%m-%d"),
    )


def when(text: str) -> Span:
    span = parse_when(text, now=NOW)
    assert span is not None, f"nothing parsed out of {text!r}"
    return span


# ── the days with names ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, first, last",
    [
        ("сьогодні", "2026-08-21", "2026-08-22"),
        ("вчора", "2026-08-20", "2026-08-21"),
        ("учора", "2026-08-20", "2026-08-21"),
        ("позавчора", "2026-08-19", "2026-08-20"),
    ],
)
def test_the_days_that_have_their_own_word(text: str, first: str, last: str) -> None:
    """«позавчора» contains «вчора». Matched in the wrong order it is
    always yesterday, and always off by exactly one day."""
    assert days(when(f"що я казав {text} про таймаут")) == (first, last)


# ── counting backwards ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, first",
    [
        ("три дні тому", "2026-08-18"),
        ("3 дні тому", "2026-08-18"),
        ("два дні тому", "2026-08-19"),
        ("п'ять днів тому", "2026-08-16"),
        ("п’ять днів тому", "2026-08-16"),
        ("десять днів тому", "2026-08-11"),
    ],
)
def test_n_days_ago_is_that_day_not_the_days_since(text: str, first: str) -> None:
    """Spoken, «три дні тому» names a day. Read as "the last three days"
    it would answer a question about Wednesday with Friday's talk."""
    span = when(text)
    assert days(span) == (first, (datetime.fromisoformat(first)
                                  + timedelta(days=1)).strftime("%Y-%m-%d"))


def test_the_typographic_apostrophe_counts_as_an_apostrophe() -> None:
    """Whisper writes «п’ять», the keyboard writes «п'ять». One of the two
    would otherwise fall through to "one day ago" without a word."""
    assert normalise("П’ЯТЬ") == "п'ять"


# ── weeks ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["минулого тижня", "на минулому тижні", "на тому тижні", "тиждень тому"],
)
def test_last_week_is_monday_to_monday(text: str) -> None:
    """Not "the last seven days": the week someone means starts on a
    Monday, and on a Friday those two differ by three days of talk."""
    assert days(when(text)) == ("2026-08-10", "2026-08-17")


def test_this_week_starts_at_monday_not_seven_days_back() -> None:
    assert days(when("цього тижня")) == ("2026-08-17", "2026-08-24")


def test_two_weeks_ago_is_a_week_not_a_day() -> None:
    assert days(when("два тижні тому")) == ("2026-08-03", "2026-08-10")


# ── weekends ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text", ["на тих вихідних", "на минулих вихідних", "минулі вихідні"]
)
def test_that_weekend_is_the_saturday_and_sunday_before_this_week(text: str) -> None:
    """It ends on Monday, not on Sunday evening: the range is half-open,
    and a Sunday-night conversation is on the weekend it was had."""
    assert days(when(text)) == ("2026-08-15", "2026-08-17")


def test_this_weekend_is_the_one_this_week_is_heading_into() -> None:
    assert days(when("на цих вихідних")) == ("2026-08-22", "2026-08-24")


# ── weekdays ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, first",
    [
        ("у вівторок", "2026-08-18"),
        ("в середу", "2026-08-19"),
        ("у понеділок", "2026-08-17"),
        ("у четвер", "2026-08-20"),
        ("в неділю", "2026-08-16"),
    ],
)
def test_a_weekday_means_the_most_recent_one(text: str, first: str) -> None:
    """Asked on a Friday, «у вівторок» is three days back — never the
    Tuesday coming, which holds no transcripts at all."""
    assert days(when(f"{text} ми говорили про збірку"))[0] == first


def test_naming_today_by_its_weekday_means_today() -> None:
    """«у п'ятницю», said on a Friday. A week back would be a different
    conversation entirely."""
    assert days(when("у п'ятницю"))[0] == "2026-08-21"


def test_the_preposition_among_is_not_a_wednesday() -> None:
    """«серед» is the stem of «середа» and also the word "among". Matched
    by stem, one ordinary sentence silently moves the whole search."""
    assert parse_when("що там серед іншого було", now=NOW) is None


# ── months and years ──────────────────────────────────────────────────


def test_a_month_already_past_this_year_is_this_year() -> None:
    assert days(when("у березні")) == ("2026-03-01", "2026-04-01")


def test_a_month_still_ahead_is_last_year() -> None:
    """Asked in August, «у листопаді» can only be the one that happened."""
    assert days(when("у листопаді")) == ("2025-11-01", "2025-12-01")


def test_the_current_month_runs_to_the_next_one() -> None:
    assert days(when("у серпні")) == ("2026-08-01", "2026-09-01")


def test_last_month_crosses_a_year_boundary_correctly() -> None:
    january = datetime(2026, 1, 9, 10, 0)
    span = parse_when("минулого місяця", now=january)
    assert span is not None
    assert days(span) == ("2025-12-01", "2026-01-01")


def test_last_year_is_the_whole_of_it() -> None:
    assert days(when("минулого року")) == ("2025-01-01", "2026-01-01")


# ── saying nothing ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["що я казав про таймаут", "", "   ", "нагадай про докер"],
)
def test_a_question_without_a_time_in_it_parses_to_nothing(text: str) -> None:
    """None means "search everything". Guessing a range here would answer
    a question about last month with today's silence."""
    assert parse_when(text, now=NOW) is None


# ── the phrase comes back out ─────────────────────────────────────────


def test_the_time_expression_is_removed_from_the_query() -> None:
    """«вчора» appears in no transcript. Left in the search terms it
    matches nothing while diluting the words that would have."""
    span = when("що я казав вчора про таймаут")
    assert strip_when("що я казав вчора про таймаут", span) == "що я казав про таймаут"


def test_stripping_nothing_leaves_the_query_alone() -> None:
    assert strip_when("про таймаут", None) == "про таймаут"


# ── saying when it was ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ts, said",
    [
        (datetime(2026, 8, 21, 9, 0), "сьогодні"),
        (datetime(2026, 8, 20, 9, 0), "вчора"),
        (datetime(2026, 8, 19, 9, 0), "позавчора"),
        (datetime(2026, 8, 18, 9, 0), "у вівторок"),
        (datetime(2026, 8, 17, 9, 0), "у понеділок"),
        (datetime(2026, 8, 14, 9, 0), "14 серпня"),
        (datetime(2026, 3, 3, 9, 0), "3 березня"),
        (datetime(2025, 12, 30, 9, 0), "30 грудня 2025 року"),
    ],
)
def test_when_something_was_said_in_the_words_a_person_uses(
    ts: datetime, said: str
) -> None:
    """An answer that quotes something back without saying when it was
    said is a coincidence, not a memory."""
    assert said_when(ts.timestamp(), now=NOW) == said


def test_a_day_is_a_day_regardless_of_the_hour() -> None:
    """Fourteen hours apart but both today: counted in elapsed seconds,
    the morning would become «вчора» by the evening."""
    assert said_when(datetime(2026, 8, 21, 0, 5).timestamp(), now=NOW) == "сьогодні"
    assert said_when(datetime(2026, 8, 20, 23, 55).timestamp(), now=NOW) == "вчора"
