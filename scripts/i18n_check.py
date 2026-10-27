"""Fail when a line the person hears has no Ukrainian, or nothing is compiled.

    make i18n-check

The standard library only: it reads the .po as text.
"""

from __future__ import annotations

import sys
from pathlib import Path


def untranslated(po: Path) -> list[str]:
    missing: list[str] = []
    key = None
    for line in po.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):
            key = line[6:].strip('"')
        elif line.startswith("msgstr") and key:
            # msgstr, or msgstr[n] for each plural form.
            if line.split(" ", 1)[1].strip() == '""':
                missing.append(key)
            if not line.startswith("msgstr["):
                key = None
    return missing


def main(locale: Path) -> int:
    po = locale / "uk" / "LC_MESSAGES" / "heare.po"
    mo = po.with_suffix(".mo")
    if not po.is_file():
        print(f"немає {po} — make i18n", file=sys.stderr)
        return 1
    bad = 0
    if not mo.is_file():
        print(f"немає {mo} — make i18n", file=sys.stderr)
        bad = 1
    # A catalog marked fuzzy is skipped by the compiler, silently: every
    # line then comes out in English (27 October).
    if "#, fuzzy" in po.read_text(encoding="utf-8").split("msgid", 2)[1]:
        print(f"{po} позначено fuzzy — компілятор його пропустить", file=sys.stderr)
        bad = 1
    for key in untranslated(po):
        print(f"без перекладу uk: {key}", file=sys.stderr)
        bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
