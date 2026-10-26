"""The setup transaction against fakes: why every refusal keeps the AP up.

The portal core's bytes are covered by ``test_portal.py``. This file is
about the decisions around them: which SSID is used, what is persisted, when
the AP stops, and what a person sees on serial. The save tests assert the
negative that matters most: a config file exists only after the station has
an address.
"""

from __future__ import annotations

import json
import os
import struct
from urllib.parse import urlencode

import pytest
from heare_panel import config as schema
from heare_panel import net, portal, portal_server, store

from tests.firmware.fakes import FakeSocket

PORTAL_PASSWORD = "portalpass1"
WIFI_PASSWORD = "hunter2-secret"


class FakeAP:
    def __init__(self, ssid="heare-panel-a1b2", password=PORTAL_PASSWORD):
        self.ssid = ssid
        self.password = password
        self.started = False
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        self.started = True
        return self.ssid

    def stop(self):
        self.stops += 1
        self.started = False


class FakeSta:
    def __init__(self, networks=None, ip="192.168.1.50", error=None):
        self.networks = list(networks or [])
        self.ip_value = ip
        self.error = error
        self.connects = []
        self.scans = 0

    def scan(self):
        self.scans += 1
        return list(self.networks)

    def connect(self, ssid, password, timeout_ms=None):
        self.connects.append((ssid, password))
        if self.error is not None:
            raise self.error
        return self.ip_value


class FakePanel:
    def __init__(self):
        self.ops = []
        self.shown = 0

    def fill(self, color):
        self.ops.append(("fill", color))

    def text(self, text, x, y, color=1):
        self.ops.append(("text", text, x, y, color))

    def show(self):
        self.shown += 1


class FakeDisplay:
    def __init__(self, width=128, height=64, available=True):
        self.width = width
        self.height = height
        self.available = available
        self._panel = FakePanel()


class FakeListener(FakeSocket):
    def __init__(self, request=b""):
        super().__init__(request)
        self.connection = FakeSocket(request)
        self.bound = None
        self.listened = None

    def bind(self, address):
        self.bound = address

    def listen(self, backlog):
        self.listened = backlog

    def accept(self):
        return self.connection, ("192.168.4.2", 50000)


class FakeDatagram(FakeSocket):
    def __init__(self, datagram=b"", address=("192.168.4.2", 5353)):
        super().__init__()
        self.datagram = datagram
        self.address = address
        self.bound = None
        self.sent_to = []

    def bind(self, address):
        self.bound = address

    def recvfrom(self, size):
        data = self.datagram
        self.datagram = b""
        return data, self.address

    def sendto(self, data, address):
        self.sent_to.append((bytes(data), address))
        return len(data)


class FakeSocketModule:
    AF_INET = 2
    SOCK_DGRAM = 2
    SOCK_STREAM = 1
    SOL_SOCKET = 1
    SO_REUSEADDR = 2

    def __init__(self, udp, tcp):
        self.u = udp
        self.t = tcp
        self.created = []

    def socket(self, family, kind):
        self.created.append((family, kind))
        return self.u if kind == self.SOCK_DGRAM else self.t


class OneShotSelector:
    def __init__(self, sockets):
        self.sockets = sockets
        self.calls = 0

    def __call__(self, readers, writers, errors, timeout):
        self.calls += 1
        if self.calls == 1:
            return (list(self.sockets), [], [])
        return ([], [], [])


def a_config(ssid="kitchen", password=WIFI_PASSWORD, host="192.168.1.10", name="kitchen"):
    return schema.validate(
        {
            "version": 1,
            "wifi": {"ssid": ssid, "password": password},
            "brain": {"host": host, "port": 9790},
            "device": {"name": name},
        }
    )


def request(method, target, body=b"", headers=()):
    lines = ["{} {} HTTP/1.1".format(method, target)]
    lines.extend("{}: {}".format(name, value) for name, value in headers)
    if body:
        lines.append("Content-Length: {}".format(len(body)))
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def form_body(
    ssid="kitchen",
    manual="",
    password=WIFI_PASSWORD,
    host="192.168.1.10",
    port="9790",
    name="kitchen",
    code="",
):
    return urlencode(
        {
            "ssid": ssid,
            "ssid_manual": manual,
            "password": password,
            "brain_host": host,
            "brain_port": port,
            "name": name,
            "pair_code": code,
        }
    ).encode()


