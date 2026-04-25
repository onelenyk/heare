"""US-IND-A4b: VisualBackend writes JSONL and trims at keep_last."""
from __future__ import annotations

import json
from pathlib import Path

from src.indication import IndicationKind, IndicationLevel
from src.indication_backends.visual import VisualBackend


async def test_fire_writes_one_valid_json_line(tmp_path: Path) -> None:
    path = tmp_path / "indication.jsonl"
    backend = VisualBackend(path)

    await backend.fire(
        IndicationKind.AWAITING_CONFIRMATION,
        IndicationLevel.INPUT_WAITING,
        "title",
        "тіло",
        {"k": "v"},
    )
    contents = path.read_text(encoding="utf-8")
    lines = contents.splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "awaiting_confirmation"
    assert record["level"] == "input_waiting"
    assert record["title"] == "title"
    assert record["body"] == "тіло"
    assert isinstance(record["ts"], float)


async def test_rotation_trims_to_keep_last(tmp_path: Path) -> None:
    path = tmp_path / "indication.jsonl"
    backend = VisualBackend(path, keep_last=10)
    for i in range(25):
        await backend.fire(
            IndicationKind.HEARTBEAT_TICK,
            IndicationLevel.INFO,
            f"t{i}",
            f"b{i}",
            {},
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    # Last line should be the most recent fire.
    last = json.loads(lines[-1])
    assert last["title"] == "t24"
    assert last["body"] == "b24"
    # First retained line should be t15 (i.e. last 10 of 0..24).
    first = json.loads(lines[0])
    assert first["title"] == "t15"


async def test_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "logs" / "indication.jsonl"
    backend = VisualBackend(path)
    await backend.fire(
        IndicationKind.MODE_CHANGED,
        IndicationLevel.INFO,
        "t",
        "b",
        {},
    )
    assert path.exists()
    assert path.parent.is_dir()


async def test_aclose_is_noop(tmp_path: Path) -> None:
    backend = VisualBackend(tmp_path / "x.jsonl")
    await backend.aclose()
