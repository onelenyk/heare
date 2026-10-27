"""Babel's extractor for the page: the words the person sees there.

    [heare_babel.page: packages/heare-brain/heare/api/page.html]   (babel.cfg)

It finds tr("…") and trn(n, "…", "…") in the script, and in the markup the
text of an element marked data-t and the attribute marked data-t-title,
data-t-placeholder or data-t-aria. Only double-quoted keys: one form, one
pattern.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import IO, Iterator

_KEY = r'"((?:[^"\\]|\\.)*)"'
TR = re.compile(r"\btr\(\s*" + _KEY)
TRN = re.compile(r"\btrn\([^,]+,\s*" + _KEY + r"\s*,\s*" + _KEY)
MARKS = {"data-t-title": "title", "data-t-placeholder": "placeholder", "data-t-aria": "aria-label"}


def _unquote(s: str) -> str:
    return s.encode().decode("unicode_escape").encode("latin-1").decode("utf-8")


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[int, str]] = []
        self._open: list[tuple[int, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        named = dict(attrs)
        line = self.getpos()[0]
        for mark, attr in MARKS.items():
            if mark in named and named.get(attr):
                self.found.append((line, str(named[attr])))
        if "data-t" in named:
            self._open.append((line, []))

    def handle_data(self, data: str) -> None:
        if self._open:
            self._open[-1][1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._open:
            line, text = self._open.pop()
            if "".join(text).strip():
                self.found.append((line, "".join(text).strip()))


def page(fileobj: IO[bytes], keywords: object, comment_tags: object,
         options: object) -> Iterator[tuple[int, str, object, list[str]]]:
    text = fileobj.read().decode("utf-8")
    line = lambda at: text.count("\n", 0, at) + 1  # noqa: E731
    for m in TR.finditer(text):
        yield line(m.start()), "gettext", _unquote(m.group(1)), []
    for m in TRN.finditer(text):
        yield line(m.start()), "ngettext", (_unquote(m.group(1)), _unquote(m.group(2))), []
    markup = _Markup()
    markup.feed(text.split("<script>", 1)[0])
    for at, key in markup.found:
        yield at, "gettext", key, []


def swift(fileobj: IO[bytes], keywords: object, comment_tags: object,
          options: object) -> Iterator[tuple[int, str, object, list[str]]]:
    """tr("…") in the Mac shell (apps/mac, Words.swift)."""
    text = fileobj.read().decode("utf-8")
    for m in TR.finditer(text):
        yield text.count("\n", 0, m.start()) + 1, "gettext", _unquote(m.group(1)), []
