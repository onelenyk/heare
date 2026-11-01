"""Other tools' memory: read, never written, and no version of theirs breaks ours."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from heare.memory.sources import TEXT_CHARS, all_sources, read_only
from heare.memory.sources.claude_code import ClaudeCode
from heare.memory.sources.claude_mem import ClaudeMem
from heare.memory.sources.engram import Engram


def note(root: Path, project: str, name: str, kind: str | None, body: str) -> Path:
    folder = root / project / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    head = f"---\nname: x\nmetadata:\n  type: {kind}\n---\n" if kind else ""
    path = folder / name
    path.write_text(head + body, encoding="utf-8")
    return path


def engram_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
      CREATE TABLE observations(id INTEGER PRIMARY KEY, type TEXT, title TEXT, content TEXT,
        project TEXT, created_at TEXT, deleted_at TEXT);
      CREATE VIRTUAL TABLE observations_fts USING fts5(title, content, content='observations', content_rowid='id');
      INSERT INTO observations VALUES
        (1,'decision','Вибір','Пишу на Kotlin','lenyk','2026-08-20 21:21:26',NULL),
        (2,'decision','Вибір','драйвер панелі','kmp-driver','2026-08-20 21:21:26',NULL),
        (3,'bugfix','Вада','щось','lenyk','2026-08-20 21:21:26',NULL),
        (4,'decision','Стерто','старе','lenyk','2026-08-20 21:21:26','2026-08-21');
      INSERT INTO observations_fts(observations_fts) VALUES('rebuild');
    """)
    c.commit()
    c.close()
    return path


def mem_db(path: Path) -> Path:
    c = sqlite3.connect(path)
    c.executescript("""
      CREATE TABLE observations(id INTEGER PRIMARY KEY, project TEXT, type TEXT, title TEXT,
        narrative TEXT, created_at_epoch INTEGER);
      CREATE VIRTUAL TABLE observations_fts USING fts5(title, narrative, content='observations', content_rowid='id');
      INSERT INTO observations VALUES (1,'p','discovery','Панель','OLED на столі',1790000000000);
      INSERT INTO observations_fts(observations_fts) VALUES('rebuild');
    """)
    c.commit()
    c.close()
    return path


def test_only_notes_about_the_person_are_personal(tmp_path):
    note(tmp_path, "a", "me.md", "user", "Ти пишеш на Kotlin")
    note(tmp_path, "a", "how.md", "feedback", "Коротко")
    note(tmp_path, "a", "code.md", "project", "Вада в модулі")
    note(tmp_path, "a", "plain.md", None, "Без шапки")
    note(tmp_path, "a", "MEMORY.md", None, "індекс")
    records = {r.text: r for r in ClaudeCode(tmp_path).scan()}
    assert len(records) == 4
    assert records["Ти пишеш на Kotlin"].personal and records["Коротко"].personal
    assert not records["Вада в модулі"].personal and not records["Без шапки"].personal


def test_a_note_keeps_its_key_until_it_changes(tmp_path):
    path = note(tmp_path, "a", "me.md", "user", "раз")
    first = next(ClaudeCode(tmp_path).scan()).key
    assert next(ClaudeCode(tmp_path).scan()).key == first
    path.write_text("два", encoding="utf-8")
    assert next(ClaudeCode(tmp_path).scan()).key != first


def test_a_broken_note_is_skipped(tmp_path):
    note(tmp_path, "a", "ok.md", "user", "добре")
    (tmp_path / "a" / "memory" / "bad.md").write_bytes(b"\xff\xfe\xff")
    assert [r.text for r in ClaudeCode(tmp_path).scan()] == ["добре"]


def test_a_long_note_is_cut(tmp_path):
    note(tmp_path, "a", "me.md", "user", "я" * (TEXT_CHARS * 2))
    assert len(next(ClaudeCode(tmp_path).scan()).text) == TEXT_CHARS


def test_notes_are_searched_by_their_words(tmp_path):
    note(tmp_path, "a", "me.md", "user", "Ти пишеш на Kotlin")
    note(tmp_path, "a", "no.md", "user", "Інше")
    assert [r.text for r in ClaudeCode(tmp_path).search("kotlin")] == ["Ти пишеш на Kotlin"]


def test_engram_offers_only_the_owners_decisions(tmp_path):
    source = Engram(engram_db(tmp_path / "lenyk" / ".engram" / "e.db"))
    assert source.found() and source.count() == 3
    assert [r.key for r in source.scan() if r.personal] == ["engram:1"]


def test_deleted_is_gone(tmp_path):
    source = Engram(engram_db(tmp_path / "lenyk" / ".engram" / "e.db"))
    assert "engram:4" not in [r.key for r in source.scan()]
    assert source.search("старе") == []


def test_engram_is_searched_with_quotes_in_the_query(tmp_path):
    source = Engram(engram_db(tmp_path / "lenyk" / ".engram" / "e.db"))
    assert [r.key for r in source.search('панелі "kmp')] == ["engram:2"]


def test_claude_mem_is_searchable_but_never_personal(tmp_path):
    source = ClaudeMem(mem_db(tmp_path / "m.db"))
    assert [r.text for r in source.search("панель")] == ["Панель\nOLED на столі"]
    assert not any(r.personal for r in source.scan())


@pytest.mark.parametrize("cls", [Engram, ClaudeMem])
def test_an_unknown_version_is_not_found_and_does_not_raise(tmp_path, cls):
    path = tmp_path / "x.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE observations(id INTEGER)")
    c.commit()
    c.close()
    source = cls(path)
    assert not source.found() and source.count() == 0
    assert list(source.scan()) == [] and source.search("щось") == []


@pytest.mark.parametrize("cls", [Engram, ClaudeMem])
def test_not_a_database_is_not_found(tmp_path, cls):
    path = tmp_path / "x.db"
    path.write_text("not sqlite")
    assert not cls(path).found()


def test_absent_sources_are_simply_absent(tmp_path):
    assert [s.found() for s in all_sources(tmp_path)] == [False, False, False]


def test_a_source_cannot_be_written(tmp_path):
    path = engram_db(tmp_path / "lenyk" / ".engram" / "e.db")
    before = path.read_bytes()
    with read_only(path) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM observations")
    assert path.read_bytes() == before


def test_a_broken_byte_does_not_end_the_read(tmp_path):
    path = engram_db(tmp_path / "lenyk" / ".engram" / "e.db")
    c = sqlite3.connect(path)
    c.execute("UPDATE observations SET title = CAST(x'ff41' AS TEXT) WHERE id = 1")
    c.commit()
    c.close()
    assert len(list(Engram(path).scan())) == 3


def test_the_owners_project_is_not_written_into_the_code(tmp_path):
    source = Engram(engram_db(tmp_path / "someone" / ".engram" / "e.db"))
    assert not any(r.personal for r in source.scan())
