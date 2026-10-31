"""install.sh on a Mac and for a server: one install path on a Mac, sums for
URL wheels, staged tools, and keys that never reach a command line."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "install.sh"


def _script() -> str:
    return INSTALL.read_text(encoding="utf-8")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(INSTALL), *args], capture_output=True, text=True,
        env={**os.environ, "HEARE_LANG": "en"})


def test_sh_syntax_is_clean():
    subprocess.run(["sh", "-n", str(INSTALL)], check=True)


def test_darwin_refuses_and_names_the_app():
    if sys.platform != "darwin":
        return
    done = _run("--role", "all")
    said = done.stdout + done.stderr

    assert done.returncode == 1
    assert "mac" in said.lower() and "app" in said.lower()
    assert "make live" in said
    assert "launchd" not in said.lower()


def test_the_help_names_one_mac_path():
    done = _run("--help")
    said = done.stdout + done.stderr

    assert done.returncode == 0
    assert "a Mac: the brain and the parts here" not in said
    assert said.count("make live") == 1
    assert said.count("Mac") == 1


def test_no_launchd_line_can_run_on_a_mac():
    script = _script()

    assert "launchctl" not in script
    assert "launchd" not in script
    assert "LaunchAgents" not in script
    assert "plist com.heare" not in script


def test_keys_are_not_accepted_on_the_command_line():
    script = _script()
    done = _run("--deepseek-key", "secret-value", "--help")

    assert "--deepseek-key" not in script
    assert "--groq-key" not in script
    assert "--keys-file" in script
    assert "HEARE_DEEPSEEK_KEY" in script and "HEARE_GROQ_KEY" in script
    assert done.returncode == 1
    assert "unknown flag" in done.stderr
    assert "secret-value" not in done.stdout + done.stderr


def test_url_wheels_are_checked_against_the_release_sums():
    script = _script()

    assert "SHA256SUMS.txt" in script
    assert "sum_missing" in script and "sum_wrong" in script
    assert "verify_wheel" in script


def test_tools_are_staged_before_the_swap_and_the_previous_kept():
    script = _script()

    assert 'UV_TOOL_DIR="$STAGE_TOOLS"' in script
    assert 'UV_TOOL_BIN_DIR="$STAGE_BIN"' in script
    assert "heare-tool-previous" in script
    assert "restore_tools" in script
