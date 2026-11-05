"""Turn a gettext catalog into the Mac's Localizable.strings, or the
phone's words/<lang>.json.

    python scripts/po_strings.py <heare.po> <out/Localizable.strings>
    python scripts/po_strings.py <heare.po> <out/uk.json>

One catalog for every surface: the shell reads the same lines the brain and
the page do (apps/mac/Sources/heare/Words.swift). Standard library only.
"""

from __future__ import annotations

import gettext
import json
import sys
from pathlib import Path


def lines(mo: Path) -> dict[str, str]:
    with mo.open("rb") as handle:
        catalog = gettext.GNUTranslations(handle)
    # Plural forms (tuple keys) are the page's; the shells say none.
    return {k: v for k, v in catalog._catalog.items() if isinstance(k, str) and k and v}


def strings(mo: Path) -> str:
    quote = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'  # noqa: E731
    said = [f"{quote(k)} = {quote(v)};" for k, v in sorted(lines(mo).items())]
    return "\n".join(said) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    mo = Path(sys.argv[1]).with_suffix(".mo")
    if out.suffix == ".json":
        out.write_text(json.dumps(lines(mo), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    else:
        out.write_text(strings(mo), encoding="utf-8")
