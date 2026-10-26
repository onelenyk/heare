import json

import pytest
from heare_panel import link as link_module
from heare_panel import net
from heare_panel import store as storage
from heare_panel.provision import Provisioner

from tests.firmware.fakes import FakeClock, FakeNVS


def _config(**overrides):
    wifi = {"ssid": "home", "password": "secret123"}
    brain = {"host": "192.168.1.10", "port": 9790}
    device = {"name": "kitchen"}
    wifi.update(overrides.pop("wifi", {}))
    brain.update(overrides.pop("brain", {}))
    device.update(overrides.pop("device", {}))
    config = {"version": 1, "wifi": wifi, "brain": brain, "device": device}
    if "pair" in overrides:
        config["pair"] = overrides.pop("pair")
    config.update(overrides)
    return config


def _save(**fields):
    command = {
        "cmd": "save",
        "ssid": "home",
        "password": "secret123",
        "brain_host": "192.168.1.10",
        "brain_port": "9790",
        "name": "kitchen",
        "pair_code": "ABCD2345",
    }
    command.update(fields)
    return command


class RecordingStore(storage.Store):
    def __init__(self, path, order):
        super().__init__(FakeNVS("heare"), path=str(path))
        self.order = order

    def load_config(self):
        self.order.append("load_config")
        return super().load_config()

    def save_config(self, config):
        self.order.append("save_config")
        return super().save_config(config)


class RecordingSta:
    def __init__(
        self,
        order,
        ip="192.168.7.42",
        connect_error=None,
        scan_error=None,
        networks=(),
    ):
        self.order = order
        self.ip_value = ip
        self.connect_error = connect_error
        self.scan_error = scan_error
        self.networks = list(networks)
        self.calls = []
        self.connected = False

    def connect(self, ssid, password):
        self.order.append("connect")
        self.calls.append(("connect", ssid, password))
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True
        return self.ip_value

    def scan(self):
        self.order.append("scan")
        self.calls.append(("scan",))
        if self.scan_error is not None:
            raise self.scan_error
        return list(self.networks)

    def isconnected(self):
        return self.connected

    def ip(self):
        return self.ip_value if self.connected else ""


class RecordingLink:
    def __init__(self, store, order, error, token, pid):
        self.store = store
        self.order = order
        self.error = error
        self.token = token
        self.pid = pid
        self.codes = []

    def pair(self, code):
        self.order.append("pair")
        self.codes.append(code)
        if self.error is not None:
            raise self.error
        self.store.set_part_token(self.token)
        self.store.set_pid(self.pid)
        return self.token


class RecordingLinkFactory:
    def __init__(
        self,
        order,
        errors=(),
        token="part-token-secret",
        pid="oled-abcd@esp32c3-abcd",
    ):
        self.order = order
        self.errors = list(errors)
        self.token = token
        self.pid = pid
        self.calls = []
        self.links = []

    def __call__(self, config, store):
        self.order.append("link")
        self.calls.append(config)
        error = self.errors.pop(0) if self.errors else None
        link = RecordingLink(store, self.order, error, self.token, self.pid)
        self.links.append(link)
        return link


class LeakyLinkFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, config, store):
        self.calls.append((config, store))

        class LeakyLink:
            pid = "oled-abcd@esp32c3-abcd"

            def pair(self, code):
                store.set_part_token("leaked-part-token")
                raise link_module.NeedsProvisioning("gone")

        return LeakyLink()


class Fixture:
    def __init__(self, tmp_path, sta=None, factory=None):
        self.order = []
        self.store = RecordingStore(tmp_path / "config.json", self.order)
        self.sta = sta or RecordingSta(self.order)
        self.factory = factory or RecordingLinkFactory(self.order)
        self.logs = []
        self.provisioner = Provisioner(
            self.store,
            self.sta,
            self.factory,
            clock=FakeClock(),
            out=self.logs.append,
        )


def _write_config(store, config=None):
    store.save_config(config or _config())
    store.order.clear()


def test_wrong_wifi_is_not_saved_and_does_not_pair(tmp_path):
    fixture = Fixture(
        tmp_path,
        sta=RecordingSta([], connect_error=net.WifiError("no address")),
    )
    fixture.sta.order = fixture.order

    result = fixture.provisioner.handle(_save())

    assert result == {"ok": False, "error": "wifi"}
    assert fixture.order == ["load_config", "connect"]
    assert fixture.store.load_config() is None
    assert fixture.store.get_part_token() is None
    assert fixture.store.get_pid() is None
    assert fixture.factory.calls == []


