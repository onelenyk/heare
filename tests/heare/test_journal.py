"""The journal: what is written, what is read back, what is forgotten."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import stat
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from heare.core.state import State
from heare.memory.book import Book
from heare.memory.journal import Journal


def _lines(root):
    return [
        line
        for path in sorted(root.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_append_writes_one_line_with_the_agreed_fields(tmp_path):
    journal = Journal(tmp_path)
    journal.append(
        "heard", {"text": "привіт", "channel": "voice"},
        source="voice", trust="user_said",
    )
    journal.append(
        "said", {"text": "вітаю", "channel": "text", "failed": None},
        source="text", trust="system",
    )

    lines = _lines(tmp_path)
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    for row in rows:
        assert set(row) == {
            "id", "at", "kind", "source", "trust", "trace_id", "payload",
        }
        assert row["id"].startswith("ev_")
        assert datetime.fromisoformat(row["at"]).utcoffset() is not None
    assert rows[0]["kind"] == "heard" and rows[0]["trust"] == "user_said"
    assert rows[1]["payload"]["failed"] is None


def test_events_skips_a_malformed_line_and_a_non_dict_row(tmp_path):
    journal = Journal(tmp_path)
    good = journal.append(
        "heard", {"text": "привіт"}, source="voice", trust="user_said",
    )
    path = next(tmp_path.glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as file:
        file.write("{not json\n[1, 2, 3]\n{\"payload\": {}}\n")

    rows = list(journal.events())
    assert [row["id"] for row in rows] == [good["id"]]


def test_tail_messages_keeps_order_skips_empty_and_limits(tmp_path):
    journal = Journal(tmp_path)
    journal.append(
        "heard", {"text": "перше", "channel": "voice"},
        source="voice", trust="user_said",
    )
    journal.append(
        "said", {"text": "друге", "channel": "voice"},
        source="engine", trust="system",
    )
    journal.append(
        "heard", {"text": "   ", "channel": "voice"},
        source="voice", trust="user_said",
    )
    journal.append("task", {"id": 1}, source="hands", trust="system")
    journal.append(
        "said", {"text": "третє", "channel": "voice"},
        source="engine", trust="system",
    )

    assert journal.tail_messages() == [
        {"role": "user", "content": "перше"},
        {"role": "assistant", "content": "друге"},
        {"role": "assistant", "content": "третє"},
    ]
    assert journal.tail_messages(limit=2) == [
        {"role": "assistant", "content": "друге"},
        {"role": "assistant", "content": "третє"},
    ]


def test_forget_hides_events_and_leaves_the_originals_on_disk(tmp_path):
    journal = Journal(tmp_path)
    heard = journal.append(
        "heard", {"text": "перше"}, source="voice", trust="user_said",
    )
    said = journal.append(
        "said", {"text": "друге"}, source="engine", trust="system",
    )
    before = _lines(tmp_path)
    assert len(before) == 2

    tombstone = journal.forget([said["id"], heard["id"]])

    after = _lines(tmp_path)
    assert after[:2] == before
    assert len(after) == 3
    assert tombstone["kind"] == "forget"
    assert tombstone["payload"] == {
        "ids": sorted([heard["id"], said["id"]]), "by": "user",
    }
    assert journal.tail_messages() == []


def test_latest_reads_from_the_newest_day_and_stops_at_the_limit(tmp_path):
    journal = Journal(tmp_path)
    days = [datetime(2026, 10, d, 12).timestamp() for d in (20, 21, 22)]
    for day, at in enumerate(days):
        for n in range(2):
            journal.append(
                "heard", {"text": f"{day}-{n}"}, source="voice",
                trust="user_said", at=at + n,
            )
    newest = sorted(tmp_path.glob("*.jsonl"))[-1]
    with newest.open("a", encoding="utf-8") as file:
        file.write("{not json\n")

    rows = journal.latest(3)
    assert [row["payload"]["text"] for row in rows] == ["2-1", "2-0", "1-1"]
    assert journal.latest(0) == []


def test_latest_hides_what_was_forgotten(tmp_path):
    journal = Journal(tmp_path)
    kept = journal.append(
        "heard", {"text": "лишається"}, source="voice", trust="user_said",
    )
    gone = journal.append(
        "heard", {"text": "забути"}, source="voice", trust="user_said",
    )
    tombstone = journal.forget([gone["id"]])

    assert [row["id"] for row in journal.latest()] == [
        tombstone["id"], kept["id"],
    ]


def test_new_events_returns_only_what_the_cursor_has_not_seen(tmp_path):
    journal = Journal(tmp_path / "events")
    state_path = tmp_path / "memory" / "consolidate.json"
    first = journal.append(
        "heard", {"text": "привіт"}, source="voice", trust="user_said",
    )
    second = journal.append(
        "said", {"text": "вітаю"}, source="engine", trust="system",
    )

    events, state = journal.new_events(state_path)
    assert [event["id"] for event in events] == [first["id"], second["id"]]
    assert not state_path.exists()
    journal.save_state(state_path, state)

    path = next((tmp_path / "events").glob("*.jsonl"))
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved == {"version": 1, "files": {path.name: path.stat().st_size}}

    third = journal.append(
        "heard", {"text": "ще"}, source="voice", trust="user_said",
    )
    events, state = journal.new_events(state_path)
    assert [event["id"] for event in events] == [third["id"]]
    journal.save_state(state_path, state)

    events, state = journal.new_events(state_path)
    assert events == []
    assert state == {"version": 1, "files": {path.name: path.stat().st_size}}


def test_a_torn_line_is_not_silently_skipped_by_the_cursor(tmp_path, caplog):
    journal = Journal(tmp_path / "events")
    state_path = tmp_path / "memory" / "consolidate.json"
    first = journal.append(
        "heard", {"text": "перше"}, source="voice", trust="user_said",
    )
    path = next((tmp_path / "events").glob("*.jsonl"))
    torn = b'{"id": "ev_torn", "kind": "hea\n'
    with path.open("ab") as file:
        file.write(torn)

    with caplog.at_level(logging.WARNING, logger="heare.memory.journal"):
        events, state = journal.new_events(state_path)

    assert [event["id"] for event in events] == [first["id"]]
    first_line = path.read_bytes().splitlines(keepends=True)[0]
    assert state["files"][path.name] == len(first_line)
    assert any("unreadable line" in record.message for record in caplog.records)

    second = journal.append(
        "heard", {"text": "друге"}, source="voice", trust="user_said",
    )

    assert [row["id"] for row in journal.events()] == [
        first["id"], second["id"],
    ]
    _events, again = journal.new_events(state_path)
    assert again["files"][path.name] == len(first_line)


def test_two_processes_appending_do_not_interleave_a_line(tmp_path):
    root = tmp_path / "events"
    body = "х" * 8_000

    def write(worker: int) -> None:
        journal = Journal(root)
        for step in range(30):
            journal.append(
                "heard",
                {"text": f"{worker}-{step}-{body}"},
                source="voice",
                trust="user_said",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, range(2)))

    lines = _lines(root)
    assert len(lines) == 60
    rows = [json.loads(line) for line in lines]
    assert sorted(row["payload"]["text"].split("-")[0] for row in rows) == (
        ["0"] * 30 + ["1"] * 30
    )


def test_new_events_restarts_a_truncated_file_from_zero(tmp_path):
    journal = Journal(tmp_path / "events")
    state_path = tmp_path / "memory" / "consolidate.json"
    journal.append(
        "heard", {"text": "перше"}, source="voice", trust="user_said",
    )
    _, state = journal.new_events(state_path)
    journal.save_state(state_path, state)
    path = next((tmp_path / "events").glob("*.jsonl"))

    path.write_text("", encoding="utf-8")
    events, state = journal.new_events(state_path)
    assert events == []
    assert state["files"][path.name] == 0
    journal.save_state(state_path, state)

    second = journal.append(
        "heard", {"text": "друге"}, source="voice", trust="user_said",
    )
    events, _ = journal.new_events(state_path)
    assert [event["id"] for event in events] == [second["id"]]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_consolidation_state_is_private(tmp_path):
    journal = Journal(tmp_path / "events")
    state_path = tmp_path / "memory" / "consolidate.json"
    journal.append(
        "heard", {"text": "привіт"}, source="voice", trust="user_said",
    )

    _, state = journal.new_events(state_path)
    journal.save_state(state_path, state)

    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700


def test_export_keeps_the_tombstone_but_not_the_forgotten_line(tmp_path):
    # This test used to assert that the forgotten line was exported too.
    # That was the leak: an export is a copy of memory, so it must not
    # hand back what a tombstone buried. The tombstone itself stays.
    journal = Journal(tmp_path)
    kept = journal.append(
        "heard", {"text": "лишається"}, source="voice", trust="user_said",
    )
    gone = journal.append(
        "heard", {"text": "перше"}, source="voice", trust="user_said",
    )
    journal.forget([gone["id"]])

    target = tmp_path / "exports" / "everything.jsonl"
    assert journal.export(target) == 2

    rows = [json.loads(line) for line in _lines(target.parent)]
    assert [row["kind"] for row in rows] == ["heard", "forget"]
    assert rows[0]["id"] == kept["id"]
    assert "перше" not in target.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


async def test_forget_clears_aggregated_text_and_export(tmp_path):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        journal=journal,
    )
    forgotten = "Унікальний маркер для забуття."
    try:
        await book.keep("user", forgotten, "text")
        await book.keep("user", "Це лишається.", "text")
        ids = [
            event["id"]
            for event in journal.events()
            if event.get("kind") == "heard"
            and event["payload"]["text"] == forgotten
        ]
        await book.forget(ids)
    finally:
        book.close()

    with sqlite3.connect(tmp_path / "heare.db") as conn:
        aggregated = [
            row[0] for row in conn.execute("SELECT aggregated_text FROM turns")
        ]
    assert all(forgotten not in text for text in aggregated)
    assert any("Це лишається." in text for text in aggregated)

    target = tmp_path / "export.jsonl"
    journal.export(target)
    exported = target.read_text(encoding="utf-8")
    assert forgotten not in exported
    assert "Це лишається." in exported


async def test_forget_removes_the_words_from_the_journal_file(tmp_path):
    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db",
        profile_dir=tmp_path / "memory",
        journal=journal,
    )
    gone = "Це просили забути."
    kept = "Це лишається."
    try:
        await book.keep("user", gone, "text")
        await book.keep("user", kept, "text")
        ids = [
            event["id"]
            for event in journal.events()
            if event.get("kind") == "heard"
            and event["payload"]["text"] == gone
        ]
        await book.forget(ids)
    finally:
        book.close()

    raw = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "events").glob("*.jsonl"))
    )
    assert gone not in raw
    assert kept in raw

    rows = list(journal.events())
    assert ids[0] not in [row["id"] for row in rows]
    tombstones = [row for row in rows if row.get("kind") == "forget"]
    assert len(tombstones) == 1
    assert tombstones[0]["payload"] == {"ids": ids, "by": "user"}
    assert gone not in json.dumps(tombstones[0], ensure_ascii=False)


def test_the_journal_stays_readable_after_a_compaction(tmp_path):
    journal = Journal(tmp_path)
    first = journal.append(
        "heard", {"text": "перше"}, source="voice", trust="user_said",
        at=datetime(2026, 10, 20, 12).timestamp(),
    )
    gone = journal.append(
        "heard", {"text": "забути"}, source="voice", trust="user_said",
        at=datetime(2026, 10, 20, 13).timestamp(),
    )
    third = journal.append(
        "heard", {"text": "третє"}, source="voice", trust="user_said",
        at=datetime(2026, 10, 21, 9).timestamp(),
    )
    gone_too = journal.append(
        "heard", {"text": "теж забути"}, source="voice", trust="user_said",
        at=datetime(2026, 10, 21, 10).timestamp(),
    )
    fourth = journal.append(
        "heard", {"text": "четверте"}, source="voice", trust="user_said",
        at=datetime(2026, 10, 21, 11).timestamp(),
    )
    before = [row["id"] for row in journal.visible_events()]

    journal.forget([gone["id"], gone_too["id"]])
    tombstone = next(
        row for row in journal.events() if row.get("kind") == "forget"
    )
    journal.compact([gone["id"], gone_too["id"]])

    assert before == [
        first["id"], gone["id"], third["id"], gone_too["id"], fourth["id"],
    ]
    assert [row["id"] for row in journal.events()] == [
        first["id"], third["id"], fourth["id"], tombstone["id"],
    ]
    assert [row["id"] for row in journal.visible_events()] == [
        first["id"], third["id"], fourth["id"],
    ]
    assert journal.tail_messages() == [
        {"role": "user", "content": text}
        for text in ("перше", "третє", "четверте")
    ]


def test_a_compaction_keeps_a_concurrent_append_whole(
    tmp_path, monkeypatch
):
    root = tmp_path / "events"
    journal = Journal(root)
    forgotten = [
        journal.append(
            "heard", {"text": f"remove-{n}"}, source="voice",
            trust="user_said",
        )
        for n in range(5)
    ]
    entered = threading.Event()
    release = threading.Event()
    original = Journal._rewrite

    def paused(self, path, data):
        entered.set()
        assert release.wait(5)
        original(self, path, data)

    monkeypatch.setattr(Journal, "_rewrite", paused)

    written: list[str] = []
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            other = Journal(root)
            for step in range(50):
                event = other.append(
                    "heard", {"text": f"keep-{step}"}, source="voice",
                    trust="user_said",
                )
                written.append(event["id"])
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    compactor = threading.Thread(
        target=journal.compact, args=([event["id"] for event in forgotten],)
    )
    compactor.start()
    assert entered.wait(5)
    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.05)
    release.set()
    compactor.join(10)
    thread.join(10)

    assert not errors
    assert len(written) == 50
    raw = _lines(root)
    rows = [json.loads(line) for line in raw]
    assert [row["id"] for row in rows] == written
    assert all(f"remove-{n}" not in "\n".join(raw) for n in range(5))


def test_a_compaction_does_not_stall_the_consolidation_cursor(tmp_path):
    journal = Journal(tmp_path / "events")
    state_path = tmp_path / "memory" / "consolidate.json"
    journal.append(
        "heard", {"text": "х" * 200}, source="voice", trust="user_said",
    )
    second = journal.append(
        "heard", {"text": "друге"}, source="voice", trust="user_said",
    )
    _, state = journal.new_events(state_path)
    journal.save_state(state_path, state)

    third = journal.append(
        "heard", {"text": "т" * 300}, source="voice", trust="user_said",
    )
    gone = next(
        event
        for event in journal.events()
        if event["payload"].get("text") == "х" * 200
    )
    journal.forget([gone["id"]])
    journal.compact([gone["id"]])

    events, _ = journal.new_events(state_path)
    assert [
        event["id"] for event in events if event.get("kind") == "heard"
    ] == [second["id"], third["id"]]


def test_a_crash_mid_compaction_leaves_the_old_journal(
    tmp_path, monkeypatch
):
    journal = Journal(tmp_path)
    kept = journal.append(
        "heard", {"text": "лишається"}, source="voice", trust="user_said",
    )
    gone = journal.append(
        "heard", {"text": "забути"}, source="voice", trust="user_said",
    )
    journal.forget([gone["id"]])
    path = next(tmp_path.glob("*.jsonl"))
    before = path.read_bytes()

    def boom(*_args, **_kwargs):
        raise OSError("power loss before the replace")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            journal.compact([gone["id"]])

    assert path.read_bytes() == before
    assert "забути" in path.read_text(encoding="utf-8")
    assert not [
        p for p in tmp_path.iterdir() if p.name.startswith(".compact-")
    ]

    assert journal.compact([gone["id"]]) == 1
    raw = path.read_text(encoding="utf-8")
    assert "забути" not in raw
    assert "лишається" in raw
    assert kept["id"] in [row["id"] for row in journal.events()]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_directory_is_0700_and_the_file_0600(tmp_path):
    journal = Journal(tmp_path / "events")
    journal.append(
        "heard", {"text": "привіт"}, source="voice", trust="user_said",
    )

    folder = tmp_path / "events"
    path = next(folder.glob("*.jsonl"))
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_full_reader_counts_the_dropped_event():
    state = State()
    reader = state.subscribe(size=1)
    state.emit("one")
    state.emit("two")
    assert state.dropped == 1
    assert reader.get_nowait().kind == "one"


async def test_run_records_only_the_system_events(tmp_path):
    journal = Journal(tmp_path)
    state = State()
    running = asyncio.create_task(journal.run(state))
    await asyncio.sleep(0)

    state.emit("heard", text="привіт", channel="voice")
    state.emit("said", text="вітаю", channel="", failed=None)
    state.emit("task", id=1, request="зроби", status="running")
    state.emit("verb", name="recall", arguments={"query": "x"})
    state.emit("permission", id=2, task=1, executor="worker", what="записати")
    state.emit("permission_closed", id=2, allowed=True)
    state.emit("overheard", text="не до мене", channel="voice")
    state.emit("around", key="front", value="Safari")
    state.emit("around", key="playing", value=["музика"])
    state.emit("phase", phase="thinking")

    for _ in range(100):
        if len(list(journal.events())) == 5:
            break
        await asyncio.sleep(0.01)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)

    rows = list(journal.events())
    assert [row["kind"] for row in rows] == [
        "task", "verb", "permission", "permission_closed", "around",
    ]
    assert rows[0]["source"] == "hands"
    assert rows[0]["trust"] == "tool_output"
    assert rows[0]["payload"] == {"id": 1, "request": "зроби", "status": "running"}
    assert rows[1]["source"] == "core"
    assert rows[2]["source"] == "brain"
    assert rows[3]["source"] == "brain"


def test_what_plays_nearby_is_kept_and_the_rest_of_around_is_not(tmp_path):
    journal = Journal(tmp_path)
    journal._record("around", {"key": "playing", "value": ["YouTube"]})
    journal._record("around", {"key": "front", "value": "Safari"})
    kept = list(journal.events())
    assert [(e["kind"], e["payload"]["value"]) for e in kept] == [("around", ["YouTube"])]