def a_query(name="example.com", qtype=portal.DNS_TYPE_A):
    labels = bytearray()
    for label in name.split("."):
        labels.append(len(label))
        labels.extend(label.encode())
    labels.append(0)
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    return header + bytes(labels) + struct.pack("!HH", qtype, 1)


@pytest.fixture
def storage(fake_nvs, tmp_path):
    return store.Store(nvs=fake_nvs, path=str(tmp_path / "config.json"))


def build(storage, ap=None, sta=None, **options):
    logs = []
    ap = ap or FakeAP()
    sta = sta or FakeSta()
    options.setdefault("password", PORTAL_PASSWORD)
    setup = portal_server.Setup(
        storage, ap=ap, sta=sta, out=logs.append, **options
    )
    return setup, ap, sta, logs


class FixedRng:
    def __init__(self, values):
        self.values = list(values)

    def getrandbits(self, _bits):
        return self.values.pop(0)


def test_generated_password_is_8_to_12_unambiguous_characters():
    for length in (8, 10, 12):
        password = portal_server.generate_password(length=length)
        assert len(password) == length
        assert set(password) <= set(portal_server.AP_PASSWORD_ALPHABET)
        assert not set(password) & set("01iloIO")


def test_generated_password_rejects_a_length_outside_the_radio_range():
    for length in (7, 13):
        with pytest.raises(ValueError, match="8-12"):
            portal_server.generate_password(length=length)


def test_generate_password_skips_bytes_that_would_bias_the_alphabet():
    rng = FixedRng([0, 1, 2, 3, 4, 5, 6, 252, 7])

    assert portal_server.generate_password(source=rng, length=8) == "23456789"
    assert rng.values == []


def test_the_setup_ssid_is_the_ap_ssid_from_its_tag(storage):
    setup, ap, _sta, _logs = build(storage)

    assert setup.ssid == "heare-panel-a1b2"
    assert setup.ssid == ap.ssid


def test_setup_needed_opens_only_after_three_failed_associations():
    assert portal_server.setup_needed(0) is False
    assert portal_server.setup_needed(2) is False
    assert portal_server.setup_needed(3) is True
    assert portal_server.setup_needed(9) is True


def test_setup_needed_does_not_open_the_ap_for_a_brain_error_alone():
    assert portal_server.setup_needed(0, brain_error=True) is False
    assert portal_server.setup_needed(None) is False
    assert portal_server.setup_needed("many") is False


def test_get_root_serves_the_form_with_the_scan(storage):
    sta = FakeSta(networks=[{"ssid": "kitchen", "rssi": -40}])
    setup, _ap, _sta, _logs = build(storage, sta=sta)

    response = setup.handle_http(request("GET", "/"))

    assert response.startswith(b"HTTP/1.0 200 OK")
    assert b'<select name="ssid">' in response
    assert b'<option value="kitchen">kitchen</option>' in response


def test_get_scan_returns_a_bounded_json_list(storage):
    networks = [
        {"ssid": "net-{:02d}".format(index), "rssi": -30 - index}
        for index in range(20)
    ]
    setup, _ap, _sta, _logs = build(storage, sta=FakeSta(networks=networks))

    response = setup.handle_http(request("GET", "/scan"))
    body = response.split(b"\r\n\r\n", 1)[1]
    entries = json.loads(body)

    assert response.startswith(b"HTTP/1.0 200 OK")
    assert b"Content-Type: application/json" in response
    assert len(entries) == portal_server.SCAN_LIMIT
    assert entries[0] == {"ssid": "net-00", "rssi": -30}


