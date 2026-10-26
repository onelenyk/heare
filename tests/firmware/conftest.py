"""Shared fixtures for the host-side ESP32-C3 panel tests.

The firmware is copied to the board as ``heare_panel/``, so tests import it
exactly as the device does: with ``firmware/esp32-c3`` on ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.firmware.fakes import (
    FakeCanvas,
    FakeClock,
    FakeI2C,
    FakeNVS,
    FakeSocket,
    FakeWLAN,
)

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware" / "esp32-c3"
if str(FIRMWARE) not in sys.path:
    sys.path.insert(0, str(FIRMWARE))


@pytest.fixture
def fake_socket() -> FakeSocket:
    return FakeSocket()


@pytest.fixture
def fake_canvas() -> FakeCanvas:
    return FakeCanvas()


@pytest.fixture
def fake_i2c() -> FakeI2C:
    return FakeI2C()


@pytest.fixture
def fake_wlan() -> FakeWLAN:
    return FakeWLAN()


@pytest.fixture
def fake_nvs() -> FakeNVS:
    return FakeNVS()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


__all__ = [
    "FakeCanvas",
    "FakeClock",
    "FakeI2C",
    "FakeNVS",
    "FakeSocket",
    "FakeWLAN",
    "fake_canvas",
    "fake_clock",
    "fake_i2c",
    "fake_nvs",
    "fake_socket",
    "fake_wlan",
]
