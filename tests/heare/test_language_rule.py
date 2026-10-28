"""The language rule, kept by the code rather than by memory.

What a model reads is English; what the person hears and sees goes through
one catalog (``heare.core.i18n``, ``tr()`` in the pages and the Mac shell);
the person's own words are never translated. docs/product/language.md has
the why. These tests hold the rule, not particular lines: a Ukrainian word
typed straight into the code, a new ``_()`` or ``tr()`` with no translation,
a catalog out of step with the code — each fails here, today or next year.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest
from babel.messages.pofile import read_po

from heare.core import i18n
from heare.words import catalog

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "packages"
PO = PACKAGES / "heare-core/heare/core/locale/uk/LC_MESSAGES/heare.po"
CYRILLIC = re.compile("[А-Яа-яЁёІіЇїЄєҐґʼ]")
# «…» quotes the person's own speech: an example of what they say is data,
# in their language, wherever it stands.
QUOTED = re.compile("«[^»]*»")
PLACE = re.compile(r"\{(\w+)\}")

# Where a language may be written in the code, and why. Everything else
# says it in English and lets the catalog translate.
MAY_SPEAK = {
    # Understanding the person: a dictionary per language, never a translation.
    "heare-brain/heare/language/uk.py": "words of the person's language",
    "heare-brain/heare/language/promises.py": "how the person's language promises",
    "heare-memory/heare/memory/when.py": "dates as the person says them",
    "heare-memory/heare/memory/fts.py": "how the person's words decline, for search",
    "heare-memory/heare/memory/consolidate.py": "phrases the person says about themselves",
    "heare-voice/heare/channels/voice/hallucinations.py": "what the recogniser makes up in that language",
    "heare-voice/heare/channels/voice/wake_phrases.py": "the person's wake phrases",
    "heare-voice/heare/channels/voice/turn.py": "words that do not end a turn",
    "heare-voice/heare/channels/voice/language.py": "telling the languages apart",
    "heare-voice/heare/channels/voice/channel.py": "a letter to pick a voice by",
    "heare-brain/heare/prompt/persona.py": "the default name, a word the person says",
    "heare-eyes/heare/surroundings/sense.py": "macOS window titles, as macOS writes them",
    # Examples of the owner's words and of the answer in their language.
    "heare-brain/heare/model/judge.py": "examples of the owner's words",
    "heare-memory/heare/memory/review.py": "examples of the owner's words",
    # Data, as it is.
    "heare-memory/heare/memory/persist.py": "the mark old rows still carry",
    "heare-sources/heare/sources.py": "the default city, a name",
    # Not yet: the recall answer is built with when.py's Ukrainian dates.
    "heare-memory/heare/memory/search.py": "the Ukrainian fallback of recall's answer",
}


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)):
            found.add(id(body[0].value))
    return found


def _speaks(text: str) -> bool:
    return bool(CYRILLIC.search(QUOTED.sub("", text)))


def test_no_language_is_typed_into_the_python():
    stray = []
    for path in sorted(PACKAGES.rglob("*.py")):
        name = str(path.relative_to(PACKAGES))
        if "__pycache__" in path.parts or name in MAY_SPEAK:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        stray += [f"{name}:{node.lineno}: {node.value[:60]!r}" for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and id(node) not in docs and _speaks(node.value)]
    assert not stray, ("say it in English through _() or N_(); the catalog "
                       "translates (docs/product/language.md):\n" + "\n".join(stray))


def test_everything_allowed_to_speak_still_exists():
    """A stale exception would let a new file hide behind an old name."""
    gone = [name for name in MAY_SPEAK if not (PACKAGES / name).is_file()]
    assert not gone


# A page and the Mac shell: comments may speak, code may not. The few words
# below are not translations: a language's own name, and an old line the
# terminal still prints.
PAGES = [
    PACKAGES / "heare-brain/heare/api/page.html",
    PACKAGES / "heare-keeper/heare/installer.html",
    *sorted((ROOT / "apps/mac/Sources").rglob("*.swift")),
]
PAGE_MAY_SAY = ("Українська", "агент|agent")
_COMMENTS = re.compile(r"/\*.*?\*/|<!--.*?-->|(?<![:\"'])//[^\n]*", re.S)


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name)
def test_no_language_is_typed_into_a_page(path: Path):
    code = _COMMENTS.sub("", path.read_text(encoding="utf-8"))
    for allowed in PAGE_MAY_SAY:
        code = code.replace(allowed, "")
    stray = [line.strip()[:80] for line in code.splitlines() if _speaks(line)]
    assert not stray, "mark it with tr() or data-t:\n" + "\n".join(stray)


@pytest.fixture(scope="module")
def extracted(tmp_path_factory) -> set[str]:
    """What the code marks today, found the way make i18n finds it."""
    pot = tmp_path_factory.mktemp("i18n") / "heare.pot"
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "extract", "-F", "babel.cfg",
         "-o", str(pot), "--no-location", "--omit-header", "-k", "_", "-k", "N_", "."],
        cwd=ROOT, capture_output=True, check=True,
        env={"PYTHONPATH": str(ROOT / "scripts"), "PATH": ""})
    with pot.open("rb") as handle:
        found = {m.id if isinstance(m.id, str) else m.id[0] for m in read_po(handle) if m.id}
    # A finder that finds nothing would pass every check below.
    assert {"On it.", "Brain", "Next →", "Open heare"} <= found
    return found


@pytest.fixture(scope="module")
def ukrainian():
    with PO.open("rb") as handle:
        return read_po(handle)


def test_the_catalog_knows_every_line_the_code_marks(ukrainian, extracted):
    known = {m.id if isinstance(m.id, str) else m.id[0] for m in ukrainian if m.id}
    missing = extracted - known
    assert not missing, "run make i18n and translate:\n" + "\n".join(sorted(missing))


def test_every_line_has_its_ukrainian_with_the_same_places(ukrainian):
    bad = []
    for message in ukrainian:
        if not message.id:
            continue
        ids = [message.id] if isinstance(message.id, str) else list(message.id)
        texts = [message.string] if isinstance(message.string, str) else list(message.string)
        if not all(texts):
            bad.append(f"no translation: {ids[0]}")
            continue
        wanted = set(PLACE.findall(ids[0]))
        bad += [f"{{places}} differ: {ids[0]} → {text}" for text in texts
                if set(PLACE.findall(text)) != wanted]
        if len(ids) > 1 and len(texts) != 3:
            bad.append(f"Ukrainian has three plural forms: {ids[0]}")
    assert not bad, "\n".join(bad)


def test_the_compiled_catalog_is_the_source(ukrainian):
    compiled = catalog("uk")
    stale = [m.id for m in ukrainian
             if isinstance(m.id, str) and m.id and compiled.get(m.id) != m.string]
    assert not stale, "run make i18n:\n" + "\n".join(stale[:10])


def test_english_is_the_key_so_english_needs_no_catalog():
    i18n.choose(lambda: "en")
    try:
        assert i18n._("On it.") == "On it."
    finally:
        i18n.choose(lambda: "uk")
    assert i18n._("On it.") == "Беру."


async def test_what_the_voice_reads_is_english(tmp_path, monkeypatch):
    """The persona with no data of the person: rules, laws, the body."""
    from heare.prompt import persona

    monkeypatch.setattr(persona, "IDENTITY", tmp_path / "none.json")
    text = await persona.prompt({}, {"p": {
        "name": "panel", "about": "a board", "state": "approved", "now": "face",
        "skills": [{"name": "screen_info", "about": "What is on the screen", "effect": "read"}]}})
    assert not _speaks(text), [line for line in text.splitlines() if _speaks(line)]


def test_the_tools_the_voice_is_offered_are_english():
    from heare.tools import schemas

    offered = [schemas._RECALL, schemas._STOP, schemas._DELEGATE, schemas._REMIND,
               schemas._LIST_REMINDERS, schemas._CANCEL_REMINDER, schemas._READ_BODY]
    assert not [s["function"]["name"] for s in offered if _speaks(str(s))]


def test_the_mac_gets_the_same_catalog_as_strings(tmp_path):
    """build.sh turns the catalog into the shell's Localizable.strings."""
    out = tmp_path / "Localizable.strings"
    subprocess.run([sys.executable, str(ROOT / "scripts/po_strings.py"), str(PO), str(out)],
                   check=True)
    text = out.read_text(encoding="utf-8")
    assert '"Quit" = "Вийти";' in text
    assert '"in the Trash: {path}" = "у Кошику: {path}";' in text
