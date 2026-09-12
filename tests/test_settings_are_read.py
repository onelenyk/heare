"""Every setting must be read by something.

A setting that can be written into ``config.toml``, is loaded faithfully
into ``Settings``, and is then looked at by no line of code is worse than
a missing feature: the person tuning an assistant by ear cannot tell "I
changed it and it made no difference" from "changing it does nothing".

Thirty-one of them were found this way on 9 September 2026, most left
behind by the pipecat engine deleted on 17 August — a whole ``[audio]``
section of VAD thresholds, six echo-gate knobs, the turn_end mode
selector. They are listed in ``config.RETIRED`` now, which is what a stale
``config.toml`` gets warned against.

Six more were retired the same day by mistake and put back the next: the
provider endpoints and models, which are read through
``getattr(settings, f"{name}_model")`` and never spelled out. A text
search finds no reader for a name that is only ever assembled. That is
what ``_built_names()`` below is for.

This is the ratchet on the next one. It is the same shape as
``tests/test_core_boundary.py``: the property held by discipline for
months, and discipline is not a check.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import re

import pytest

from src.config import RETIRED, Settings

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _built_names() -> dict[str, str]:
    """Attribute names assembled at runtime: regex -> where.

    ``getattr(settings, f"{name}_model")`` reads ``openrouter_model``
    without the word ever appearing in the source. A text search cannot
    see that read, and on 9 September it did not: six provider fields
    were retired as unread, and the dashboard's model picker — which
    writes them the same way — lost its choice on every restart until
    the next day. This turns each such f-string into a pattern, so a
    field it could produce counts as read.
    """
    found: dict[str, str] = {}
    for path in (ROOT / "src").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("getattr", "setattr", "hasattr")
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.JoinedStr)
            ):
                continue
            pattern = "".join(
                # A built segment is a provider or device kind — never
                # something with an underscore in it. `\w+` would let
                # f"{provider}_model" claim every name ending in _model.
                re.escape(part.value) if isinstance(part, ast.Constant) else r"[A-Za-z0-9]+"
                for part in node.args[1].values
            )
            found.setdefault(pattern, f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


BUILT = _built_names()

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
    if any(re.fullmatch(pattern, name) for pattern in BUILT):
        return  # read by a name built at runtime — see _built_names()
    assert re.search(rf"\b{re.escape(name)}\b", TEXT), (
        f"Settings.{name} is written by config.toml and read by nothing. "
        "Wire it, retire it into config.RETIRED, or record why it waits "
        "in NOT_YET_WIRED above."
    )


def test_nothing_retired_is_read_by_a_built_name() -> None:
    """The check that would have stopped the 9 September mistake.

    A retired name that some ``getattr(x, f"...")`` can still produce is
    not retired — it is a setting whose loader was taken away while its
    readers kept asking, and every one of them now silently gets the
    default.
    """
    still_asked = {
        name: where
        for name in RETIRED
        for pattern, where in BUILT.items()
        if re.fullmatch(pattern, name)
    }
    assert not still_asked, f"retired, but still read by a built name: {still_asked}"


def test_the_scanner_sees_the_reads_it_was_written_for() -> None:
    """If this fails the scanner is blind again, and every other test in
    this file is quietly back to trusting a text search."""
    assert any(re.fullmatch(p, "openrouter_model") for p in BUILT)
    assert any(re.fullmatch(p, "zai_base_url") for p in BUILT)


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
