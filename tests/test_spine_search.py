"""Searching the conversations it actually had.

Two halves, tested apart. The index and the query run against a real
temporary SQLite file, because an FTS5 table with triggers is exactly the
kind of thing that is correct in a fake and empty in the database. The
wording of the answer runs against a list of fragments, because it is a
voice reply and the only question about it is whether it is one sentence
that says when.

The failure this file exists to catch is the one the real database
already had waiting: `transcripts.source` was added late and reads NULL
on 3 592 of its 4 412 rows, so a filter written the obvious way —
`source = 'voice'` — passes every test built from fresh writes and finds
almost nothing in the file it was written for.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from src.spine import search
from src.spine.persist import ADDRESSED, OVERHEARD, SpinePersistence

NOW = datetime(2026, 8, 21, 14, 30)
NOW_TS = NOW.timestamp()
DAY = 86400.0


@pytest.fixture
def persist(tmp_path):
    p = SpinePersistence(tmp_path / "heare.db")
    try:
        yield p
    finally:
        p.close()


def write(
    persist: SpinePersistence,
    text: str,
    *,
    ago_days: float = 0.0,
    agent: int = 0,
    source: str | None = ADDRESSED,
) -> None:
    """One transcript row, placed in time. Straight SQL rather than
    `log_user_turn`, so a case can put a line last Tuesday."""
    persist._conn.execute(
        "INSERT INTO transcripts (ts, text, mode, agent_spoken, source) "
        "VALUES (?, ?, 'spine', ?, ?)",
        (NOW_TS - ago_days * DAY, text, agent, source),
    )
    persist._conn.commit()


def texts(fragments: list[search.Fragment]) -> list[str]:
    return [f.text for f in fragments]


# ── the index ─────────────────────────────────────────────────────────


def test_a_line_becomes_findable_the_moment_it_is_written(persist) -> None:
    """The triggers, in one assertion. Without them the index is a table
    that exists and never fills."""
    turn = persist.log_user_turn("таймаут піднімаємо до тридцяти, це тимчасово")
    persist.log_agent_reply("Запам'ятав: тридцять секунд, тимчасово.", turn)

    found = search.find(persist, "таймаут", now=NOW)
    assert "тридцяти" in found[0].text


def test_rows_written_before_the_index_existed_are_still_found(tmp_path) -> None:
    """The 4 412 rows already on disk are the entire point of the
    feature, and no trigger can reach backwards for them."""
    db = tmp_path / "heare.db"
    first = SpinePersistence(db)
    # Drop the index and the marker: what a database looks like the
    # moment before this code was written.
    first._conn.executescript(
        "DROP TABLE IF EXISTS transcripts_fts;"
        "DROP TRIGGER IF EXISTS transcripts_ai;"
        "DROP TRIGGER IF EXISTS transcripts_ad;"
        "DROP TRIGGER IF EXISTS transcripts_au;"
        "DELETE FROM meta WHERE key = 'transcripts_fts';"
    )
    first._conn.execute(
        "INSERT INTO transcripts (ts, text, mode, agent_spoken, source) "
        "VALUES (?, 'збірка падає на сорок сьомому кроці', 'spine', 0, NULL)",
        (NOW_TS - DAY,),
    )
    first._conn.commit()
    first.close()

    reopened = SpinePersistence(db)
    try:
        assert texts(search.find(reopened, "збірка", now=NOW))
    finally:
        reopened.close()


def test_a_row_written_through_another_connection_is_indexed_too(persist) -> None:
    """The triggers are in the database file, not in the code that made
    them. Any future writer — another process, the async store if it
    ever logs again — indexes what it writes without knowing they exist,
    which is why they are defined beside the table and not in the spine.
    """
    with sqlite3.connect(persist.db_path) as other:
        other.execute(
            "INSERT INTO transcripts (ts, text, mode, agent_spoken) "
            "VALUES (?, 'воротар пропускає не ту репліку', 'daemon', 0)",
            (NOW_TS - DAY,),
        )
        other.commit()

    assert texts(search.find(persist, "воротар", now=NOW))


def test_the_backfill_runs_once_and_not_on_every_start(tmp_path) -> None:
    """`rebuild` over a hundred thousand rows on every launch is a cost
    nobody would attribute to search."""
    db = tmp_path / "heare.db"
    for _ in range(2):
        p = SpinePersistence(db)
        p.close()
    with sqlite3.connect(db) as conn:
        marks = conn.execute(
            "SELECT count(*) FROM meta WHERE key = 'transcripts_fts'"
        ).fetchone()[0]
    assert marks == 1


def test_a_forgotten_line_stops_being_findable(persist) -> None:
    """Otherwise «забудь останню годину» deletes the row and leaves the
    text searchable — the worst of both."""
    write(persist, "хтось у кімнаті назвав пароль", source=OVERHEARD)
    assert texts(search.find(persist, "пароль", now=NOW, room=True))

    persist.forget_overheard_since(after_ts=NOW_TS - DAY)
    assert search.find(persist, "пароль", now=NOW, room=True) == []


# ── whose words ───────────────────────────────────────────────────────


def test_the_assistants_own_replies_are_searched_too(persist) -> None:
    """What Whisper heard is «докер»; what the assistant wrote back is
    «Дока». The reply is often the only correctly spelled copy of what
    the person actually said."""
    write(persist, "докер, підніми таймаут", ago_days=1, agent=0)
    write(persist, "Дока підняла таймаут до тридцяти.", ago_days=1, agent=1)

    found = search.find(persist, "Дока", now=NOW)
    assert [f.agent_spoken for f in found] == [True]


def test_a_worker_result_is_not_something_the_person_said(persist) -> None:
    """`delegate` results re-enter as user turns carrying an instruction
    to the model. Returned as «ти казав», the assistant quotes its own
    plumbing back at whoever asked."""
    write(
        persist,
        "[результат роботи] Робота завершена. Результат: таймаут тридцять",
        ago_days=1,
    )
    write(persist, "хай таймаут буде тридцять", ago_days=2)

    assert texts(search.find(persist, "таймаут", now=NOW)) == [
        "хай таймаут буде тридцять"
    ]


# ── the room stays out unless invited ─────────────────────────────────


def test_the_room_is_not_searched_by_default(persist) -> None:
    """Overheard speech is other people. Folded into an ordinary answer
    there is no way to tell from the answer that it happened."""
    write(persist, "колега сказав, що деплой о шостій", source=OVERHEARD)
    assert search.find(persist, "деплой", now=NOW) == []


def test_the_room_is_searched_when_it_is_asked_for(persist) -> None:
    write(persist, "колега сказав, що деплой о шостій", source=OVERHEARD)
    assert texts(search.find(persist, "деплой", now=NOW, room=True))


def test_a_row_with_no_source_is_a_conversation_not_the_room(persist) -> None:
    """3 592 of the rows on this machine read NULL — every line written
    before the column existed, and every line the spine has logged. A
    filter of `source = 'voice'` would find none of them."""
    write(persist, "домовились на тридцять секунд", source=None)
    assert texts(search.find(persist, "тридцять", now=NOW))


# ── when ──────────────────────────────────────────────────────────────


def test_a_time_expression_narrows_the_search(persist) -> None:
    write(persist, "таймаут двадцять", ago_days=6)
    write(persist, "таймаут тридцять", ago_days=1)

    assert texts(search.find(persist, "таймаут", now=NOW, when="вчора")) == [
        "таймаут тридцять"
    ]


def test_the_time_word_in_the_query_counts_even_unasked_for(persist) -> None:
    """A model rephrasing the question into `query` keeps «вчора» in it
    far more reliably than it fills in a second argument."""
    write(persist, "таймаут двадцять", ago_days=6)
    write(persist, "таймаут тридцять", ago_days=1)

    assert texts(search.find(persist, "що я казав вчора про таймаут", now=NOW)) == [
        "таймаут тридцять"
    ]


def test_a_question_that_is_only_a_date_answers_with_the_day(persist) -> None:
    """«що я казав учора» is nothing but stopwords once «вчора» is taken
    out. The honest answer is the day itself, not silence."""
    write(persist, "треба переписати воротаря", ago_days=1)
    write(persist, "це було давно", ago_days=9)

    assert texts(search.find(persist, "що я казав учора", now=NOW)) == [
        "треба переписати воротаря"
    ]


def test_a_question_with_neither_terms_nor_a_date_finds_nothing(persist) -> None:
    """Without both it is not a question, and answering it with the most
    recent thing said would be an answer to nothing."""
    write(persist, "будь-що", ago_days=1)
    assert search.find(persist, "а що я", now=NOW) == []


def test_the_more_recent_mention_wins_between_equals(persist) -> None:
    """Asked out loud, «що я казав про таймаут» almost always means the
    last time. bm25 alone answers with whichever line was shortest."""
    write(persist, "таймаут має бути двадцять секунд", ago_days=40)
    write(persist, "таймаут має бути тридцять секунд", ago_days=1)

    assert texts(search.find(persist, "таймаут", now=NOW))[0] == (
        "таймаут має бути тридцять секунд"
    )


# ── three fragments, not thirty ───────────────────────────────────────


def test_no_more_than_three_come_back(persist) -> None:
    for i in range(8):
        write(persist, f"таймаут варіант {i}", ago_days=i)
    assert len(search.find(persist, "таймаут", now=NOW)) == 3


def test_the_wake_word_on_its_own_is_not_a_memory(persist) -> None:
    """Found against the real database: «докер» answered «12 серпня ти
    казав: Докер.» — bm25 rewards short documents, and the shortest
    lines in a voice transcript are the wake word and «Дякую.»."""
    write(persist, "Докер.", ago_days=1)
    write(persist, "Докер, підніми таймаут до тридцяти", ago_days=2)

    assert texts(search.find(persist, "докер", now=NOW)) == [
        "Докер, підніми таймаут до тридцяти"
    ]


def test_the_same_sentence_twice_counts_once(persist) -> None:
    """A user line and the reply restating it are near-identical often
    enough that the top three are frequently one thing said once."""
    write(persist, "таймаут тридцять", ago_days=1, agent=0)
    write(persist, "Таймаут  тридцять", ago_days=1, agent=1)
    write(persist, "таймаут двадцять", ago_days=3)

    assert len(search.find(persist, "таймаут", now=NOW)) == 2


# ── never into the conversation path ──────────────────────────────────


def test_a_broken_index_costs_the_verb_and_nothing_else(persist) -> None:
    """A SQLite without FTS5, or a corrupted index, must not take the
    assistant's ability to talk down with it."""
    persist._conn.executescript("DROP TABLE IF EXISTS transcripts_fts;")
    assert search.find(persist, "таймаут", now=NOW) == []


