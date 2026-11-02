"""The fence holds where the machine can hold it, and says when it cannot."""

from __future__ import annotations

import subprocess
import sys

import pytest

from heare import fence

mac = pytest.mark.skipif(sys.platform != "darwin", reason="the macOS fence")


def _run(argv, work, home):
    wrapped = fence.wrap(argv, work, home=home)
    return subprocess.run(wrapped, capture_output=True, text=True, timeout=20)


def test_no_fence_is_said_not_pretended(tmp_path):
    assert fence.wrap(["ls"], tmp_path, platform="plan9") is None


def test_linux_hides_secrets_and_cuts_the_network(tmp_path, monkeypatch):
    (tmp_path / ".ssh").mkdir()
    monkeypatch.setattr(fence.shutil, "which", lambda name: "/usr/bin/bwrap")
    line = fence.wrap(["ls"], tmp_path, home=tmp_path, platform="linux")
    assert "--unshare-net" in line and str(tmp_path / ".ssh") in line
    assert "--unshare-net" not in fence.wrap(
        ["ls"], tmp_path, home=tmp_path, platform="linux", net=True)


@mac
def test_writing_stays_in_the_work_folder(tmp_path):
    home = tmp_path / "home"
    work = home / "work"
    outside = home / "out"
    outside.mkdir(parents=True)
    assert _run(["touch", str(work / "a")], work, home).returncode == 0
    assert _run(["touch", str(outside / "b")], work, home).returncode != 0
    assert not (outside / "b").exists()


@mac
def test_a_secret_cannot_be_read(tmp_path):
    home = tmp_path.resolve() / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id").write_text("x")
    assert _run(["cat", str(home / ".ssh" / "id")], home / "w", home).returncode != 0


@mac
def test_no_network_unless_asked(tmp_path):
    line = fence.wrap(["/usr/bin/nc", "-z", "-w", "2", "1.1.1.1", "443"], tmp_path)
    assert subprocess.run(line, capture_output=True, timeout=20).returncode != 0
