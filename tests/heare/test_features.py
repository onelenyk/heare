"""The feature registry: what it lists, what an action changes, and the
work that outlives the request that started it."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from heare.config import Settings
from heare.features import Feature, Features, build_features


def _recorder() -> tuple[list[dict], object]:
    events: list[dict] = []

    def emit(kind: str, **data: object) -> None:
        events.append({"kind": kind, **data})

    return events, emit


def _feature(id: str = "demo", *, group: str = "memory", order: int = 10,
             title: str = "Демо", state: str = "ready", note: str = "",
             read=None, write=None) -> Feature:
    async def noop(name: str, value: object) -> None: ...

    return Feature(
        id=id, title=title, about="Тестова фіча.", group=group, order=order,
        read=read or (lambda: {"state": state, "note": note, "controls": []}),
        write=write or noop,
    )


def test_list_has_only_used_groups_sorted_by_order_and_title():
    _, emit = _recorder()
    features = Features(emit)
    features.add(_feature(id="z", order=10, title="Яблуко"))
    features.add(_feature(id="second", order=20, title="Друга"))
    features.add(_feature(id="a", order=10, title="Альфа"))

    listed = features.list()

    assert listed["groups"] == [{"id": "memory", "title": "Памʼять",
                                 "order": 10}]
    assert [item["id"] for item in listed["features"]] == [
        "a", "z", "second"]


def test_a_broken_feature_does_not_hide_the_others():
    _, emit = _recorder()
    features = Features(emit)

    def broken() -> dict[str, object]:
        raise RuntimeError("boom")

    features.add(_feature(id="bad", read=broken))
    features.add(_feature(id="good", note="добре"))

    listed = features.list()
    bad, good = listed["features"]

    assert bad["state"] == "error"
    assert bad["note"] == "не вдалося прочитати"
    assert good["state"] == "ready"
    assert good["note"] == "добре"


async def test_an_action_updates_the_view_and_an_unknown_one_is_refused():
    events, emit = _recorder()
    features = Features(emit)
    number = {"value": 1}

    def read() -> dict[str, object]:
        return {"state": "ready", "note": str(number["value"]),
                "controls": []}

    async def write(name: str, value: object) -> None:
        if name != "bump":
            raise ValueError(f"unknown action: {name}")
        number["value"] += int(value)

    features.add(_feature(id="demo", read=read, write=write))

    answer = await features.act("demo", "bump", 2)

    assert answer["ok"] is True
    assert answer["feature"]["note"] == "3"
    assert events == [{"kind": "feature", "id": "demo", "state": "changed"}]
    with pytest.raises(ValueError):
        await features.act("demo", "nope", None)


async def test_background_work_runs_once_and_reports_both_ends():
    events, emit = _recorder()
    features = Features(emit)
    features.add(_feature(id="demo"))
    started, release = asyncio.Event(), asyncio.Event()

    async def work() -> None:
        started.set()
        await release.wait()

    assert features.start("demo", work) is True
    await started.wait()
    task = features._running["demo"]

    assert features.start("demo", work) is False
    assert features.list()["features"][0]["state"] == "running"

    release.set()
    await task

    assert task.done()
    assert [event["state"] for event in events] == ["running", "done"]


async def test_a_failure_is_a_note_until_the_next_success():
    _, emit = _recorder()
    features = Features(emit)
    features.add(_feature(id="demo"))

    async def boom() -> None:
        raise RuntimeError("зламалося")

    features.start("demo", boom)
    task = features._running["demo"]
    await task

    broken = features.list()["features"][0]
    assert broken["note"] == "зламалося"

    await features.act("demo", "anything", None)

    assert features.list()["features"][0]["note"] == ""


class _Brain:
    def __init__(self, tmp_path) -> None:
        self.settings = Settings(
            path=tmp_path / "config.toml", memory_dir=tmp_path / "memory")
        self.core = SimpleNamespace(
            state=SimpleNamespace(emit=lambda kind, **data: None))
        self.reviewed = 0
        self.consolidated = 0

    async def review_once(self) -> None:
        self.reviewed += 1

    async def consolidate_once(self) -> None:
        self.consolidated += 1


def test_the_first_slice_has_two_memory_features(tmp_path):
    features = build_features(_Brain(tmp_path))

    listed = features.list()

    assert [item["id"] for item in listed["features"]] == [
        "review", "consolidation"]
    review = listed["features"][0]
    assert review["title"] == "Щоденний огляд"
    assert review["group"] == "memory" and review["order"] == 10
    assert review["state"] == "ready" and review["note"] == "ще не було"
    assert [control["name"] for control in review["controls"]] == [
        "enabled", "hour", "run", "last"]
    hour = review["controls"][1]
    assert hour == {
        "kind": "slider", "name": "hour", "label": "година", "value": 4,
        "min": 0, "max": 23, "step": 1, "unit": "год",
    }
    consolidation = listed["features"][1]
    assert consolidation["title"] == "Згортка"
    assert consolidation["note"] == "ще не було"
    assert [control["name"] for control in consolidation["controls"]] == [
        "run", "last"]


async def test_review_settings_are_validated_and_saved(tmp_path):
    brain = _Brain(tmp_path)
    features = build_features(brain)

    view = await features.act("review", "hour", "5")
    assert brain.settings.review_hour == 5
    assert view["feature"]["controls"][1]["value"] == 5

    off = await features.act("review", "enabled", False)
    assert brain.settings.review_enabled is False
    assert off["feature"]["state"] == "off"
    assert off["feature"]["note"] == "вимкнено"

    for bad in (True, 24, -1, 4.5, "x"):
        with pytest.raises(ValueError):
            await features.act("review", "hour", bad)
    with pytest.raises(ValueError):
        await features.act("review", "enabled", "true")


async def test_the_run_action_starts_the_shared_pass(tmp_path):
    brain = _Brain(tmp_path)
    features = build_features(brain)

    running = await features.act("review", "run", True)
    task = features._running["review"]
    assert running["feature"]["state"] == "running"
    await task

    running = await features.act("consolidation", "run", True)
    task = features._running["consolidation"]
    await task

    assert brain.reviewed == 1
    assert brain.consolidated == 1


async def test_a_failed_review_is_kept_as_a_note(tmp_path):
    from heare.memory.contracts import ReviewReport

    brain = _Brain(tmp_path)

    async def failed() -> ReviewReport:
        return ReviewReport(error="model is down")

    brain.review_once = failed
    features = build_features(brain)

    await features.act("review", "run", True)
    task = features._running["review"]
    await task

    assert features.list()["features"][0]["note"] == "model is down"
