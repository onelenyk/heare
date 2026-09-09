"""Every setting must be read by something.

A setting that can be written into ``config.toml``, is loaded faithfully
into ``Settings``, and is then looked at by no line of code is worse than
a missing feature: the person tuning an assistant by ear cannot tell "I
changed it and it made no difference" from "changing it does nothing".

Thirty-six of them were found this way on 9 September 2026, most left
behind by the pipecat engine deleted on 17 August — a whole ``[audio]``
section of VAD thresholds, six echo-gate knobs, three provider endpoints
duplicating the live table in ``src/agent/llm/providers.py``. They are
listed in ``config.RETIRED`` now, which is what a stale ``config.toml``
gets warned against.

This is the ratchet on the next one. It is the same shape as
``tests/test_core_boundary.py``: the property held by discipline for
months, and discipline is not a check.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

import pytest

from src.config import RETIRED, Settings

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Read by nothing yet, on purpose, each with the reason it is not simply
# deleted. Adding a name here is allowed and is meant to be uncomfortable:
# it is a promise recorded in a file, not a shrug.
NOT_YET_WIRED: dict[str, str] = {
    # src/spine/aec.py hard-codes _STREAM_DELAY_MS = 30 under a comment
    # claiming it matches "the daemon's default configuration", which
    # stopped being true when this was raised to a measured ~125 ms.
    # Wiring it changes what the room sounds like, so it needs an ear.
    "aec_stream_delay_ms": "spine/aec.py hard-codes 30; needs a live listen",
    "aec_noise_suppression": "spine/aec.py hard-codes False, deliberately",
}


def _searchable_text() -> str:
    """Everything that could plausibly read a setting, minus config.py.

    Deliberately includes the frontend and the docs: a name reaching the
    dashboard as a string is a reader, and so is a finding that explains
    why the value is what it is.
    """
    skip_dirs = {"__pycache__", "node_modules", "dist", ".venv", ".git"}
    suffixes = {".py", ".js", ".jsx", ".ts", ".tsx", ".toml", ".md", ".json", ".html"}
    chunks: list[str] = []
    for base in ("src", "tests", "scripts", "deploy", "docs"):
        for path in (ROOT / base).rglob("*"):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if set(path.parts) & skip_dirs:
                continue
            if path == ROOT / "src" / "config.py":
                continue
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


TEXT = _searchable_text()
FIELDS = sorted(f.name for f in dataclasses.fields(Settings))


@pytest.mark.parametrize("name", FIELDS)
def test_a_setting_has_a_reader(name: str) -> None:
    """One assertion per field, so a failure names the setting.

    Read the failure as a question: either wire it up, delete it into
    ``RETIRED``, or say in ``NOT_YET_WIRED`` why it waits.
    """
    if name in NOT_YET_WIRED:
        pytest.skip(f"declared unwired: {NOT_YET_WIRED[name]}")
    assert re.search(rf"\b{re.escape(name)}\b", TEXT), (
        f"Settings.{name} is written by config.toml and read by nothing. "
        "Wire it, retire it into config.RETIRED, or record why it waits "
        "in NOT_YET_WIRED above."
    )


def test_retired_names_are_really_gone() -> None:
    """A name in both places is the worst of both: it still loads, and it
    warns that it does not."""
    live = {f.name for f in dataclasses.fields(Settings)}
    assert not (live & RETIRED.keys())


def test_every_retired_name_says_what_to_do_instead() -> None:
    """The warning is only worth printing if it carries an answer."""
    empty = [name for name, why in RETIRED.items() if not why.strip()]
    assert not empty, f"RETIRED entries with no reason: {empty}"


def test_the_unwired_list_stays_short() -> None:
    """It is an exceptions list. An exceptions list that grows is a
    default."""
    assert len(NOT_YET_WIRED) <= 4
