"""Turn a gettext catalog into the Mac's Localizable.strings.

    python scripts/po_strings.py <heare.po> <out/Localizable.strings>

One catalog for every surface: the shell reads the same lines the brain and
the page do (apps/mac/Sources/heare/Words.swift). Standard library only.
"""

from __future__ import annotations

import gettext
import sys
from pathlib import Path


def strings(mo: Path) -> str:
    with mo.open("rb") as handle:
        catalog = gettext.GNUTranslations(handle)
    quote = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'  # noqa: E731
    # Plural forms (tuple keys) are the page's; the shell says none.
    single = {k: v for k, v in catalog._catalog.items() if isinstance(k, str) and k and v}
    lines = [f"{quote(k)} = {quote(v)};" for k, v in sorted(single.items())]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(strings(Path(sys.argv[1]).with_suffix(".mo")), encoding="utf-8")
