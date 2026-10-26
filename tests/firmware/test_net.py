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


class MacWLAN(FakeWLAN):
    def config(self, *args, **kwargs):
        if args == ("mac",):
            return b"\x14\x63\x93\x90\x23\x64"
        return super().config(*args, **kwargs)


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


def test_ap_start_configures_wpa2_and_stops():
    radio = FakeWLAN()
    ap = net.Ap(radio, "secret123", tag="a1b2c3d4")

    assert ap.ssid == "heare-panel-a1b2"
    assert ap.authmode == 3
    assert ap.start() == "heare-panel-a1b2"
    assert ("active", True) in radio.calls
    assert {
        "essid": "heare-panel-a1b2",
        "password": "secret123",
        "authmode": 3,
        "channel": 6,
        "max_clients": 2,
    } in _configs(radio)
    assert ap.started is True

    ap.stop()

    assert ("active", False) in radio.calls
    assert ap.started is False


def test_ap_uses_the_board_authmode_constant_when_network_is_present(monkeypatch):
    monkeypatch.setattr(net, "network", SimpleNamespace(AUTH_WPA2_PSK=9))
    radio = FakeWLAN()

    ap = net.Ap(radio, "secret123", tag="a1b2")
    ap.start()

    assert ap.authmode == 9
    assert _configs(radio)[0]["authmode"] == 9


def test_ap_takes_the_last_four_of_a_full_mac():
    ap = net.Ap(FakeWLAN(), "secret123", tag="14:63:93:90:23:64")

    assert ap.ssid == "heare-panel-2364"


def test_ap_derives_the_tag_from_the_radio_mac():
    ap = net.Ap(MacWLAN(), "secret123")

    assert ap.ssid == "heare-panel-2364"


def test_ap_prefers_the_station_mac_over_the_ap_mac(monkeypatch):
    ap_radio = FakeWLAN()

    class Network:
        STA_IF = 0
        AP_IF = 1
        AUTH_WPA2_PSK = 3

        def WLAN(self, flag):
            return MacWLAN() if flag == 0 else ap_radio

    monkeypatch.setattr(net, "network", Network())

    ap = net.Ap(ap_radio, "secret123")

    assert ap.ssid == "heare-panel-2364"


def test_ap_falls_back_to_zeroes_when_the_radio_has_no_mac():
    ap = net.Ap(FakeWLAN(), "secret123")

    assert ap.ssid == "heare-panel-0000"


def test_ap_creates_its_radio_from_the_network_module(monkeypatch):
    radio = FakeWLAN()

    class Network:
        AP_IF = 1
        AUTH_WPA2_PSK = 3

        def __init__(self):
            self.flags = []

        def WLAN(self, flag):
            self.flags.append(flag)
            return radio

    network = Network()
    monkeypatch.setattr(net, "network", network)

    ap = net.Ap(password="secret123", tag="a1b2")

    assert network.flags == [1]
    assert ap.radio is radio


@pytest.mark.parametrize("password", ["", "short12", "1234567890123", None, 12345678])
def test_ap_refuses_a_password_outside_the_wpa2_window(password):
    with pytest.raises(ValueError):
        net.Ap(FakeWLAN(), password, tag="a1b2")


@pytest.mark.parametrize("tag", ["", "abc", "zzzz", "a1b2-"])
def test_ap_refuses_a_tag_that_is_not_hex(tag):
    with pytest.raises(ValueError):
        net.Ap(FakeWLAN(), "secret123", tag=tag)