@pytest.mark.parametrize(
    "path",
    ["/hotspot-detect.html", "/generate_204", "/connecttest.txt"],
)
def test_every_os_probe_is_redirected_to_the_portal(storage, path):
    setup, _ap, _sta, _logs = build(storage)

    response = setup.handle_http(request("GET", path))

    assert response.startswith(b"HTTP/1.0 302 Found")
    assert b"Location: http://192.168.4.1/" in response


def test_an_unknown_path_is_redirected_to_the_form(storage):
    setup, _ap, _sta, _logs = build(storage)

    response = setup.handle_http(request("GET", "/favicon.ico"))

    assert response.startswith(b"HTTP/1.0 302 Found")
    assert b"Location: /\r\n" in response


def test_a_malformed_request_is_a_bad_request_not_a_crash(storage):
    setup, _ap, _sta, _logs = build(storage)

    response = setup.handle_http(request("POST", "/save"))

    assert response.startswith(b"HTTP/1.0 400 Bad Request")
    assert b"POST requires Content-Length" in response


def test_dns_hijack_answers_with_the_ap_address(storage):
    setup, _ap, _sta, _logs = build(storage)
    query = a_query()

    answer = setup.handle_dns(query)

    assert answer == portal.dns_reply(query)
    assert answer[-4:] == bytes((192, 168, 4, 1))
    assert setup.handle_dns(b"") is None


def test_ssid_manual_wins_over_the_scanned_ssid(storage):
    sta = FakeSta(networks=[{"ssid": "kitchen", "rssi": -40}])
    setup, ap, _sta, logs = build(storage, sta=sta)
    setup.start()

    response = setup.handle_http(
        request("POST", "/save", form_body(ssid="kitchen", manual="attic"))
    )

    assert sta.connects == [("attic", WIFI_PASSWORD)]
    assert storage.load_config()["wifi"]["ssid"] == "attic"
    assert ap.started is True
    assert "sta up ip=192.168.1.50" in logs
    assert b"Wi-Fi saved" in response


def test_the_scanned_ssid_is_used_when_the_manual_box_is_empty(storage):
    setup, _ap, sta, _logs = build(storage)
    setup.start()

    setup.handle_http(
        request("POST", "/save", form_body(ssid="kitchen", manual=""))
    )

    assert sta.connects == [("kitchen", WIFI_PASSWORD)]
    assert storage.load_config()["wifi"]["ssid"] == "kitchen"


def test_an_invalid_config_shows_the_error_and_persists_nothing(storage):
    setup, ap, sta, logs = build(storage)
    setup.start()

    response = setup.handle_http(
        request("POST", "/save", form_body(host="brain.local"))
    )

    assert response.startswith(b"HTTP/1.0 200 OK")
    assert b"brain host must be an IPv4 address" in response
    assert sta.connects == []
    assert storage.load_config() is None
    assert ap.started is True
    assert not any("sta up" in line for line in logs)


def test_a_wrong_wifi_password_keeps_the_ap_and_writes_no_config(storage):
    error = net.WifiError("wifi did not connect within 15000 ms (status 1)")
    setup, ap, _sta, logs = build(storage, sta=FakeSta(error=error))
    setup.start()

    response = setup.handle_http(request("POST", "/save", form_body()))

    assert b"could not join Wi-Fi" in response
    assert b"hunter2-secret" not in response
    assert storage.load_config() is None
    assert ap.started is True
    assert not any(WIFI_PASSWORD in line for line in logs)


def test_a_saved_config_appears_only_after_the_station_has_an_address(storage):
    setup, ap, sta, logs = build(storage)
    setup.start()

    response = setup.handle_http(request("POST", "/save", form_body()))

    saved = storage.load_config()
    assert saved is not None
    assert saved["wifi"]["ssid"] == "kitchen"
    assert saved["wifi"]["password"] == WIFI_PASSWORD
    assert saved["brain"]["host"] == "192.168.1.10"
    assert saved["device"]["name"] == "kitchen"
    assert ap.started is True
    assert setup.stop_requested is False
    assert len(sta.connects) == 1
    assert b"Pairing is next." in response
    assert not any(WIFI_PASSWORD in line for line in logs)


