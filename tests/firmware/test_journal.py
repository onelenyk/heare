"""The panel's own log: kept in memory, printed only when debugged."""

from heare_panel import journal


def test_lines_are_kept_up_to_a_limit_and_not_printed(monkeypatch, capsys):
    monkeypatch.setattr(journal, "_lines", [])
    monkeypatch.setattr(journal, "_loud", [False])
    for n in range(journal.KEEP + 5):
        journal.say(f"line {n}")
    assert journal.lines()[0] == "line 5" and len(journal.lines()) == journal.KEEP
    assert capsys.readouterr().out == ""


def test_a_debugged_panel_prints_as_well(monkeypatch, capsys, tmp_path):
    flag = tmp_path / "debug"
    flag.write_text("1")
    monkeypatch.setattr(journal, "DEBUG_PATH", str(flag))
    monkeypatch.setattr(journal, "_lines", [])
    monkeypatch.setattr(journal, "_loud", [None])
    journal.say("screen=IDLE")
    assert capsys.readouterr().out == "screen=IDLE\n"


def test_nothing_on_the_board_prints_but_the_journal():
    import pathlib

    root = pathlib.Path(journal.__file__).parents[1]
    for path in [root / "main.py", *(root / "heare_panel").glob("*.py")]:
        source = path.read_text(encoding="utf-8")
        if path.name in ("journal.py", "display.py"):
            continue
        assert "out=print" not in source, path
