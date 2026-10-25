"""~/.heare in sections: the owner's, this machine's, and what nothing reads."""

from __future__ import annotations

import multiprocessing
import os
import time

from heare import config, layout


def _old_home(root):
    home = root / ".heare"
    (home / "events").mkdir(parents=True)
    (home / "events" / "2026-10-25.jsonl").write_text("{}\n", encoding="utf-8")
    (home / "memory").mkdir()
    (home / "memory" / "user.md").write_text("Назар", encoding="utf-8")
    (home / "memory" / "review.json").write_text("{}", encoding="utf-8")
    (home / "parts" / "voice").mkdir(parents=True)
    for name, text in {"heare.db": "db", "heare.db-wal": "wal", "intents.json": "[]",
                       "config.toml": 'llm_provider = "deepseek"\n', "api_token": "t",
                       "identity.json": '{"name": "Дока"}', "core.db": "old",
                       "mine.txt": "моє"}.items():
        (home / name).write_text(text, encoding="utf-8")
    return home


def test_an_old_home_is_put_in_sections(tmp_path):
    home = _old_home(tmp_path)
    layout.settle(home)

    assert (home / "memory/heare.db").read_text() == "db"
    assert (home / "memory/heare.db-wal").read_text() == "wal"
    assert (home / "memory/journal/2026-10-25.jsonl").is_file()
    assert (home / "memory/profile/user.md").read_text() == "Назар"
    assert (home / "memory/profile/review.json").is_file()
    assert (home / "memory/reminders.json").read_text() == "[]"
    assert (home / "settings/config.toml").is_file()
    assert (home / "settings/identity.json").is_file()
    assert (home / "machine/api_token").read_text() == "t"
    assert (home / "machine/parts/voice").is_dir()
    assert (home / "old/core.db").read_text() == "old"
    assert (home / "layout").read_text().strip() == layout.VERSION


def test_what_moved_can_still_be_found_where_it_was(tmp_path):
    """make rollback puts back a release that reads the old places."""
    home = _old_home(tmp_path)
    layout.settle(home)
    for old in ("heare.db", "events", "intents.json", "config.toml", "api_token", "parts",
                "memory/user.md"):
        assert (home / old).is_symlink(), old
    assert (home / "heare.db").read_text() == "db"
    assert (home / "memory/user.md").read_text() == "Назар"
    assert (home / "events/2026-10-25.jsonl").is_file()


def test_what_it_does_not_know_stays_where_it_is(tmp_path):
    home = _old_home(tmp_path)
    layout.settle(home)
    assert (home / "mine.txt").read_text() == "моє"
    assert not (home / "mine.txt").is_symlink()


def test_a_second_settle_changes_nothing(tmp_path):
    home = _old_home(tmp_path)
    layout.settle(home)
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    layout.settle(home)
    assert sorted(str(p.relative_to(home)) for p in home.rglob("*")) == before


def test_a_file_an_older_release_wrote_over_its_link_is_not_lost(tmp_path):
    home = _old_home(tmp_path)
    layout.settle(home)
    # The rolled-back release writes atomically: the link becomes a file.
    (home / "intents.json").unlink()
    (home / "intents.json").write_text('["новіше"]', encoding="utf-8")
    later = time.time() + 5
    os.utime(home / "intents.json", (later, later))

    layout.settle(home)

    assert (home / "memory/reminders.json").read_text() == '["новіше"]'
    assert (home / "intents.json").is_symlink()
    kept = [p.read_text() for p in (home / "old").rglob("reminders.json")]
    assert kept == ["[]"]


def test_a_fresh_home_gets_its_sections(tmp_path):
    home = tmp_path / ".heare"
    layout.settle(home)
    for section in ("memory", "settings", "machine"):
        assert (home / section).is_dir()
        assert (home / section).stat().st_mode & 0o777 == 0o700


def _settle_in(home: str) -> None:
    from pathlib import Path
    layout.settle(Path(home))


def test_two_processes_starting_at_once_move_it_once(tmp_path):
    home = _old_home(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    runs = [ctx.Process(target=_settle_in, args=(str(home),)) for _ in range(2)]
    for run in runs:
        run.start()
    for run in runs:
        run.join(30)
    assert [run.exitcode for run in runs] == [0, 0]
    assert (home / "memory/heare.db").read_text() == "db"
    assert not (home / "old").joinpath("displaced").exists()
    assert not any(p.name.startswith("displaced") for p in (home / "old").iterdir())


def test_the_defaults_point_into_the_sections():
    assert config.DEFAULTS["db_path"] == config.MEMORY / "heare.db"
    assert config.DEFAULTS["events_dir"] == config.MEMORY / "journal"
    assert config.DEFAULTS["api_token_file"] == config.MACHINE / "api_token"
    assert config.DEFAULTS["identity_file"] == config.SETTINGS / "identity.json"
    for name, value in config.DEFAULTS.items():
        if name.endswith(("_file", "_dir", "_path")) and name != "executor_cwd":
            assert value.parent != config.HOME or name == "backups_dir", name


def test_before_the_first_move_what_lived_there_wins_over_a_newer_stray(tmp_path):
    """25 October: a test left an empty heare.db at the new place, a minute
    old; a year of conversation at the old place must not lose to it."""
    home = _old_home(tmp_path)
    (home / "memory" / "heare.db").write_text("порожня", encoding="utf-8")
    later = time.time() + 60
    os.utime(home / "memory" / "heare.db", (later, later))

    layout.settle(home)

    assert (home / "memory/heare.db").read_text() == "db"
    assert [p.read_text() for p in (home / "old").rglob("heare.db")] == ["порожня"]
