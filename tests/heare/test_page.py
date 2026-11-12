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

API = Path(__file__).resolve().parents[2] / "packages/heare-brain/heare/api"
PAGE = (API / "page.html").read_text(encoding="utf-8")
SCRIPT = (API / "page.js").read_text(encoding="utf-8")
STYLE = (API / "page.css").read_text(encoding="utf-8")
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
    assert tabs == ["facts", "reminders", "journal", "search", "hearing", "profile", "sources", "backups"]
    assert ('const MIND_TABS = ["facts", "reminders", "journal", "search", "hearing", '
            '"profile", "sources", "backups"]' in SCRIPT)
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
    assert 'twice(forget, tr("Forget for sure?")' in journal
    assert 'item.kind === "heard" || item.kind === "said"' in journal
    # The search tab shares mindItem, and its items carry no journal id to forget.
    item = SCRIPT.split("function mindItem")[1].split("const mindWhen")[0]
    assert "mind/journal/forget" not in item
    assert "twice(" not in item


def test_journal_and_search_say_what_happened():
    # Empty and failed states live in their panels, never in an alert.
    assert "the journal is empty." in SCRIPT
    assert "I recall nothing." in SCRIPT
    assert "the search failed" in SCRIPT
    assert "could not read the journal." in SCRIPT
    assert 'said.hidden = !text' in SCRIPT
    assert "alert(" not in SCRIPT.split("function mindItem")[1].split("function mindShow")[0]


def test_mind_refresh_keeps_search_results_and_refreshes_an_open_journal():
    refresh = SCRIPT.split("function mindLoad")[1].split("function mindClose")[0]
    assert "mindJournalLoad()" in refresh
    assert "mind-search-results" not in refresh


def test_the_drawer_styles_new_rows_within_its_own_language():
    item = STYLE.split(".mind-item {")[1].split("}")[0]
    assert "border-radius:12px" in item
    assert "var(--card-2)" in item
    text = STYLE.split(".mind-item-text {")[1].split("}")[0]
    assert "overflow-wrap:anywhere" in text
    meta = STYLE.split(".mind-item-meta {")[1].split("}")[0]
    assert "var(--mono)" in meta
    assert "#mind { width:100%; border-left:0; }" in STYLE


@pytest.mark.parametrize("kind", KINDS)
def test_the_page_draws_every_kind_of_control(kind):
    assert f'c.kind === "{kind}"' in SCRIPT


INSTALLER = (Path(__file__).resolve().parents[2]
             / "packages/heare-keeper/heare/installer.html").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_names_the_hand_that_works_and_the_one_that_asks():
    assert "t.executor" in SCRIPT
    assert "question.executor" in SCRIPT
    assert "permission.executor" in SCRIPT


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
    assert 'twice(back, tr("Sure? I will set the current aside")' in tab
    assert '"/restore", {method: "POST"}' in tab
    assert 'if (tab === "backups") backupsLoad();' in SCRIPT


def test_after_a_restore_the_reloaded_page_opens_on_the_backups():
    """The brain restarts on a restore and the page reloads with it; the
    owner should land where they were, seeing what happened."""
    assert 'sessionStorage.setItem("heare-restoring", "1")' in SCRIPT
    assert 'if (restoring) { mindOpenDrawer(); mindShow("backups"); }' in SCRIPT
    # 25 October: run before `const shut` was defined, it threw in silence.
    assert SCRIPT.index("const shut = ") < SCRIPT.index("if (restoring)")


def test_the_page_offers_a_newer_heare_and_says_how_it_goes():
    draw = SCRIPT.split("function drawUpdate(u)")[1].split("function spark()")[0]
    assert 'api("update/install", {method: "POST"})' in draw
    assert "u.can_install" in draw and "release page" in draw
    assert "drawUpdate(s.update);" in SCRIPT
    assert 'else if (e.kind === "update") drawUpdate(e);' in SCRIPT


def test_the_stream_reconnects_and_knocks():
    """After a brain restart the page must re-knock as well as refresh: the
    registry forgot the window and a stream alone does not register it."""
    opens = [line for line in SCRIPT.splitlines() if "stream.onopen =" in line]
    assert len(opens) == 1, opens
    assert "knock()" in opens[0] and "refresh()" in opens[0]


def test_what_waits_is_a_card_at_the_foot_of_the_feed():
    """A leave, a question, a running hand and a held thought are cards in
    the feed, not a hidden pult: the person can answer where they read."""
    assert 'id="hold"' in PAGE
    for name in ("permissionCard", "questionCard", "taskCard", "intentCard", "renderHold"):
        assert f"function {name}(" in SCRIPT
    assert 'hold.replaceChildren(...parts);' in SCRIPT
    assert 'if (hold && hold.parentNode === $("flow")) $("flow").insertBefore(node, hold);' in SCRIPT
    assert "#hold { display:grid;" in STYLE


def test_a_message_with_a_script_runs_in_a_sealed_frame():
    """The page cannot run a card's script where it stands; a card that
    carries one is a sealed frame of its own, sized by its bridge."""
    render = SCRIPT.split('if (m.kind === "html")')[1].split("if (m.who === \"me\")")[0]
    assert "liveframe" in render and 'sandbox="allow-scripts"' in render
    assert "/<script[\\s>]|<style[\\s>]/i" in render
    assert 'srcdoc="${escapeHtml(framed(' in render
    assert "#flow iframe" in SCRIPT and "#livefull iframe" in SCRIPT


def test_a_live_widget_cannot_read_memory_or_say():
    may = re.search(r"const MAY_GET = /(.*?)/;", SCRIPT).group(1)
    for forbidden in ("memory", "history", "canvas"):
        assert forbidden not in may
    assert {"system", "state", "tasks"} <= set(re.findall(r"[a-z]+", may))
    bridge = SCRIPT.split("const BRIDGE = `", 1)[1].split("<\\/script>`", 1)[0]
    assert "say" not in bridge
    assert 'sandbox="allow-scripts"' in SCRIPT
    assert "allow-same-origin" not in SCRIPT
    assert 'heare: "say"' not in SCRIPT and 'm.heare === "say"' not in SCRIPT


def test_the_page_takes_its_name_from_the_brain():
    # Step 7a writes identity.json («Дока»); /api/state carries it in
    # model.name, so the header is not stuck on the static «heare».
    assert 'if (m.name) { $("name").textContent = m.name;' in SCRIPT


def test_the_hearing_form_says_what_the_marks_are_for():
    assert "The marks stay with the recording" in SCRIPT
    assert "From this we measure" not in SCRIPT


def test_a_saved_key_refills_the_providers_without_reopening():
    assert "function fillProviders()" in SCRIPT
    save = SCRIPT.split('$("save-key").onclick')[1].split('$("lang").value')[0]
    assert "refresh().then" in save and "fillProviders()" in save
