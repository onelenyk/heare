"""The beacon: what it reads, and what it keeps alive."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

from heare import keeper, machine, part

# A keeper in another process: it takes the lock, keeps the parts, and exits
# with kept's code.
KEEP = (
    "import asyncio, sys\n"
    "from pathlib import Path\n"
    "from heare.keeper import kept\n"
    "sys.exit(asyncio.run(kept(Path(sys.argv[1]))))\n"
)


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _wait_for(predicate, tries: int = 250) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        time.sleep(0.02)
    return False


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


def test_a_second_keeper_does_not_start_a_second_copy(tmp_path):
    folder = tmp_path / "parts"
    folder.mkdir()
    lock = tmp_path / "keeper.lock"
    first = subprocess.Popen([sys.executable, "-c", KEEP, str(folder)],
                             stderr=subprocess.PIPE, text=True)
    try:
        assert _wait_for(lambda: lock.exists() and lock.read_text().strip() == str(first.pid))
        second = subprocess.run([sys.executable, "-c", KEEP, str(folder)],
                                capture_output=True, text=True, timeout=10)
    finally:
        first.send_signal(signal.SIGTERM)
        first.wait(timeout=10)
        first.stderr.close()

    assert second.returncode != 0
    assert str(first.pid) in second.stderr


async def test_a_stale_keeper_pidfile_does_not_block_a_new_keeper(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.01)
    folder = tmp_path / "parts"
    folder.mkdir()
    lock = tmp_path / "keeper.lock"
    lock.write_text("999999", encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(folder).run())
    try:
        for _ in range(200):
            if lock.read_text().strip() == str(os.getpid()):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("a dead pid kept the next keeper out")
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert lock.read_text().strip() == str(os.getpid())


def test_a_stopped_keeper_kills_the_whole_part_group(tmp_path):
    own = tmp_path / "part.pid"
    kid = tmp_path / "child.pid"
    folder = tmp_path / "parts" / "tree"
    folder.mkdir(parents=True)
    code = (
        "import os, pathlib, subprocess, sys, time\n"
        f"pathlib.Path({str(own)!r}).write_text(str(os.getpid()))\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(kid)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    (folder / "heare.json").write_text(json.dumps({
        "channel": "tree", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")
    boss = subprocess.Popen([sys.executable, "-c", KEEP, str(tmp_path / "parts")],
                            stderr=subprocess.PIPE, text=True)
    try:
        assert _wait_for(lambda: own.exists() and kid.exists()
                         and own.read_text() and kid.read_text())
        part_pid, child_pid = int(own.read_text()), int(kid.read_text())
        boss.send_signal(signal.SIGTERM)
        assert boss.wait(timeout=10) == 0
        for pid in (part_pid, child_pid):
            if not _wait_for(lambda: _gone(pid)):
                os.kill(pid, signal.SIGKILL)
                raise AssertionError(f"{pid} outlived its keeper's group")
    finally:
        if boss.poll() is None:
            boss.kill()
        boss.stderr.close()


async def test_a_role_change_restarts_the_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.02)
    monkeypatch.setattr(keeper, "BACKOFF_S", 0.02)
    config = tmp_path / "settings" / "config.toml"
    config.parent.mkdir()
    config.write_text('role = "body"\n', encoding="utf-8")
    folder = tmp_path / "machine" / "parts"
    home = folder / "tick"
    home.mkdir(parents=True)
    marks = tmp_path / "marks"
    code = (f"import os, pathlib, time; pathlib.Path({str(marks)!r})"
            ".open('a').write(str(os.getpid()) + '\\n'); time.sleep(60)")
    (home / "heare.json").write_text(json.dumps({
        "channel": "tick", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(folder).run())
    try:
        for _ in range(200):
            if marks.exists() and len(marks.read_text().splitlines()) == 1:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the first part did not start")
        config.write_text('role = "all"\n', encoding="utf-8")
        for _ in range(200):
            if marks.exists() and len(marks.read_text().splitlines()) >= 2:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the part was not restarted after the role change")
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    first, second = marks.read_text().splitlines()[:2]
    assert first != second


async def test_a_broken_manifest_command_is_retried_with_backoff_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.02)
    monkeypatch.setattr(keeper, "BACKOFF_S", 0.02)
    monkeypatch.setattr(keeper, "BACKOFF_MAX_S", 0.04)
    folder = tmp_path / "parts"
    home = folder / "broken"
    home.mkdir(parents=True)
    (home / "heare.json").write_text(json.dumps({
        "channel": "broken", "run": ["/nonexistent/heare-part-xyz"]}),
        encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(folder).run())
    try:
        for _ in range(200):
            state = machine.health(folder).get("broken") or {}
            if state.get("state") == "unwell" and int(state.get("attempts") or 0) >= 3:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("the keeper did not retry the broken part")
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert "heare-part-xyz" in state["why"]
    assert int(state["attempts"]) < 40


async def test_a_rejected_part_is_not_restarted_in_a_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.02)
    monkeypatch.setattr(keeper, "BACKOFF_S", 0.02)
    folder = tmp_path / "parts"
    home = folder / "eyes"
    home.mkdir(parents=True)
    marks = tmp_path / "marks"
    code = ("import pathlib, sys\n"
            f"pathlib.Path({str(marks)!r}).open('a').write('x\\n')\n"
            "from heare import part\n"
            "sys.exit(part.EXIT_REJECTED)\n")
    (home / "heare.json").write_text(json.dumps({
        "channel": "eyes", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(folder).run())
    try:
        for _ in range(250):
            state = machine.health(folder).get("eyes") or {}
            if state.get("state") == "unwell" and marks.exists():
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the rejected part was not recorded as unwell")
        await asyncio.sleep(0.3)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert str(part.EXIT_REJECTED) in state["why"]
    assert marks.read_text().splitlines() == ["x"]


async def test_a_rejected_part_starts_again_after_a_settings_change(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.02)
    config = tmp_path / "settings" / "config.toml"
    config.parent.mkdir()
    config.write_text('role = "body"\n', encoding="utf-8")
    folder = tmp_path / "machine" / "parts"
    home = folder / "eyes"
    home.mkdir(parents=True)
    marks = tmp_path / "marks"
    code = ("import pathlib, sys\n"
            f"pathlib.Path({str(marks)!r}).open('a').write('x\\n')\n"
            "from heare import part\n"
            "sys.exit(part.EXIT_REJECTED)\n")
    (home / "heare.json").write_text(json.dumps({
        "channel": "eyes", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(folder).run())
    try:
        for _ in range(250):
            if marks.exists() and len(marks.read_text().splitlines()) == 1:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the rejected part did not start")
        await asyncio.sleep(0.2)
        assert len(marks.read_text().splitlines()) == 1
        config.write_text('role = "all"\n', encoding="utf-8")
        for _ in range(250):
            if len(marks.read_text().splitlines()) >= 2:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the parked part did not start after a settings change")
        await asyncio.sleep(0.2)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert len(marks.read_text().splitlines()) == 2


async def test_a_rotated_brain_token_restarts_a_parked_part(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.02)
    config = tmp_path / "settings" / "config.toml"
    config.parent.mkdir()
    config.write_text('role = "body"\n', encoding="utf-8")
    token = tmp_path / "machine" / "api_token"
    token.parent.mkdir()
    token.write_text("old", encoding="utf-8")
    folder = tmp_path / "machine" / "parts"
    home = folder / "eyes"
    home.mkdir(parents=True)
    marks = tmp_path / "marks"
    code = ("import pathlib, sys\n"
            f"pathlib.Path({str(marks)!r}).open('a').write('x\\n')\n"
            "from heare import part\n"
            "sys.exit(part.EXIT_REJECTED)\n")
    (home / "heare.json").write_text(json.dumps({
        "channel": "eyes", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(folder).run())
    try:
        for _ in range(250):
            if marks.exists() and len(marks.read_text().splitlines()) == 1:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the rejected part did not start")
        await asyncio.sleep(0.2)
        token.write_text("rotated", encoding="utf-8")
        for _ in range(250):
            if len(marks.read_text().splitlines()) >= 2:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the parked part did not start after the token changed")
        await asyncio.sleep(0.2)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert len(marks.read_text().splitlines()) == 2


async def test_an_edited_manifest_restarts_a_parked_part(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.02)
    folder = tmp_path / "parts"
    home = folder / "eyes"
    home.mkdir(parents=True)
    marks = tmp_path / "marks"
    manifest = home / "heare.json"
    old_code = ("import os, pathlib, sys\n"
                f"pathlib.Path({str(marks)!r}).open('a').write(str(os.getpid()) + '\\n')\n"
                "from heare import part\n"
                "sys.exit(part.EXIT_REJECTED)\n")
    manifest.write_text(json.dumps({
        "channel": "eyes", "run": [sys.executable, "-c", old_code]}),
        encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(folder).run())
    try:
        for _ in range(250):
            if marks.exists() and len(marks.read_text().splitlines()) == 1:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the rejected part did not start")
        await asyncio.sleep(0.2)
        old_pid = int(marks.read_text().splitlines()[0])
        new_code = ("import os, pathlib, time\n"
                    f"pathlib.Path({str(marks)!r}).open('a').write(str(os.getpid()) + '\\n')\n"
                    "time.sleep(60)\n")
        manifest.write_text(json.dumps({
            "channel": "eyes", "run": [sys.executable, "-c", new_code]}),
            encoding="utf-8")
        for _ in range(250):
            if len(marks.read_text().splitlines()) >= 2:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the parked part did not start after the manifest changed")
        new_pid = int(marks.read_text().splitlines()[1])
        assert new_pid != old_pid
        assert not _gone(new_pid)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert marks.read_text().splitlines() == [str(old_pid), str(new_pid)]
    assert _gone(old_pid)


async def test_a_brain_that_refused_the_key_is_parked_not_looped(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper, "POLL_S", 0.02)
    monkeypatch.setattr(keeper, "BACKOFF_S", 0.02)
    folder = tmp_path / "parts"
    home = folder / "eyes"
    home.mkdir(parents=True)
    marks = tmp_path / "marks"
    code = ("import pathlib, sys\n"
            f"pathlib.Path({str(marks)!r}).open('a').write('x\\n')\n"
            "from heare import part\n"
            "sys.exit(part.EXIT_REFUSED)\n")
    (home / "heare.json").write_text(json.dumps({
        "channel": "eyes", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")

    running = asyncio.create_task(keeper.Keeper(folder).run())
    try:
        for _ in range(250):
            state = machine.health(folder).get("eyes") or {}
            if state.get("state") == "unwell" and marks.exists():
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the refused part was not recorded as unwell")
        await asyncio.sleep(0.3)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert str(part.EXIT_REFUSED) in state["why"]
    assert marks.read_text().splitlines() == ["x"]


async def test_a_machine_its_brain_forgot_stops_its_parts(tmp_path, monkeypatch):
    """The brain cannot knock on a forgotten machine: the keeper learns from
    the refused report, stops what it raises, and says why. A setting is not
    a new trust; a new pairing is, and only then do the parts come back."""
    monkeypatch.setattr(keeper, "POLL_S", 0.02)
    monkeypatch.setattr(keeper, "BACKOFF_S", 0.02)
    config = tmp_path / "settings" / "config.toml"
    config.parent.mkdir()
    config.write_text('role = "body"\n', encoding="utf-8")
    folder = tmp_path / "machine" / "parts"
    home = folder / "eyes"
    home.mkdir(parents=True)
    marks = tmp_path / "marks"
    code = ("import os, pathlib, time\n"
            f"pathlib.Path({str(marks)!r}).open('a').write(str(os.getpid()) + '\\n')\n"
            "time.sleep(60)\n")
    (home / "heare.json").write_text(json.dumps({
        "channel": "eyes", "run": [sys.executable, "-c", code]}),
        encoding="utf-8")

    why = "the brain no longer knows this machine — pair it again"
    beacon = keeper.Keeper(folder, config)
    running = asyncio.create_task(beacon.run())
    try:
        for _ in range(250):
            if marks.exists() and marks.read_text().strip():
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("the part did not start")
        old = int(marks.read_text().splitlines()[0])

        beacon.halt(why)
        for _ in range(250):
            if _gone(old):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("a forgotten machine kept its part running")
        assert machine.health(folder)["eyes"]["why"] == why

        # A setting changed after the refusal is not a new pairing.
        config.write_text('role = "all"\n', encoding="utf-8")
        await asyncio.sleep(0.3)
        assert len(marks.read_text().splitlines()) == 1, \
            "a setting started the parts again under the refused key"

        # A pairing is: the machine may knock again, so the parts come back.
        (tmp_path / "machine" / "machine_token").write_text("new", encoding="utf-8")
        for _ in range(250):
            if len(marks.read_text().splitlines()) >= 2:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("a paired again machine did not start its parts")
        assert _gone(old)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


def test_the_brain_keeper_finds_the_master_token(tmp_path):
    home = tmp_path
    (home / "machine").mkdir()
    (home / "machine" / "api_token").write_text("master", encoding="utf-8")
    (home / "settings").mkdir()
    config = home / "settings" / "config.toml"
    config.write_text("", encoding="utf-8")

    assert keeper._settings(config, home).secret == "master"
