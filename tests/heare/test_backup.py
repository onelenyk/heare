"""A backup holds the owner's memory and settings, and gives them back."""

from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest

from heare.api import backup


def _home(root):
    home = root / ".heare"
    memory, settings, machine = home / "memory", home / "settings", home / "machine"
    for folder in (memory / "journal", memory / "profile", memory / "heard", settings, machine,
                   home / "cache", home / "logs"):
        folder.mkdir(parents=True)
    with sqlite3.connect(memory / "heare.db") as conn:
        conn.execute("CREATE TABLE facts (value TEXT, status TEXT)")
        conn.executemany("INSERT INTO facts VALUES (?, ?)",
                         [("Груша", "confirmed"), ("Мрійко", "confirmed"), ("Ріко", "rejected")])
    (memory / "journal" / "2026-10-24.jsonl").write_text('{"kind": "heard"}\n', encoding="utf-8")
    (memory / "journal" / "2026-10-25.jsonl").write_text('{"kind": "said"}\n', encoding="utf-8")
    (memory / "profile" / "user.md").write_text("Назар", encoding="utf-8")
    (memory / "heard" / "20261025-120000-000-00.wav").write_bytes(b"RIFF" + b"\0" * 100)
    (memory / "heard" / "20261025-120000-000-00.json").write_text("{}", encoding="utf-8")
    (memory / "reminders.json").write_text(json.dumps([
        {"why": "reminder", "status": "held"}, {"why": "reminder", "status": "delivered"},
        {"why": "task_done", "status": "held"}]), encoding="utf-8")
    (settings / "config.toml").write_text('llm_provider = "deepseek"\n', encoding="utf-8")
    (machine / "api_token").write_text("секрет", encoding="utf-8")
    (home / "cache" / "x.pyc").write_bytes(b"x")
    (home / "logs" / "app-brain.log").write_text("лог", encoding="utf-8")
    # The link layout.settle leaves at an old place.
    (home / "memory" / "user.md").symlink_to("profile/user.md")
    return home


def _names(path):
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def test_a_backup_holds_memory_and_settings_and_nothing_of_the_machine(tmp_path):
    home = _home(tmp_path)
    path = backup.make(home, version="0.1.0")

    assert path.parent == home / "backups"
    assert path.stat().st_mode & 0o777 == 0o600
    names = _names(path)
    assert {"manifest.json", "memory/heare.db", "memory/journal/2026-10-25.jsonl",
            "memory/profile/user.md", "memory/reminders.json",
            "memory/heard/20261025-120000-000-00.wav", "settings/config.toml"} <= names
    assert not any(n.startswith(("machine/", "cache/", "logs/")) for n in names)
    assert "memory/user.md" not in names  # the link, not a second copy


def test_the_manifest_says_what_is_inside(tmp_path):
    home = _home(tmp_path)
    manifest = backup.check(backup.make(home, version="0.1.0"))
    assert manifest["format"] == backup.FORMAT
    assert manifest["version"] == "0.1.0"
    assert manifest["sound"] is True
    assert manifest["counts"] == {"facts": 2, "days": 2, "recordings": 1, "reminders": 1}


def test_without_sound_there_are_no_recordings(tmp_path):
    home = _home(tmp_path)
    path = backup.make(home, sound=False)
    assert not any(n.startswith("memory/heard/") for n in _names(path))
    assert backup.check(path)["counts"]["recordings"] == 0


def test_the_database_is_whole_while_someone_writes_to_it(tmp_path):
    home = _home(tmp_path)
    writer = sqlite3.connect(home / "memory" / "heare.db")
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO facts VALUES ('Бурштин', 'confirmed')")
    writer.commit()  # in the WAL, not yet in the main file
    try:
        path = backup.make(home)
    finally:
        writer.close()
    assert backup.check(path)["counts"]["facts"] == 3


def test_two_backups_in_one_second_do_not_overwrite_each_other(tmp_path):
    home = _home(tmp_path)
    first, second = backup.make(home), backup.make(home)
    assert first != second
    assert [b["name"] for b in backup.listing(home)] == [second.name, first.name]


def test_the_listing_says_what_each_backup_holds_and_which_is_broken(tmp_path):
    home = _home(tmp_path)
    path = backup.make(home)
    (home / "backups" / "heare-20200101-000000.zip").write_bytes(b"not a zip")
    (home / "backups" / "notes.zip").write_bytes(b"")
    listed = backup.listing(home)
    assert [b["name"] for b in listed] == [path.name, "heare-20200101-000000.zip"]
    assert listed[0]["counts"]["facts"] == 2 and listed[0]["sound"] is True
    assert listed[1]["broken"] is True