# ── the sentence it says ──────────────────────────────────────────────


def frag(text: str, *, ago_days: float = 1.0, agent: bool = False):
    return search.Fragment(NOW_TS - ago_days * DAY, text, agent)


def test_nothing_found_is_said_as_not_remembering() -> None:
    assert search.spoken([], now=NOW) == "Не пригадую, щоб ми про це говорили."


def test_the_answer_names_when_it_was() -> None:
    """A quote with no date is a coincidence, not a memory."""
    said = search.spoken([frag("таймаут піднімаємо до тридцяти")], now=NOW)
    assert said == "Вчора ти казав: таймаут піднімаємо до тридцяти."


def test_the_assistants_own_line_is_not_attributed_to_the_person() -> None:
    said = search.spoken([frag("Підняла таймаут до тридцяти.", agent=True)], now=NOW)
    assert said.startswith("Вчора прозвучало:")


def test_the_other_fragments_are_named_by_day_only() -> None:
    """Three fragments read out in full is a transcript, not an answer."""
    said = search.spoken(
        [
            frag("таймаут тридцять", ago_days=1),
            frag("таймаут двадцять", ago_days=3),
            frag("таймаут десять", ago_days=9),
        ],
        now=NOW,
    )
    assert said.startswith("Вчора ти казав: таймаут тридцять.")
    assert "у вівторок" in said and "12 серпня" in said
    assert "двадцять" not in said


