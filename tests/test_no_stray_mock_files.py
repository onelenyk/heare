"""Nothing may write into the repository root because a mock leaked.

A ``MagicMock`` answers any attribute with another mock, so a fixture
that forgets one field hands downstream code an object that is *almost*
what it wanted. When that field is a filesystem path and the code opens
it, SQLite dutifully creates a file named after the mock's repr.

That is how 2702 files called ``<MagicMock name='mock.db_path' id='…'>``
accumulated in this repository between 3 June and 5 September. Nobody
saw them because someone had added ``<MagicMock*`` to ``.gitignore`` —
the symptom was suppressed, so the cause was never looked at.

These two tests guard the two halves of that: the fixture must hand out
a real path, and the root must stay clean.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_repository_root_has_no_mock_droppings() -> None:
    """Catches leftovers, including from a run that predates this test."""
    strays = sorted(p.name for p in ROOT.glob("<MagicMock*"))
    assert strays == [], (
        f"{len(strays)} mock-repr file(s) in the repo root, e.g. "
        f"{strays[0]!r} — a fixture is leaking a mock into a path"
    )


def test_gitignore_does_not_hide_a_bug_it_should_report() -> None:
    """The `<MagicMock*` line is a suppressed symptom, not a fix.

    Kept ignored is fine — a stray file must never reach a commit — but
    this test exists so that removing the cause and leaving the line is
    a deliberate choice rather than an oversight, and so the next person
    to see that line finds this explanation from a grep.
    """
    ignored = (ROOT / ".gitignore").read_text()
    assert "<MagicMock*" in ignored, (
        "the ignore line went away; if the leak is truly fixed that is "
        "fine, but delete this test in the same commit"
    )


def test_git_sees_a_clean_root() -> None:
    """No untracked junk that is not deliberately ignored."""
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    ).stdout
    junk = [
        line[3:] for line in out.splitlines()
        if line.startswith("??") and "MagicMock" in line
    ]
    assert junk == [], f"untracked mock droppings visible to git: {junk}"
