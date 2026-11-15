"""What a task changed: the card the page draws and the sentence it is told."""

from __future__ import annotations

import html

from heare.reach import changes


def _file(name: str, added: int = 1, removed: int = 1, patch: str = "") -> dict:
    return {"file": name, "additions": added, "deletions": removed,
            "patch": patch or "Index: x\n", "status": "modified"}


def test_one_file_is_named_and_counted():
    assert changes.said([_file("src/app.py", 3, 1)]) == (
        "Змінив app.py: +3 −1.")


def test_many_files_are_counted_without_a_number_before_a_noun():
    assert changes.said([_file("a.py", 2, 1), _file("b.py", 2, 1)]) == (
        "Змінив: +4 −2.")


def test_the_card_names_one_file_and_calls_many_changes():
    assert changes.title([_file("a.py")]) == "a.py"
    assert changes.title([_file("a.py"), _file("b.py")]) == "Зміни"


def test_the_card_is_html_a_person_can_read():
    card = changes.card([_file("src/app.py", 3, 1, "-old\n+new\n")])

    assert card["kind"] == "html"
    assert card["title"] == "app.py"
    assert card["say"] == "Змінив app.py: +3 −1."
    assert '<div class="kv"><span>src/app.py</span><b>+3 −1</b></div>' in card["html"]
    assert "<pre><code>" in card["html"]
    assert "-old\n+new" in card["html"]


def test_a_name_or_a_patch_cannot_become_markup():
    card = changes.card([_file("<script>alert(1)</script>", 1, 1,
                               "+<img src=x onerror=alert(1)>")])

    assert "<script>" not in card["html"]
    assert "&lt;script&gt;" in card["html"]
    assert "<img" not in card["html"]
    assert "&lt;img" in card["html"]


def test_a_huge_patch_is_cut_and_says_nothing_more():
    card = changes.card([_file("big.py", 900, 0, "+" + "x" * 5000)])

    assert "\n…</code></pre>" in card["html"]
    assert len(card["html"]) < changes.PATCH_CHARS + 500


def test_caches_and_bytecode_are_not_work():
    files = [
        {"file": "__pycache__/calc.cpython-314.pyc", "additions": 0,
         "deletions": 0, "patch": ""},
        {"file": "node_modules/x/y.js", "additions": 5, "deletions": 0,
         "patch": "+x"},
        {"file": ".venv/lib/z.py", "additions": 2, "deletions": 0, "patch": "+y"},
        {"file": "calc.py", "additions": 1, "deletions": 1, "patch": "-a\n+b\n"},
    ]

    assert [found["file"] for found in changes.relevant(files)] == ["calc.py"]
    assert changes.said(files) == "Змінив calc.py: +1 −1."
    assert "pycache" not in changes.card(files)["html"]


def test_work_that_only_touched_caches_has_nothing_to_say():
    files = [{"file": "__pycache__/a.pyc", "additions": 0, "deletions": 0}]

    assert changes.relevant(files) == []
    assert changes.said(files) == ""
    assert changes.title(files) == "Зміни"


def test_past_the_sixth_file_the_card_counts_the_rest():
    files = [_file(f"f{i}.py") for i in range(changes.FILES + 2)]

    card = changes.card(files)

    assert "… і ще 2" in card["html"]
    assert card["html"].count('class="kv"') == changes.FILES + 1
    assert html.unescape(card["html"]).count("<pre><code>") == changes.FILES