def test_the_other_days_are_named_newest_first() -> None:
    """The ranking that chose them is invisible to whoever is listening,
    so relevance order sounds like the dates came out shuffled."""
    said = search.spoken(
        [
            frag("таймаут тридцять", ago_days=9),
            frag("таймаут двадцять", ago_days=13),
            frag("таймаут десять", ago_days=4),
        ],
        now=NOW,
    )
    # 17 August is four days back and still has a weekday name; 8 August
    # does not. Newest first regardless of which form it takes.
    assert said.endswith("Ще про це було у понеділок і 8 серпня.")


def test_a_day_named_twice_is_named_once() -> None:
    said = search.spoken(
        [frag("а", ago_days=1), frag("б", ago_days=3), frag("в", ago_days=3)],
        now=NOW,
    )
    assert said.count("у вівторок") == 1


def test_the_answer_carries_no_markup_a_voice_cannot_say() -> None:
    said = search.spoken(
        [frag("таймаут тридцять"), frag("таймаут двадцять", ago_days=4)], now=NOW
    )
    assert not any(ch in said for ch in "*_#`\n[]")


def test_a_long_fragment_is_cut_at_a_word() -> None:
    """Mid-syllable, the TTS says half a word and the sentence stops
    meaning anything."""
    long = "слово " * 80
    cut = search.shorten(long)
    assert len(cut) <= search.FRAGMENT_CHARS + 1
    assert cut.endswith("…") and not cut.endswith("сло…")


