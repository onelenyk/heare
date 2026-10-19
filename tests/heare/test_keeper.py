"""The beacon: what it reads, and what it keeps alive."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

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


def test_a_keeper_told_to_stop_takes_its_parts_along(tmp_path):
    """launchd stops the keeper with SIGTERM; a part left running then was
    started a second time by the next keeper."""
    pidfile = tmp_path / "part.pid"
    folder = tmp_path / "parts" / "sleeper"
    folder.mkdir(parents=True)
    code = (f"import os, pathlib, time; pathlib.Path({str(pidfile)!r})"
            ".write_text(str(os.getpid())); time.sleep(60)")
    (folder / "heare.json").write_text(json.dumps({
        "channel": "sleeper", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")
    boss = subprocess.Popen([sys.executable, "-c",
                             "import asyncio, sys; from pathlib import Path; "
                             "from heare.keeper import kept; "
                             "asyncio.run(kept(Path(sys.argv[1])))",
                             str(tmp_path / "parts")])
    try:
        for _ in range(250):
            if pidfile.exists() and pidfile.read_text():
                break
            time.sleep(0.02)
        part = int(pidfile.read_text())
        boss.send_signal(signal.SIGTERM)
        assert boss.wait(timeout=10) == 0
        for _ in range(250):
            try:
                os.kill(part, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            os.kill(part, signal.SIGKILL)
            raise AssertionError("the part outlived its keeper")
    finally:
        if boss.poll() is None:
            boss.kill()
