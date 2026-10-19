"""The page, checked without a browser: its script parses, every element it
reaches for exists, and it can draw every control the protocol names.

Two of 19 October's page faults were found only by eye; these are the
cheap checks that would have caught their kind."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = (Path(__file__).resolve().parents[2]
        / "packages/heare-brain/heare/api/page.html").read_text(encoding="utf-8")
SCRIPT = "\n".join(re.findall(r"<script>(.*?)</script>", PAGE, re.S))
# The kinds of control protocol.md §5 promises a part can declare.
KINDS = ("toggle", "slider", "choice", "label", "button", "log", "text")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_script_parses(tmp_path):
    script = tmp_path / "page.js"
    script.write_text(SCRIPT, encoding="utf-8")
    checked = subprocess.run(["node", "--check", str(script)],
                             capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_every_element_the_script_reaches_for_is_in_the_page():
    wanted = set(re.findall(r'\$\("([\w-]+)"\)', SCRIPT))
    present = set(re.findall(r'\bid="([\w-]+)"', PAGE))
    assert wanted - present == set()


@pytest.mark.parametrize("kind", KINDS)
def test_the_page_draws_every_kind_of_control(kind):
    assert f'c.kind === "{kind}"' in SCRIPT