def test_a_short_fragment_is_left_alone() -> None:
    assert search.shorten("таймаут тридцять") == "таймаут тридцять"


# ── the verb, end to end ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_verb_answers_out_loud(tmp_path) -> None:
    """`SpineLoop._run_tool` speaks what a verb returns, verbatim — so
    the sentence has to be finished by the toolbox, not by the model."""
    from src.config import Settings
    from src.spine.tools import VoiceToolbox

    p = SpinePersistence(tmp_path / "heare.db")
    turn = p.log_user_turn("запам'ятай: таймаут піднімаємо до тридцяти, це тимчасово")
    p.log_agent_reply("Запам'ятав.", turn)

    class FakeHands:
        def set_delivery(self, fn):
            pass

    async def deliver(text: str) -> None:
        pass

    toolbox = VoiceToolbox(
        Settings(), None, deliver, hands_factory=lambda s: FakeHands(), persist=p
    )
    said = await toolbox.execute("search_conversations", {"query": "таймаут"})
    p.close()

    assert "тридцяти" in said and "тимчасово" in said
    assert said.startswith("Сьогодні ти казав:")


@pytest.mark.asyncio
async def test_the_verb_says_so_when_nothing_is_written_down() -> None:
    """With persistence off there is nothing to search, and inventing an
    apology about not finding anything would be a different lie."""
    from src.config import Settings
    from src.spine.tools import VoiceToolbox

    class FakeHands:
        def set_delivery(self, fn):
            pass

    async def deliver(text: str) -> None:
        pass

    toolbox = VoiceToolbox(
        Settings(), None, deliver, hands_factory=lambda s: FakeHands(), persist=None
    )
    assert await toolbox.execute("search_conversations", {"query": "таймаут"}) == (
        "Я не веду записів наших розмов."
    )


@pytest.mark.asyncio
async def test_the_verb_never_raises_into_the_conversation(tmp_path) -> None:
    from src.config import Settings
    from src.spine.tools import VoiceToolbox

    class Exploding:
        def search_transcripts(self, *a, **kw):
            raise RuntimeError("disk gone")

    class FakeHands:
        def set_delivery(self, fn):
            pass

    async def deliver(text: str) -> None:
        pass

    toolbox = VoiceToolbox(
        Settings(),
        None,
        deliver,
        hands_factory=lambda s: FakeHands(),
        persist=Exploding(),
    )
    said = await toolbox.execute("search_conversations", {"query": "таймаут"})
    assert said == "Не вийшло. Спробуй ще раз."


def test_now_is_read_once_per_search() -> None:
    """The range and the wording must agree. Read twice across midnight
    they disagree by a day — «вчора» searched, «позавчора» said."""
    import inspect

    from src.spine.tools import VoiceToolbox

    source = inspect.getsource(VoiceToolbox._search_conversations)
    assert source.count("datetime.now()") == 1


def test_the_clock_is_never_read_inside_the_search() -> None:
    """Everything in `search.py` is a pure function of `now`, which is
    what makes the table of cases above possible at all."""
    import inspect

    assert "time.time()" not in inspect.getsource(search)
    assert "datetime.now()" not in inspect.getsource(search)


def test_a_stale_conversation_is_still_searchable(persist) -> None:
    """Search reads `transcripts`, not the open conversation. A line from
    a conversation that closed weeks ago answers as readily as today's."""
    write(persist, "вирішили не чіпати воротаря", ago_days=60)
    found = search.find(persist, "воротаря", now=NOW)
    assert texts(found) == ["вирішили не чіпати воротаря"]
    assert "22 червня" in search.spoken(found, now=NOW)