def test_pairing_success_requests_the_ap_stop_only_after_the_save(storage):
    paired = []

    def pair(saved):
        paired.append(saved["wifi"]["ssid"])
        return True

    setup, ap, _sta, _logs = build(storage, pair=pair)
    setup.start()

    response = setup.handle_http(request("POST", "/save", form_body()))

    assert paired == ["kitchen"]
    assert setup.stop_requested is True
    assert setup.reason == "paired"
    assert b"Paired." in response
    assert ap.started is True
    setup.close()
    assert ap.started is False
    assert ap.stops == 1


def test_pairing_failure_keeps_the_ap_up_for_another_code(storage):
    setup, ap, _sta, _logs = build(storage, pair=lambda _saved: False)
    setup.start()

    response = setup.handle_http(request("POST", "/save", form_body()))

    assert setup.stop_requested is False
    assert ap.started is True
    assert b"Pairing failed" in response
    assert storage.load_config() is not None


def test_pairing_exception_keeps_the_ap_up_and_does_not_lose_the_config(storage):
    def pair(_saved):
        raise RuntimeError("brain refused")

    setup, ap, _sta, _logs = build(storage, pair=pair)
    setup.start()

    response = setup.handle_http(request("POST", "/save", form_body()))

    assert setup.stop_requested is False
    assert ap.started is True
    assert storage.load_config() is not None
    assert b"Pairing failed" in response


def test_start_prints_credentials_and_draws_both_on_the_panel(storage):
    panel = FakeDisplay()
    setup, _ap, _sta, logs = build(storage, display=panel)

    setup.start()

    assert "ap ssid=heare-panel-a1b2 password=portalpass1" in logs
    texts = [op[1] for op in panel._panel.ops if op[0] == "text"]
    assert "heare-panel-a1b2" in texts
    assert "portalpass1" in texts
    assert panel._panel.shown == 1


def test_a_small_panel_wraps_both_credentials(storage):
    panel = FakeDisplay(width=72, height=40)
    setup, _ap, _sta, _logs = build(storage, display=panel)

    setup.start()

    texts = "".join(op[1] for op in panel._panel.ops if op[0] == "text")
    assert "heare-panel-a1b2" in texts
    assert "portalpass1" in texts


def test_a_missing_display_still_prints_credentials(storage):
    setup, _ap, _sta, logs = build(storage, display=None)

    setup.start()

    assert "ap ssid=heare-panel-a1b2 password=portalpass1" in logs


def test_a_broken_panel_does_not_stop_setup(storage):
    class BrokenPanel:
        available = True
        width = 128
        height = 64

        @property
        def _panel(self):
            raise OSError("i2c went away")

    setup, _ap, _sta, logs = build(storage, display=BrokenPanel())

    assert setup.start() == "heare-panel-a1b2"
    assert "ap ssid=heare-panel-a1b2 password=portalpass1" in logs


def test_start_never_prints_the_station_password(storage):
    storage.save_config(a_config())
    setup, _ap, _sta, logs = build(storage)

    setup.start()

    assert not any(WIFI_PASSWORD in line for line in logs)
    assert not any("kitchen" in line for line in logs)


def test_close_stops_the_ap_and_is_idempotent(storage):
    setup, ap, _sta, _logs = build(storage)
    setup.start()

    setup.close()
    setup.close()

    assert ap.started is False
    assert ap.stops == 1


def test_status_and_forget_are_the_serial_commands(storage):
    storage.set_part_token("secret-token")
    storage.set_pid("oled-a1b2@esp32c3-a1b2")
    storage.save_config(a_config())
    resets = []
    lines = []

    info = portal_server.command("status", storage, out=lines.append)
    portal_server.command("forget", storage, reset=lambda: resets.append(True), out=lines.append)

    assert info == {"config": True, "part_token": True, "pid": True}
    assert resets == [True]
    assert storage.load_config() is None
    assert storage.get_part_token() is None
    assert storage.get_pid() is None
    assert storage.get_config_ver() == 0
    assert not os.path.exists(storage.path)