def test_a_changed_file_is_found(tmp_path):
    home = _home(tmp_path)
    path = backup.make(home)
    with zipfile.ZipFile(path) as old, zipfile.ZipFile(tmp_path / "bad.zip", "w") as new:
        for item in old.infolist():
            data = old.read(item)
            new.writestr(item, b"changed" if item.filename == "memory/profile/user.md" else data)
    with pytest.raises(backup.BackupError, match="пошкоджений"):
        backup.check(tmp_path / "bad.zip")


def test_a_backup_reaching_outside_its_sections_is_refused(tmp_path):
    bad = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad, "w") as out:
        out.writestr("manifest.json", json.dumps(
            {"format": 1, "files": {"../machine/api_token": {"sha256": "x"}}}))
        out.writestr("../machine/api_token", "x")
    with pytest.raises(backup.BackupError, match="чужий шлях"):
        backup.check(bad)


def test_a_backup_from_a_newer_heare_is_refused(tmp_path):
    newer = tmp_path / "newer.zip"
    with zipfile.ZipFile(newer, "w") as out:
        out.writestr("manifest.json", json.dumps({"format": backup.FORMAT + 1, "files": {}}))
    with pytest.raises(backup.BackupError, match="онови heare"):
        backup.check(newer)


def test_not_a_zip_is_refused(tmp_path):
    (tmp_path / "x.zip").write_text("привіт", encoding="utf-8")
    with pytest.raises(backup.BackupError):
        backup.check(tmp_path / "x.zip")


def _facts(home):
    with sqlite3.connect(home / "memory" / "heare.db") as conn:
        return sorted(v for (v,) in conn.execute("SELECT value FROM facts"))


def test_a_restore_brings_back_what_was_and_keeps_what_it_replaced(tmp_path):
    home = _home(tmp_path)
    path = backup.make(home)
    # Life goes on after the backup.
    with sqlite3.connect(home / "memory" / "heare.db") as conn:
        conn.execute("DELETE FROM facts WHERE value = 'Груша'")
    (home / "memory" / "journal" / "2026-10-26.jsonl").write_text("{}\n", encoding="utf-8")
    (home / "settings" / "config.toml").write_text('llm_provider = "groq"\n', encoding="utf-8")

    backup.stage(home, path)
    assert _facts(home) == ["Мрійко", "Ріко"]  # nothing moves until the next start
    done = backup.apply(home)

    assert done["name"] == path.name
    assert _facts(home) == ["Груша", "Мрійко", "Ріко"]
    assert not (home / "memory" / "journal" / "2026-10-26.jsonl").exists()
    assert "deepseek" in (home / "settings" / "config.toml").read_text(encoding="utf-8")
    aside = home / "backups" / done["aside"]
    assert (aside / "memory" / "journal" / "2026-10-26.jsonl").exists()
    assert "groq" in (aside / "settings" / "config.toml").read_text(encoding="utf-8")
    assert backup.restored(home)["name"] == path.name
    assert not (home / backup.PENDING).exists()
    assert backup.apply(home) is None


def test_a_restore_leaves_the_machine_alone(tmp_path):
    home = _home(tmp_path)
    path = backup.make(home)
    (home / "machine" / "api_token").write_text("новий", encoding="utf-8")
    backup.stage(home, path)
    backup.apply(home)
    assert (home / "machine" / "api_token").read_text(encoding="utf-8") == "новий"


def test_a_restore_without_sound_keeps_this_macs_recordings(tmp_path):
    home = _home(tmp_path)
    path = backup.make(home, sound=False)
    backup.stage(home, path)
    backup.apply(home)
    assert (home / "memory" / "heard" / "20261025-120000-000-00.wav").exists()


def test_a_broken_backup_is_never_staged(tmp_path):
    home = _home(tmp_path)
    (tmp_path / "x.zip").write_text("привіт", encoding="utf-8")
    with pytest.raises(backup.BackupError):
        backup.stage(home, tmp_path / "x.zip")
    assert not (home / backup.PENDING).exists()
    assert backup.apply(home) is None
    assert _facts(home) == ["Груша", "Мрійко", "Ріко"]
