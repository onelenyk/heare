from types import SimpleNamespace

import pytest
from heare_panel import net

from tests.firmware.fakes import FakeWLAN


class IpWLAN(FakeWLAN):
    def __init__(self, ip="192.168.1.50", **kwargs):
        super().__init__(**kwargs)
        self.address = ip

    def ifconfig(self):
        return (self.address, "255.255.255.0", self.address, "192.168.1.1")


class NeverConnects(FakeWLAN):
    def __init__(self):
        super().__init__()
        self.polls = 0

    def connect(self, ssid=None, password=None):
        self.calls.append(("connect", ssid, password))

    def status(self):
        self.polls += 1
        return super().status()


def _configs(radio):
    return [call[1] for call in radio.calls if call[0] == "config"]


def test_sta_connects_and_returns_the_first_ifconfig_address():
    radio = IpWLAN(ip="192.168.4.23")
    sta = net.Sta(radio)

    assert sta.connect("home", "secret123") == "192.168.4.23"
    assert ("active", True) in radio.calls
    assert ("connect", "home", "secret123") in radio.calls
    assert sta.isconnected() is True
    assert sta.status() == "connected"
    assert sta.ip() == "192.168.4.23"


def test_sta_sets_power_mode_and_reconnects():
    radio = FakeWLAN()

    net.Sta(radio).connect("home", "secret123")

    configs = _configs(radio)
    assert {"pm": 0} in configs
    assert {"reconnects": -1} in configs


def test_sta_can_be_configured_without_connecting():
    radio = FakeWLAN()

    net.Sta(radio).configure()

    configs = _configs(radio)
    assert {"pm": 0} in configs
    assert {"reconnects": -1} in configs
    assert not any(call[0] == "connect" for call in radio.calls)


def test_sta_uses_the_board_pm_none_constant_when_network_is_present(monkeypatch):
    monkeypatch.setattr(net, "network", SimpleNamespace(PM_NONE=7))
    radio = FakeWLAN()

    net.Sta(radio).connect("home", "secret123")

    assert {"pm": 7} in _configs(radio)


def test_sta_connects_to_an_open_network_without_a_password():
    radio = FakeWLAN()

    net.Sta(radio).connect("cafe", "")

    assert ("connect", "cafe", None) in radio.calls


def test_sta_polls_until_it_times_out_then_raises():
    radio = NeverConnects()
    sleeps = []

    sta = net.Sta(radio, sleep=sleeps.append)

    with pytest.raises(net.WifiError) as error:
        sta.connect("home", "wrongpass", timeout_ms=350)

    assert "350" in str(error.value)
    assert "wrongpass" not in str(error.value)
    assert radio.polls == 3
    assert sleeps == [net.POLL_MS, net.POLL_MS]
    assert ("connect", "home", "wrongpass") in radio.calls


def test_sta_with_a_zero_timeout_still_makes_one_poll():
    radio = NeverConnects()
    sleeps = []

    with pytest.raises(net.WifiError):
        net.Sta(radio, sleep=sleeps.append).connect("home", "secret123", timeout_ms=0)

    assert radio.polls == 1
    assert sleeps == []


def test_sta_disconnects():
    radio = FakeWLAN(connected=True)
    sta = net.Sta(radio)

    sta.disconnect()

    assert ("disconnect",) in radio.calls
    assert sta.isconnected() is False


def test_scan_parses_bytes_hidden_and_invalid_utf8():
    radio = FakeWLAN(
        networks=[
            (b"home", b"\x00" * 6, 6, -42, 3, False),
            (b"", b"\x01" * 6, 1, -88, 0, True),
            (b"caf\xff", b"\x02" * 6, 11, -60, 4, False),
        ]
    )

    assert net.Sta(radio).scan() == [
        {"ssid": "home", "rssi": -42, "auth": 3, "hidden": False},
        {"ssid": "", "rssi": -88, "auth": 0, "hidden": True},
        {"ssid": "caf\ufffd", "rssi": -60, "auth": 4, "hidden": False},
    ]


def test_scan_accepts_an_already_decoded_ssid():
    radio = FakeWLAN(networks=[("home", b"\x00" * 6, 6, -42, 3, False)])

    assert net.Sta(radio).scan()[0]["ssid"] == "home"


def test_sta_creates_its_radio_from_the_network_module(monkeypatch):
    radio = IpWLAN()

    class Network:
        STA_IF = 0
        AP_IF = 1
        PM_NONE = 0
        AUTH_WPA2_PSK = 3

        def __init__(self):
            self.flags = []

        def WLAN(self, flag):
            self.flags.append(flag)
            return radio

    network = Network()
    monkeypatch.setattr(net, "network", network)

    sta = net.Sta()

    assert network.flags == [0]
    assert sta.radio is radio


def test_sta_without_a_radio_or_network_raises(monkeypatch):
    monkeypatch.setattr(net, "network", None)

    with pytest.raises(net.WifiError):
        net.Sta()


class MissesTheApOnce(FakeWLAN):
    """The first association scan misses the AP; the second one joins."""

    def __init__(self):
        super().__init__()
        self.joins = 0

    def connect(self, ssid=None, password=None):
        self.calls.append(("connect", ssid, password))
        self.joins += 1

    def isconnected(self):
        return self.joins >= 2

    def status(self):
        return 201 if self.joins < 2 else 1010


def test_sta_starts_the_join_over_when_the_ap_was_not_found():
    radio = MissesTheApOnce()

    assert net.Sta(radio, sleep=lambda ms: None).connect("home", "secret123")

    assert radio.joins == 2
    assert ("disconnect",) in radio.calls


def test_sta_does_not_retry_a_wrong_password():
    radio = NeverConnects()
    radio.status = lambda: 202

    with pytest.raises(net.WifiError):
        net.Sta(radio, sleep=lambda ms: None).connect("home", "x", timeout_ms=500)

    assert [c for c in radio.calls if c[0] == "connect"] == [("connect", "home", "x")]


def test_sta_names_the_board_for_mdns_before_the_radio_comes_up(monkeypatch):
    order = []

    class Radio(FakeWLAN):
        def active(self, value=None):
            order.append(("active", value))
            return super().active(value)

    monkeypatch.setattr(
        net, "network", SimpleNamespace(hostname=lambda n: order.append(("host", n)))
    )

    net.Sta(Radio())

    assert order[:2] == [("host", "heare-panel"), ("active", True)]
