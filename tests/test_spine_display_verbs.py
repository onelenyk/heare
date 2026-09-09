"""The screen the voice model could not see.

Observed live, 9 September 2026. Asked «що ти бачиш на екрані», the
assistant said it saw nothing — while `displays` row 59 held 1118
characters of HTML the person was looking at. Asked to clear the canvas,
it said there was nothing to clear.

Neither answer was the model being dim. Its whole system prompt is one
sentence about speaking in short Ukrainian and mentions no screen; its
verbs were five, none of which touched the panel; and `delegate`, the
one door to the worker's `read_display`, describes itself as being for
files, the shell, the web, settings and the browser. There was no screen
anywhere in its world, so it answered out of a world with no screen.

These tests pin the two halves of the fix: the panel is reachable from
the fast path, and a panel with something on it is never reported empty.
"""

from __future__ import annotations

import asyncio

import pytest

from src.spine.persist import SpinePersistence
from src.spine.tools import VoiceToolbox, _strip_markup


class _NoHands:
    """Hands is irrelevant here and must not be built — it reads config,
    opens a model client and would make these tests about something
    else."""

    def set_delivery(self, deliver) -> None:  # noqa: D102
        self._deliver = deliver

    def start(self, task: str) -> None:  # noqa: D102
        raise AssertionError("the panel verbs must not go through the worker")

    def cancel_all(self) -> int:  # noqa: D102
        return 0


@pytest.fixture()
def persist(tmp_path):
    p = SpinePersistence(tmp_path / "heare.db")
    yield p
    p.close()


def _toolbox(persist):
    return VoiceToolbox(
        settings=object(),
        memory=None,
        deliver=lambda text: asyncio.sleep(0),
        hands_factory=lambda _settings: _NoHands(),
        persist=persist,
    )


def _show(persist, content: str, *, title: str = "", fmt: str = "html") -> None:
    """Put something on the panel the way every writer does — an append
    to the latest-only channel."""
    import time

    persist._conn.execute(
        "INSERT INTO displays (ts, title, format, content) VALUES (?, ?, ?, ?)",
        (time.time(), title, fmt, content),
    )
    persist._conn.commit()


# ── the panel is in the fast path at all ──────────────────────────────


def test_the_voice_model_is_handed_both_panel_verbs() -> None:
    from src.spine.tools import SCHEMAS

    names = {s["function"]["name"] for s in SCHEMAS}
    assert "read_display" in names
    assert "clear_display" in names


def test_the_panel_verbs_never_reach_the_worker(persist) -> None:
    """`_NoHands.start` raises. If either verb delegated, this fails."""
    box = _toolbox(persist)
    _show(persist, "<p>привіт</p>")
    asyncio.run(box.execute("read_display", {}))
    asyncio.run(box.execute("clear_display", {}))


# ── what it says about what is there ──────────────────────────────────


def test_a_panel_with_text_is_read_out_not_denied(persist) -> None:
    box = _toolbox(persist)
    _show(persist, "<h1>Дерево</h1><p>росте на галявині</p>", title="Ботаніка")
    said = asyncio.run(box.execute("read_display", {}))
    assert "Ботаніка" in said
    assert "росте на галявині" in said
    assert "<" not in said and ">" not in said


def test_a_drawing_is_a_drawing_not_an_empty_screen(persist) -> None:
    """The live failure, exactly: a canvas whose markup strips to no
    words at all. Saying "нічого немає" about it is the bug."""
    box = _toolbox(persist)
    _show(persist, "<svg><circle cx='5' cy='5' r='4'/></svg>", title="Дерево")
    said = asyncio.run(box.execute("read_display", {}))
    assert "Дерево" in said
    assert "порожн" not in said.lower()


def test_an_empty_panel_is_reported_empty(persist) -> None:
    box = _toolbox(persist)
    said = asyncio.run(box.execute("read_display", {}))
    assert "порожній" in said.lower()


def test_a_long_panel_is_not_recited_whole(persist) -> None:
    box = _toolbox(persist)
    _show(persist, "<p>" + ("слово " * 500) + "</p>")
    said = asyncio.run(box.execute("read_display", {}))
    assert len(said) < 500
    assert said.endswith("…")


# ── clearing ──────────────────────────────────────────────────────────


def test_clearing_empties_the_panel_for_every_reader(persist) -> None:
    box = _toolbox(persist)
    _show(persist, "<p>щось</p>", title="Щось")
    assert "Прибрала" in asyncio.run(box.execute("clear_display", {}))
    assert persist.latest_display() is None
    assert "порожній" in asyncio.run(box.execute("read_display", {})).lower()


def test_clearing_keeps_what_was_shown(persist) -> None:
    """An empty newest row, not a DELETE — "поверни назад" is the next
    thing a person says after "очисти"."""
    box = _toolbox(persist)
    _show(persist, "<p>щось</p>", title="Щось")
    asyncio.run(box.execute("clear_display", {}))
    rows = persist._conn.execute("SELECT content FROM displays").fetchall()
    assert any("щось" in (r[0] or "") for r in rows)


def test_clearing_an_empty_panel_says_so_and_writes_nothing(persist) -> None:
    box = _toolbox(persist)
    said = asyncio.run(box.execute("clear_display", {}))
    assert "порожньо" in said.lower()
    assert persist._conn.execute("SELECT COUNT(*) FROM displays").fetchone()[0] == 0


# ── the stripper ──────────────────────────────────────────────────────


def test_script_and_style_bodies_are_never_read_aloud() -> None:
    raw = "<style>p{color:red}</style><p>текст</p><script>alert(1)</script>"
    assert _strip_markup(raw) == "текст"


def test_entities_become_the_characters_they_stand_for() -> None:
    assert _strip_markup("<p>a&nbsp;&amp;&nbsp;b</p>") == "a & b"
