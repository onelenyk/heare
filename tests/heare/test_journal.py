"""The journal: what is written, what is read back, what is forgotten."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from datetime import datetime

import pytest

from heare.core.state import State
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


def test_export_writes_every_event_including_forget(tmp_path):
    journal = Journal(tmp_path)
    heard = journal.append(
        "heard", {"text": "перше"}, source="voice", trust="user_said",
    )
    journal.forget([heard["id"]])

    target = tmp_path / "exports" / "everything.jsonl"
    assert journal.export(target) == 2

    rows = [json.loads(line) for line in _lines(target.parent)]
    assert [row["kind"] for row in rows] == ["heard", "forget"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


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
    state.emit("around", key="playing", value=["музика"])
    state.emit("phase", phase="thinking")

    for _ in range(100):
        if len(list(journal.events())) == 4:
            break
        await asyncio.sleep(0.01)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)

    rows = list(journal.events())
    assert [row["kind"] for row in rows] == [
        "task", "verb", "permission", "permission_closed",
    ]
    assert rows[0]["source"] == "hands"
    assert rows[0]["trust"] == "tool_output"
    assert rows[0]["payload"] == {"id": 1, "request": "зроби", "status": "running"}
    assert rows[1]["source"] == "core"
    assert rows[2]["source"] == "brain"
    assert rows[3]["source"] == "brain"
