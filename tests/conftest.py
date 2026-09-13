"""Shared fixtures.

The one rule here: no test touches the developer's Keychain. The key
store picks the macOS Keychain on this platform, and a test that saved a
key through the API would otherwise write a real entry under the
service ``heare`` — and a migration test would move the developer's own
keys out of their `.env`. Every test runs on the file backend unless it
opts into a fake Keychain itself (see tests/test_keys.py).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _keys_in_a_file(monkeypatch):
    monkeypatch.setenv("HEARE_KEYS_BACKEND", "file")
