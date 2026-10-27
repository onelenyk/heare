"""The profile as a model sees it: the owner's files inside a data frame."""

from __future__ import annotations

from pathlib import Path

import pytest

from heare.memory.book import Book
from heare.memory.profile import (
    CLOSE, ELLIPSIS, FRAME, GOALS_LIMIT, THREADS_LIMIT, USER_LIMIT, Profile,
)
from tests.heare.fakes import open_room


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def _block(rendered: str, heading: str) -> str:
    start = rendered.index(heading) + len(heading) + 1
    rest = rendered[start:]
    stops = [rest.find(mark) for mark in ("\n###", "\n" + CLOSE) if mark in rest]
    return rest[: min(stops)] if stops else rest


def test_an_empty_profile_renders_nothing(tmp_path):
    assert Profile(tmp_path).render() == ""


def test_the_owner_files_are_rendered_in_order(tmp_path):
    _write(tmp_path, "user.md", "Мене звати Назар.")
    _write(tmp_path, "threads.md", "До пʼятниці — подати звіт.")
    _write(tmp_path, "goals.md", "Запустити heare.")
    rendered = Profile(tmp_path).render()
    order = [
        rendered.index("### Owner"),
        rendered.index("### Open threads"),
        rendered.index("### Goals"),
    ]
    assert order == sorted(order)
    assert (
        rendered.index("Мене звати Назар.")
        < rendered.index("До пʼятниці")
        < rendered.index("Запустити heare.")
    )


@pytest.mark.parametrize(("name", "heading", "limit"), [
    ("user.md", "### Owner", USER_LIMIT),
    ("threads.md", "### Open threads", THREADS_LIMIT),
    ("goals.md", "### Goals", GOALS_LIMIT),
])
def test_a_block_is_truncated_at_its_limit_without_breaking_a_letter(
    tmp_path, name, heading, limit
):
    _write(tmp_path, name, "ї" * (limit + 500))
    block = _block(Profile(tmp_path).render(), heading)
    assert len(block) == limit
    assert block.endswith(ELLIPSIS)
    assert set(block) <= {"ї", ELLIPSIS}


def test_missing_or_unreadable_files_are_empty_blocks(tmp_path):
    assert Profile(tmp_path).render() == ""

    (tmp_path / "user.md").mkdir()
    _write(tmp_path, "threads.md", "До пʼятниці — подати звіт.")
    rendered = Profile(tmp_path).render()
    assert "### Owner" not in rendered
    assert "### Open threads" in rendered
    assert rendered.startswith(FRAME)
    assert rendered.endswith(CLOSE)


def test_the_whole_render_stays_within_the_total_limit(tmp_path):
    for name in ("user.md", "threads.md", "goals.md"):
        _write(tmp_path, name, "ї" * 5000)
    rendered = Profile(tmp_path).render()
    assert len(rendered) <= 6000


async def test_the_book_returns_the_profile_it_is_pointed_at(tmp_path):
    profile_dir = tmp_path / "memory"
    profile_dir.mkdir()
    _write(profile_dir, "user.md", "Мене звати Назар.")
    book = Book(tmp_path / "heare.db", profile_dir=profile_dir)
    try:
        assert await book.always() == Profile(profile_dir).render()
        assert "Мене звати Назар." in await book.always()
    finally:
        book.close()


async def test_the_profile_is_in_the_system_message_the_model_reads(tmp_path):
    _write(tmp_path, "user.md", "Мене звати Назар.")
    rendered = Profile(tmp_path).render()
    room = await open_room()
    room.memory.known = rendered
    room.model.will_say("Привіт.")
    room.says("Привіт")
    await room.settled()
    assert rendered in room.model.asked[0][0]["content"]
    await room.close()
