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
KINDS = ("toggle", "slider", "choice", "label", "button", "log", "text", "terminal")


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


def test_the_mind_drawer_has_journal_and_search_before_profile():
    tabs = re.findall(r'id="mind-tab-(\w+)"', PAGE)
    assert tabs == ["facts", "reminders", "journal", "search", "hearing", "profile", "backups"]
    assert ('const MIND_TABS = ["facts", "reminders", "journal", "search", "hearing", '
            '"profile", "backups"]' in SCRIPT)
    for tab in ("journal", "search", "hearing", "backups"):
        assert f'id="mind-panel-{tab}"' in PAGE
        assert f'aria-controls="mind-panel-{tab}"' in PAGE
        assert (f'id="mind-panel-{tab}" role="tabpanel" '
                f'aria-labelledby="mind-tab-{tab}"') in PAGE


def test_journal_and_search_share_one_item_renderer():
    assert "function mindItem(item)" in SCRIPT
    assert 'el("div", "mind-item")' in SCRIPT
    assert 'el("p", "mind-item-text"' in SCRIPT
    assert "mindStamp(item.at)" in SCRIPT
    journal = SCRIPT.split("function mindJournalLoad")[1].split("function mindSearchSubmit")[0]
    search = SCRIPT.split("function mindSearchSubmit")[1].split("function mindShow")[0]
    assert "mindItem(item)" in journal
    assert "items.map(mindItem)" in search


def test_the_mind_drawer_asks_for_journal_and_search():
    assert 'api("mind/journal?limit=80")' in SCRIPT
    assert 'api("mind/search?q=" + encodeURIComponent(q))' in SCRIPT


def test_the_journal_can_forget_an_entry_on_a_second_click():
    journal = SCRIPT.split("function mindJournalLoad")[1].split("function mindSearchSubmit")[0]
    assert 'mindAction(forget, "mind/journal/forget"' in journal
    assert 'twice(forget, "Точно забути?"' in journal
    assert 'item.kind === "heard" || item.kind === "said"' in journal
    # The search tab shares mindItem, and its items carry no journal id to forget.
    item = SCRIPT.split("function mindItem")[1].split("const mindWhen")[0]
    assert "mind/journal/forget" not in item
    assert "twice(" not in item


def test_journal_and_search_say_what_happened():
    # Empty and failed states live in their panels, never in an alert.
    assert "журнал порожній." in SCRIPT
    assert "нічого не пригадую." in SCRIPT
    assert "не вдалося пошукати" in SCRIPT
    assert "не вдалося прочитати журнал." in SCRIPT
    assert 'said.hidden = !text' in SCRIPT
    assert "alert(" not in SCRIPT.split("function mindItem")[1].split("function mindShow")[0]


def test_mind_refresh_keeps_search_results_and_refreshes_an_open_journal():
    refresh = SCRIPT.split("function mindLoad")[1].split("function mindClose")[0]
    assert "mindJournalLoad()" in refresh
    assert "mind-search-results" not in refresh


def test_the_drawer_styles_new_rows_within_its_own_language():
    item = PAGE.split(".mind-item {")[1].split("}")[0]
    assert "border-radius:12px" in item
    assert "var(--card-2)" in item
    text = PAGE.split(".mind-item-text {")[1].split("}")[0]
    assert "overflow-wrap:anywhere" in text
    meta = PAGE.split(".mind-item-meta {")[1].split("}")[0]
    assert "var(--mono)" in meta
    assert "#mind { width:100%; border-left:0; }" in PAGE


@pytest.mark.parametrize("kind", KINDS)
def test_the_page_draws_every_kind_of_control(kind):
    assert f'c.kind === "{kind}"' in SCRIPT


INSTALLER = (Path(__file__).resolve().parents[2]
             / "packages/heare-keeper/heare/installer.html").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_installer_s_script_parses(tmp_path):
    script = tmp_path / "installer.js"
    script.write_text("\n".join(re.findall(r"<script>(.*?)</script>", INSTALLER, re.S)),
                      encoding="utf-8")
    checked = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_the_page_shows_versions_and_marks_a_part_of_another():
    # The brain's version beside its uptime; a part's in its name's tooltip,
    # and a mark when the two differ.
    assert "s.running" in SCRIPT
    assert '"v" + b.version' in SCRIPT
    assert "b.version !== brain" in SCRIPT


def test_no_browser_dialogs_the_apps_window_would_swallow():
    """The app's window does not show confirm, alert or prompt: confirm
    answers «no» in silence, so «Забути» did nothing there (24 October)."""
    found = re.findall(r"\b(?:window\.)?(confirm|alert|prompt)\s*\(", SCRIPT)
    assert not found, found


def test_the_backups_tab_makes_brings_takes_and_restores_on_a_second_click():
    tab = SCRIPT.split("/* ── «Копії»")[1].split("function mindSearchSubmit")[0]
    assert 'api("backups", {method: "POST", body: {sound: $("backup-sound").checked}})' in tab
    assert 'fetch("/api/backups/bring", {method: "POST", body: file' in tab
    assert 'take.href = "/api/backups/" + encodeURIComponent(b.name)' in tab
    assert 'twice(back, "Точно? Поточне відкладу"' in tab
    assert '"/restore", {method: "POST"}' in tab
    assert 'if (tab === "backups") backupsLoad();' in SCRIPT


def test_after_a_restore_the_reloaded_page_opens_on_the_backups():
    """The brain restarts on a restore and the page reloads with it; the
    owner should land where they were, seeing what happened."""
    assert 'sessionStorage.setItem("heare-restoring", "1")' in SCRIPT
    back = SCRIPT.split('if (sessionStorage.getItem("heare-restoring"))')[1].split("} catch")[0]
    assert "mindOpenDrawer();" in back and 'mindShow("backups");' in back
