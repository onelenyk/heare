"""A machine's parts are named after the name it was paired under.

5 November: a reboot turned the Mac's network name MBP-M4--Nazar.lan into
MacBook-Pro-M4--Nazar.local. Every part knocked as voice@<the new name>,
the brain answered «not your name», and the Mac went deaf and mute with a
valid key. The name of the day is the router's; the paired one is ours.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path

from heare.config import Settings, machine_host

ROOT = Path(__file__).resolve().parents[2]


def test_a_paired_machine_keeps_its_name_whatever_the_network_says(tmp_path, monkeypatch):
    (tmp_path / "machine_name").write_text("MBP-M4--Nazar.lan\n")
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro-M4--Nazar.local")
    settings = Settings(tmp_path / "config.toml", machine_name_file=tmp_path / "machine_name")
    assert settings.host == "MBP-M4--Nazar.lan"


def test_a_machine_never_paired_is_called_what_its_host_is(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "brain-box")
    assert machine_host(tmp_path / "nothing") == "brain-box"


def test_no_part_names_itself_after_the_network():
    """The rule, for every part to come: a part's key and host come from
    the machine's paired name, never from socket.gethostname()."""
    offenders = []
    for path in ROOT.glob("packages/*/heare/**/*.py"):
        if "heare-brain" in path.parts or path.name in {"config.py", "setup.py"}:
            continue  # the brain is its own machine; config holds the fallback
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "attr", "") == "gethostname"
                    and any(getattr(t, "id", "") == "host" for t in node.targets)):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, f"host from the network, not the pairing: {offenders}"
