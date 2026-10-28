"""Shared fixtures.

The one rule here: no test touches the developer's Keychain. The key
store picks the macOS Keychain on this platform, and a test that saved a
key through the API would otherwise write a real entry under the service
``heare``. Every test runs on the file backend unless it opts into a fake
Keychain itself.
"""

from __future__ import annotations

import pytest

from heare import keys


@pytest.fixture(autouse=True)
def _keys_in_a_file(monkeypatch):
    monkeypatch.setenv("HEARE_KEYS_BACKEND", "file")
    keys._STORED.clear()
    for name in keys.NAMES:
        monkeypatch.delenv(name, raising=False)

@pytest.fixture(autouse=True)
def _tests_speak_ukrainian(monkeypatch):
    """The product defaults to English; the old suite asserts Ukrainian words."""
    from heare import config
    from heare.core import i18n

    monkeypatch.setitem(config.DEFAULTS, "language", "uk")
    i18n.choose(lambda: "uk")
    yield
    i18n.choose(lambda: "uk")
