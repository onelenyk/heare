from __future__ import annotations

from heare import config
from heare.core import i18n
from heare.language import en, for_language, uk

SHIPPED_DEFAULT = (config.DEFAULTS["language"], i18n.DEFAULT)


def test_english_and_ukrainian_are_chosen_by_code():
    assert for_language("en") is en
    assert for_language("uk") is uk
    assert for_language("EN") is en
    assert for_language("") is uk


def test_english_has_the_same_words_as_the_core_expects():
    for name in uk.__all__:
        assert hasattr(en, name), name


def test_the_shipped_default_is_english():
    assert SHIPPED_DEFAULT == ("en", "en")
