"""The installer speaks one of two languages, and says nothing it cannot
translate: both tables hold the same keys, and everything outside the
Ukrainian table is English."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "install.sh"
CYRILLIC = re.compile("[А-Яа-яЁёІіЇїЄєҐґ]")


def _script() -> str:
    return INSTALL.read_text(encoding="utf-8")


def _table(name: str) -> str:
    found = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", _script(), re.S | re.M)
    assert found, f"install.sh has no {name}()"
    return found.group(1)


def _keys(name: str) -> set[str]:
    return set(re.findall(r"^\s{8}([a-z][a-z0-9_]*)\)", _table(name), re.M))


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(INSTALL), *args], capture_output=True, text=True, env=env
    )


def test_sh_syntax_is_clean():
    subprocess.run(["sh", "-n", str(INSTALL)], check=True)


def test_both_tables_hold_every_message_key():
    english = _keys("text_en")
    ukrainian = _keys("text_uk")

    assert "usage" in english and "stopped" in english
    assert english == ukrainian


def test_every_key_the_script_asks_for_is_in_the_tables():
    used = set(re.findall(r"\b(?:msg|say|warn)\s+([a-z][a-z0-9_]*)", _script()))

    assert used
    assert used <= _keys("text_en")


def test_nothing_outside_the_ukrainian_table_speaks_ukrainian():
    without_ukrainian = _script().replace(_table("text_uk"), "")

    assert not CYRILLIC.search(without_ukrainian)


def test_help_is_english_by_default_even_on_a_ukrainian_mac():
    english = _run("--help", env={"LANG": "uk_UA.UTF-8", "LC_ALL": "uk_UA.UTF-8"})

    assert english.returncode == 0
    assert "install every tool" in english.stdout
    assert not CYRILLIC.search(english.stdout + english.stderr)


def test_lang_switch_speaks_ukrainian_and_explicit_english_wins():
    ukrainian = _run("--help", "--lang", "uk", env={"LANG": "en_US.UTF-8"})
    english = _run(
        "--lang", "en", "--help", env={"HEARE_LANG": "uk", "LANG": "uk_UA.UTF-8"}
    )

    assert ukrainian.returncode == 0
    assert CYRILLIC.search(ukrainian.stdout)
    assert english.returncode == 0
    assert not CYRILLIC.search(english.stdout + english.stderr)


def test_heare_lang_chooses_the_language_and_bad_values_stop_in_english():
    ukrainian = _run("--help", env={"HEARE_LANG": "uk"})
    bad = _run("--help", env={"HEARE_LANG": "de"})

    assert ukrainian.returncode == 0
    assert CYRILLIC.search(ukrainian.stdout)
    assert bad.returncode == 1
    assert "language is en or uk" in bad.stderr
    assert not CYRILLIC.search(bad.stderr)


def test_the_chosen_language_is_written_for_a_fresh_config_and_kept_otherwise():
    script = _script()

    assert 'Settings().set("language", sys.argv[1])' in script
    assert '"$SPOKEN"' in script
    assert "EXPLICIT_LANGUAGE" in script
    assert '[ ! -f "$CONFIG" ]' in script
