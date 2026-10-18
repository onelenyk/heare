"""The beacon: what it reads, and what it keeps alive."""

from __future__ import annotations

import asyncio
import json
import sys

from heare import keeper


def test_a_manifest_names_the_command_to_run(tmp_path):
    folder = tmp_path / "parts"
    (folder / "eyes").mkdir(parents=True)
    (folder / "eyes" / "heare.json").write_text(json.dumps({
        "channel": "eyes", "run": ["python", "-m", "heare.eyes"]}),
        encoding="utf-8")
    (folder / "quiet").mkdir()
    (folder / "quiet" / "heare.json").write_text(
        json.dumps({"channel": "quiet"}), encoding="utf-8")
    (folder / "broken").mkdir()
    (folder / "broken" / "heare.json").write_text("not json", encoding="utf-8")

    assert keeper.wanted(folder) == {"eyes": ["python", "-m", "heare.eyes"]}


def test_python_in_a_manifest_is_the_beacon_s_own():
    assert keeper._resolve(["python", "-m", "x"])[0] == sys.executable
    assert keeper._resolve(["echo", "x"]) == ["echo", "x"]


async def test_a_fallen_part_is_started_again(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.05)
    monkeypatch.setattr(keeper, "BACKOFF_S", 0.05)
    marks = tmp_path / "marks"
    folder = tmp_path / "parts" / "tick"
    folder.mkdir(parents=True)
    code = f"import pathlib; pathlib.Path({str(marks)!r}).open('a').write('x')"
    (folder / "heare.json").write_text(json.dumps({
        "channel": "tick", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(tmp_path / "parts").run())
    try:
        for _ in range(200):
            if marks.exists() and len(marks.read_text()) >= 2:
                break
            await asyncio.sleep(0.02)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert len(marks.read_text()) >= 2
