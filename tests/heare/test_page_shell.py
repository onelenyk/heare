"""The chat-first shell: the frame around the conversation.

The page is three files; this holds the shell's promises: the chat panel,
the two floating bubbles, and the rule that the preview and the sheet are
not trapped inside the panel (a container-query container is the containing
block for fixed children too)."""

from __future__ import annotations

from pathlib import Path

API = Path(__file__).resolve().parents[2] / "packages/heare-brain/heare/api"
PAGE = (API / "page.html").read_text(encoding="utf-8")
STYLE = (API / "page.css").read_text(encoding="utf-8")
SCRIPT = (API / "page.js").read_text(encoding="utf-8")


def test_the_shell_has_its_frame_and_bubbles():
    for ident in ("top", "chat", "pin-dock", "feed", "flow", "composer",
                  "system", "sheet", "pinpreview", "veil", "toast"):
        assert f'id="{ident}"' in PAGE


def test_the_panel_owns_the_wings_and_the_scroll_room():
    assert "container-type:inline-size" in STYLE
    assert "--wing:max(0px, calc((100cqw" in STYLE
    assert "scroll-padding-block:calc(var(--dock-h) + 16px)" in STYLE
    assert "#chat::before" in STYLE and "#chat::after" in STYLE


def test_the_preview_and_the_sheet_stay_outside_the_chat():
    # #chat is a container, so fixed children would be positioned against it.
    chat = PAGE.split('id="chat"', 1)[1].split("</section>", 1)[0]
    for ident in ("pinpreview", "sheet", "toast", "veil"):
        assert f'id="{ident}"' not in chat
    assert PAGE.index('id="pinpreview"') > PAGE.index('id="sheet"')
    assert PAGE.index('id="toast"') > PAGE.index('id="sheet"')


def test_the_demo_is_a_flag_not_a_second_page():
    assert 'const DEMO = new URLSearchParams(location.search).get("demo") === "1"' in SCRIPT
    assert "const DEMO_FEED =" in SCRIPT and "const DEMO_PINS =" in SCRIPT
    feed = SCRIPT.split("const DEMO_FEED = [")[1].split("const DEMO_PINS")[0]
    assert feed.count('id: "demo/') == 15
    assert SCRIPT.count('mid: "demo/') == 3 and SCRIPT.count('mid: "nov') == 1
    assert "if (!DEMO) { loadSessions()" in SCRIPT
    assert "demo();" in SCRIPT