@pytest.mark.parametrize(
    "error",
    [
        link_module.NeedsProvisioning("gone"),
        link_module.Throttled("slow down"),
        link_module.LinkError("refused", 401),
        OSError("brain is down"),
    ],
)
def test_bad_pair_keeps_wifi_and_stores_no_token(tmp_path, error):
    order = []
    fixture = Fixture(tmp_path, factory=RecordingLinkFactory([], errors=[error]))
    fixture.order = order
    fixture.store.order = order
    fixture.sta.order = order
    fixture.factory.order = order

    result = fixture.provisioner.handle(_save())

    assert result == {"ok": False, "error": "pair"}
    assert fixture.order == [
        "load_config",
        "connect",
        "save_config",
        "link",
        "pair",
    ]
    saved = fixture.store.load_config()
    assert saved["wifi"] == {"ssid": "home", "password": "secret123"}
    assert saved["pair"]["code"] == "ABCD2345"
    assert fixture.store.get_part_token() is None
    assert fixture.store.get_pid() is None
    assert fixture.factory.links[0].codes == ["ABCD2345"]


def test_a_pair_failure_drops_a_token_the_attempt_leaked(tmp_path):
    fixture = Fixture(tmp_path, factory=LeakyLinkFactory())

    result = fixture.provisioner.handle(_save())

    assert result == {"ok": False, "error": "pair"}
    assert fixture.store.get_part_token() is None
    assert fixture.store.load_config()["wifi"]["ssid"] == "home"


def test_success_stores_the_part_token_and_no_machine_token(tmp_path):
    fixture = Fixture(tmp_path)

    result = fixture.provisioner.handle(_save())

    assert result == {
        "ok": True,
        "ip": "192.168.7.42",
        "pid": "oled-abcd@esp32c3-abcd",
    }
    assert fixture.order == [
        "load_config",
        "connect",
        "save_config",
        "link",
        "pair",
    ]
    assert fixture.sta.calls == [("connect", "home", "secret123")]
    assert fixture.factory.links[0].codes == ["ABCD2345"]
    assert fixture.store.get_part_token() == "part-token-secret"
    assert fixture.store.get_pid() == "oled-abcd@esp32c3-abcd"
    assert set(fixture.store.nvs.blobs) == {
        "part_token",
        "device_pid",
        "config_ver",
    }
    assert not any("machine" in key for key in fixture.store.nvs.blobs)
    assert "part-token-secret" not in json.dumps(result)
    assert "secret123" not in json.dumps(result)
    assert "ABCD2345" not in json.dumps(result)


def test_ssid_manual_wins_over_the_scan_choice(tmp_path):
    fixture = Fixture(tmp_path)

    result = fixture.provisioner.handle(
        _save(ssid="scan-choice", ssid_manual="manual-net")
    )

    assert result["ok"] is True
    assert fixture.sta.calls == [("connect", "manual-net", "secret123")]
    assert fixture.store.load_config()["wifi"]["ssid"] == "manual-net"


def test_a_pair_retry_needs_only_a_new_code(tmp_path):
    order = []
    fixture = Fixture(
        tmp_path,
        factory=RecordingLinkFactory([], errors=[link_module.LinkError("refused")]),
    )
    fixture.order = order
    fixture.store.order = order
    fixture.sta.order = order
    fixture.factory.order = order

    first = fixture.provisioner.handle(_save())
    assert first == {"ok": False, "error": "pair"}
    assert fixture.store.get_part_token() is None

    order.clear()
    fixture.sta.calls.clear()
    second = fixture.provisioner.handle({"cmd": "save", "pair_code": "bbbb3333"})

    assert second == {
        "ok": True,
        "ip": "192.168.7.42",
        "pid": "oled-abcd@esp32c3-abcd",
    }
    # Still on the network from the first try: no rejoin for a new code.
    assert fixture.sta.calls == []
    assert fixture.factory.links[-1].codes == ["BBBB3333"]
    saved = fixture.store.load_config()
    assert saved["wifi"] == {"ssid": "home", "password": "secret123"}
    assert saved["pair"]["code"] == "BBBB3333"
    assert fixture.store.get_part_token() == "part-token-secret"


@pytest.mark.parametrize(
    "command",
    [
        {"cmd": "save"},
        _save(ssid="", password="short"),
        {"cmd": "save", "pair_code": "bad"},
    ],
)
def test_a_bad_config_does_not_connect_or_save(tmp_path, command):
    fixture = Fixture(tmp_path)

    result = fixture.provisioner.handle(command)

    assert result == {"ok": False, "error": "config"}
    assert fixture.order == ["load_config"]
    assert fixture.sta.calls == []
    assert fixture.factory.calls == []


def test_handle_rejects_unknown_or_non_dict_commands(tmp_path):
    fixture = Fixture(tmp_path)

    assert fixture.provisioner.handle({"cmd": "reboot"}) == {
        "ok": False,
        "error": "command",
    }
    assert fixture.provisioner.handle("save") == {
        "ok": False,
        "error": "command",
    }
    assert fixture.order == []


def test_scan_is_limited_and_returns_only_public_fields(tmp_path):
    networks = [
        {"ssid": f"net-{index}", "rssi": -index, "auth": 4, "hidden": False}
        for index in range(15)
    ]
    fixture = Fixture(tmp_path, sta=RecordingSta([], networks=networks))
    fixture.sta.order = fixture.order

    result = fixture.provisioner.handle({"cmd": "scan"})

    assert result["ok"] is True
    assert fixture.order == ["scan"]
    assert len(result["list"]) == 12
    assert result["list"][0] == {"ssid": "net-0", "rssi": 0}
    assert set(result["list"][0]) == {"ssid", "rssi"}