def test_forget_reports_cleared_without_any_value(storage):
    storage.set_part_token("secret-token")
    storage.set_pid("oled-a1b2@esp32c3-a1b2")
    storage.save_config(a_config())
    lines = []

    portal_server.forget(storage, reset=lambda: None, out=lines.append)

    joined = "\n".join(lines)
    assert "part_token=cleared" in joined
    assert "secret-token" not in joined
    assert "oled-a1b2" not in joined
    assert WIFI_PASSWORD not in joined


def test_status_prints_presence_but_never_a_value(storage):
    storage.set_part_token("secret-token")
    storage.set_pid("oled-a1b2@esp32c3-a1b2")
    storage.save_config(a_config())
    lines = []

    info = portal_server.status(storage, out=lines.append)
    joined = "\n".join(lines)

    assert info == {"config": True, "part_token": True, "pid": True}
    assert "config=present part_token=present pid=present" in joined
    for secret in ("secret-token", "oled-a1b2", WIFI_PASSWORD, "192.168.1.10", "kitchen"):
        assert secret not in joined


def test_an_unknown_command_is_refused(storage):
    with pytest.raises(ValueError, match="unknown command"):
        portal_server.command("reboot", storage)


def test_serve_handles_one_request_and_stops_after_pairing(storage, monkeypatch):
    monkeypatch.setattr(portal_server.clock, "now_ms", lambda: 0)
    body = form_body()
    listener = FakeListener(request("POST", "/save", body))
    datagram = FakeDatagram()
    module = FakeSocketModule(datagram, listener)
    selector = OneShotSelector([listener])
    setup, ap, _sta, _logs = build(
        storage,
        pair=lambda _saved: True,
        sockets=module,
        selector=selector,
    )

    reason = setup.serve(timeout_ms=None)

    assert reason == "paired"
    assert listener.bound == ("0.0.0.0", 80)
    assert listener.listened == 1
    assert datagram.bound == ("0.0.0.0", 53)
    assert listener.connection.sent.startswith(b"HTTP/1.0 200 OK")
    assert b"Paired." in listener.connection.sent
    assert ap.started is False
    assert listener.closed is True
    assert datagram.closed is True


def test_serve_times_out_and_closes_the_radio(storage, monkeypatch):
    monkeypatch.setattr(portal_server.clock, "now_ms", lambda: 0)
    datagram = FakeDatagram()
    listener = FakeListener()
    module = FakeSocketModule(datagram, listener)
    setup, ap, _sta, _logs = build(
        storage, sockets=module, selector=OneShotSelector([])
    )

    reason = setup.serve(timeout_ms=0)

    assert reason == "timeout"
    assert ap.started is False
    assert listener.closed is True
    assert datagram.closed is True


def test_complete_request_detection_waits_for_the_declared_body():
    head = b"POST /save HTTP/1.1\r\nContent-Length: 4\r\n\r\n"

    assert portal_server._complete(head + b"abc") is False
    assert portal_server._complete(head + b"abcd") is True
    assert portal_server._complete(b"GET / HTTP/1.1\r\n\r\n") is True


def test_a_second_start_does_not_add_a_second_ap_call(storage):
    setup, ap, _sta, _logs = build(storage)

    setup.start()
    setup.start()

    assert ap.starts == 1


def test_requests_still_parse_when_micropython_lacks_latin_1(storage, monkeypatch):
    def broken(_raw):
        raise LookupError("unknown encoding: latin-1")

    monkeypatch.setattr(portal_server.portal, "parse_request", broken)
    setup, _ap, sta, _logs = build(storage)
    setup.start()

    form = setup.handle_http(request("GET", "/"))
    saved = setup.handle_http(request("POST", "/save", form_body()))

    assert form.startswith(b"HTTP/1.0 200 OK")
    assert b'<select name="ssid">' in form
    assert saved.startswith(b"HTTP/1.0 200 OK")
    assert b"Wi-Fi saved" in saved
    assert sta.connects == [("kitchen", WIFI_PASSWORD)]
    assert storage.load_config() is not None
