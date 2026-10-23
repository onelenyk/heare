from __future__ import annotations

import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from heare.memory.book import Book
from heare.memory.contracts import CONFIRMED, PENDING, REJECTED, Fact
from heare.memory.facts import SQLiteFacts


def _fact(
    *,
    subject: str = "owner",
    predicate: str = "likes",
    value: str = "tea",
    text: str = "The owner likes tea",
    source: str = "user_said",
    trust: str = "user_said",
    status: str = CONFIRMED,
    confidence: float = 0.9,
    valid_from: float = 0.0,
    valid_to: float | None = None,
    source_ids: tuple[str, ...] = ("source-1",),
    supersedes: int | None = None,
    created_at: float = 100.0,
) -> Fact:
    return Fact(
        subject=subject,
        predicate=predicate,
        value=value,
        text=text,
        source=source,
        trust=trust,
        status=status,
        confidence=confidence,
        valid_from=valid_from,
        valid_to=valid_to,
        source_ids=source_ids,
        supersedes=supersedes,
        created_at=created_at,
    )


def test_add_and_get_round_trip(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    fact = _fact(source_ids=("event-1", "event-2"))

    fact_id = store.add(fact)
    stored = store.get(fact_id)

    assert stored == replace(fact, id=fact_id)
    assert stored is not None
    assert stored.source_ids == ("event-1", "event-2")
    assert stored.id == fact_id


def test_add_is_idempotent_by_source(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")

    first = store.add(_fact(source_ids=("event-1",)))
    second = store.add(_fact(text="different", source_ids=("event-1",)))

    assert second == first
    assert [fact.id for fact in store.current(include_pending=True)] == [first]
    assert store.has_source("event-1")
    assert not store.has_source("event-2")


def test_current_filters_status_window_subject_and_predicate(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    confirmed = store.add(
        _fact(
            subject="owner",
            predicate="likes",
            valid_from=100.0,
            valid_to=200.0,
            source_ids=("confirmed",),
        )
    )
    pending = store.add(
        _fact(
            subject="owner",
            predicate="likes",
            valid_from=100.0,
            valid_to=200.0,
            status=PENDING,
            source_ids=("pending",),
        )
    )
    other = store.add(
        _fact(
            subject="guest",
            predicate="drinks",
            valid_from=0.0,
            source_ids=("other",),
        )
    )

    def ids(
        *,
        subject: str | None = None,
        predicate: str | None = None,
        now: float | None = None,
        include_pending: bool = False,
    ) -> set[int | None]:
        return {
            fact.id
            for fact in store.current(
                subject=subject,
                predicate=predicate,
                now=now,
                include_pending=include_pending,
            )
        }

    assert ids(subject="owner", predicate="likes", now=100.0) == {confirmed}
    assert ids(subject="owner", predicate="likes", now=150.0) == {confirmed}
    assert ids(subject="owner", predicate="likes", now=200.0) == set()
    assert ids(
        subject="owner",
        predicate="likes",
        now=150.0,
        include_pending=True,
    ) == {confirmed, pending}
    assert ids(now=50.0) == {other}
    assert ids(subject="guest", now=50.0) == {other}
    assert ids(predicate="drinks", now=50.0) == {other}


def test_search_finds_text_value_and_predicate(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    by_text = store.add(
        _fact(
            text="The owner prefers green tea",
            value="tea",
            predicate="prefers",
            source_ids=("text",),
        )
    )
    by_value = store.add(
        _fact(
            text="A drink",
            value="oolong",
            predicate="drinks",
            source_ids=("value",),
        )
    )
    by_predicate = store.add(
        _fact(
            text="Something quiet",
            value="nothing",
            predicate="meditates",
            source_ids=("predicate",),
        )
    )

    assert store.search("green")[0].id == by_text
    assert store.search("oolong")[0].id == by_value
    assert store.search("meditates")[0].id == by_predicate


def test_search_filters_pending_and_honours_limit(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    confirmed = [
        store.add(
            _fact(
                text=f"tea number {index}",
                source_ids=(f"confirmed-{index}",),
                created_at=float(index),
            )
        )
        for index in range(3)
    ]
    pending = store.add(
        _fact(
            text="tea pending",
            status=PENDING,
            source_ids=("pending",),
            created_at=10.0,
        )
    )
    expired = store.add(
        _fact(
            text="tea expired",
            valid_to=10.0,
            source_ids=("expired",),
        )
    )

    found = {fact.id for fact in store.search("tea", now=20.0)}
    assert found == set(confirmed)
    assert expired not in found
    assert len(store.search("tea", now=20.0, limit=2)) == 2
    assert pending in {
        fact.id for fact in store.search("tea", now=20.0, include_pending=True)
    }


def test_search_survives_arbitrary_punctuation(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    store.add(_fact(text="The owner likes tea", source_ids=("tea",)))

    assert store.search('tea" OR *')[0].text == "The owner likes tea"
    assert store.search("***") == []
    assert store.search("") == []


def test_confirm_reject_and_forget(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    fact_id = store.add(_fact(status=PENDING, source_ids=("pending",)))

    assert store.current() == []
    assert store.confirm(fact_id)
    assert store.get(fact_id).status == CONFIRMED
    assert [fact.id for fact in store.current()] == [fact_id]

    assert store.reject(fact_id)
    stored = store.get(fact_id)
    assert stored is not None
    assert stored.status == REJECTED
    assert store.current() == []
    assert not store.confirm(fact_id)


def test_forget_closes_the_interval_and_keeps_the_row(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    fact_id = store.add(_fact(source_ids=("forget-me",)))

    assert store.forget(fact_id)
    stored = store.get(fact_id)
    assert stored is not None
    assert stored.status == REJECTED
    assert stored.valid_to is not None
    assert store.current() == []
    assert not store.forget(fact_id + 1)


def test_supersede_closes_old_and_points_new_at_it(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    old_id = store.add(
        _fact(value="tea", valid_from=100.0, source_ids=("old",))
    )
    new = _fact(value="coffee", valid_from=200.0, source_ids=("new",))

    new_id = store.supersede(old_id, new)

    old = store.get(old_id)
    replacement = store.get(new_id)
    assert old is not None
    assert replacement is not None
    assert old.valid_to == 200.0
    assert replacement.supersedes == old_id
    assert replacement.valid_to is None
    assert {fact.id for fact in store.current(now=150.0)} == {old_id}
    assert {fact.id for fact in store.current(now=250.0)} == {new_id}


def test_a_second_store_sees_the_same_rows(tmp_path):
    path = tmp_path / "heare.db"
    first = SQLiteFacts(path)
    fact_id = first.add(_fact(source_ids=("shared",)))

    second = SQLiteFacts(path)

    assert second.get(fact_id) == first.get(fact_id)
    assert second.has_source("shared")


def test_a_new_database_is_private(tmp_path):
    path = tmp_path / "heare.db"

    SQLiteFacts(path)

    assert path.stat().st_mode & 0o777 == 0o600


def test_a_database_created_by_the_book_is_private(tmp_path):
    path = tmp_path / "heare.db"

    book = Book(path, profile_dir=tmp_path / "memory")
    book.close()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_add_races_safely_on_the_same_source(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")
    fact = _fact(source_ids=("event-1",))
    start = threading.Barrier(2)

    def add(_: int) -> int:
        start.wait()
        return store.add(fact)

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(add, range(2)))

    assert ids[0] == ids[1]
    assert [fact.id for fact in store.current(include_pending=True)] == [ids[0]]


def test_add_accepts_repeated_source_ids_within_one_fact(tmp_path):
    store = SQLiteFacts(tmp_path / "heare.db")

    fact_id = store.add(_fact(source_ids=("same", "same")))

    assert store.has_source("same")
    stored = store.get(fact_id)
    assert stored is not None
    assert stored.source_ids == ("same", "same")


def test_default_path_is_under_the_configured_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    store = SQLiteFacts()
    store.add(_fact(source_ids=("home",)))

    assert store.db_path == tmp_path / ".heare" / "heare.db"
    assert store.db_path.is_relative_to(tmp_path)