def test_scan_failure_is_reported_without_secrets(tmp_path):
    fixture = Fixture(
        tmp_path,
        sta=RecordingSta([], scan_error=OSError("radio is off")),
    )
    fixture.sta.order = fixture.order

    result = fixture.provisioner.handle({"cmd": "scan"})

    assert result == {"ok": False, "error": "scan"}
    assert fixture.logs == ["save failed error=scan"]
    assert "radio is off" not in "".join(fixture.logs)


def test_forget_clears_token_pid_and_config(tmp_path):
    order = []
    fixture = Fixture(tmp_path)
    fixture.order = order
    fixture.store.order = order
    _write_config(fixture.store, _config(pair={"code": "ABCD2345"}))
    fixture.store.set_part_token("part-token-secret")
    fixture.store.set_pid("oled-abcd@esp32c3-abcd")
    assert fixture.store.load_config() is not None
    order.clear()

    result = fixture.provisioner.handle({"cmd": "forget"})

    assert result == {"ok": True}
    assert fixture.order == []
    assert fixture.store.get_part_token() is None
    assert fixture.store.get_pid() is None
    assert fixture.store.load_config() is None
    assert fixture.store.get_config_ver() == 0
    assert not (tmp_path / "config.json").exists()
    assert fixture.sta.calls == []
    assert fixture.factory.calls == []


def test_status_reports_public_values_only(tmp_path):
    fixture = Fixture(tmp_path)
    _write_config(fixture.store, _config(pair={"code": "ABCD2345"}))
    fixture.store.set_part_token("part-token-secret")
    fixture.store.set_pid("oled-abcd@esp32c3-abcd")

    status = fixture.provisioner.handle({"cmd": "status"})

    assert set(status) == {"state", "name", "ip", "ssid", "pid", "error"}
    assert status == {
        "state": "ready",
        "name": "kitchen",
        "ip": "",
        "ssid": "home",
        "pid": "oled-abcd@esp32c3-abcd",
        "error": "",
    }
    text = json.dumps(status)
    assert "secret123" not in text
    assert "ABCD2345" not in text
    assert "part-token-secret" not in text


def test_status_reflects_a_failure_and_a_success(tmp_path):
    fixture = Fixture(
        tmp_path,
        factory=RecordingLinkFactory([], errors=[link_module.LinkError("refused")]),
    )
    fixture.provisioner.handle(_save())
    failed = fixture.provisioner.handle({"cmd": "status"})
    assert failed["state"] == "pair"
    assert failed["error"] == "pair"
    assert failed["ip"] == "192.168.7.42"

    fixture.factory.errors = []
    fixture.provisioner.handle(_save())
    ready = fixture.provisioner.handle({"cmd": "status"})
    assert ready["state"] == "ready"
    assert ready["error"] == ""


def test_a_fresh_status_is_idle(tmp_path):
    fixture = Fixture(tmp_path)

    status = fixture.provisioner.handle({"cmd": "status"})

    assert status == {
        "state": "idle",
        "name": "",
        "ip": "",
        "ssid": "",
        "pid": "",
        "error": "",
    }


def test_no_secret_reaches_a_log_or_a_result(tmp_path):
    password = "hunter2-not-in-logs"
    code = "WXYZ2345"
    token = "part-token-never-logged"
    fixture = Fixture(
        tmp_path,
        factory=RecordingLinkFactory([], token=token),
    )

    result = fixture.provisioner.handle(_save(password=password, pair_code=code))
    status = fixture.provisioner.handle({"cmd": "status"})
    scan = fixture.provisioner.handle({"cmd": "scan"})

    logs = "".join(fixture.logs)
    results = json.dumps([result, status, scan])
    for secret in (password, code, token):
        assert secret not in logs
        assert secret not in results


def test_a_wifi_only_save_joins_and_stops_before_pairing(tmp_path):
    fixture = Fixture(tmp_path)

    result = fixture.provisioner.handle(
        {"cmd": "save", "ssid_manual": "home", "password": "secret123"}
    )

    assert result == {"ok": True, "ip": "192.168.7.42", "pid": ""}
    assert fixture.sta.calls == [("connect", "home", "secret123")]
    assert fixture.factory.links == []
    assert set(fixture.store.load_config()) == {"version", "wifi"}


def test_a_wifi_resave_never_redeems_the_code_left_in_the_config(tmp_path):
    fixture = Fixture(tmp_path)
    assert fixture.provisioner.handle(_save())["ok"] is True
    links_before = len(fixture.factory.links)

    result = fixture.provisioner.handle(
        {"cmd": "save", "ssid_manual": "home", "password": "secret123"}
    )

    assert result["ok"] is True
    assert len(fixture.factory.links) == links_before
    assert "brain" in fixture.store.load_config()
