"""What the person hears follows the chosen language; the key is English."""

from __future__ import annotations

from heare.core import i18n


def test_ukrainian_by_default_and_english_when_chosen():
    i18n.choose(lambda: "uk")
    assert i18n._("On it.") == "Беру."
    i18n.choose(lambda: "en")
    assert i18n._("On it.") == "On it."
    i18n.choose(lambda: "uk")


def test_an_unknown_language_or_line_falls_back_without_breaking():
    i18n.choose(lambda: "zz")
    assert i18n.language() == i18n.DEFAULT
    assert i18n._("A line nobody translated.") == "A line nobody translated."
    i18n.choose(lambda: 1 / 0)
    assert i18n.language() == i18n.DEFAULT
    i18n.choose(lambda: "uk")


def test_the_page_gets_the_whole_catalog():
    assert i18n.catalog("uk")["Stopped."] == "Зупинив."
