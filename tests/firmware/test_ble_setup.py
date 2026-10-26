import json

import pytest
from heare_panel import ble_setup, clock


class FakeUUID:
    def __init__(self, value):
        self.value = value


class FakeBLE:
    def __init__(self):
        self.active_calls = []
        self.config_calls = []
        self.services = None
        self.registrations = 0
        self.buffers = {}
        self.buffer_sizes = {}
        self.irq_handler = None
        self.advertise_calls = []
        self.advertising = False
        self.adv_data = b""
        self.resp_data = b""
        self.writes = []
        self.disconnections = []
        self.conn_handle = 1

    def active(self, value=None):
        self.active_calls.append(value)
        return bool(value)

    def config(self, *args, **kwargs):
        self.config_calls.append((args, dict(kwargs)))

    def gatts_register_services(self, services):
        self.registrations += 1
        self.services = services
        handles = []
        handle = 1
        for _uuid, characteristics in services:
            service_handles = []
            for _characteristic in characteristics:
                service_handles.append(handle)
                handle += 1
            handles.append(tuple(service_handles))
        return tuple(handles)

    def gatts_set_buffer(self, handle, size, append=False):
        self.buffers[handle] = bytearray()
        self.buffer_sizes[handle] = (size, append)

    def irq(self, handler):
        self.irq_handler = handler

    def gatts_read(self, handle):
        data = bytes(self.buffers.get(handle, b""))
        if self.buffer_sizes.get(handle, (0, False))[1]:
            self.buffers[handle] = bytearray()
        return data

    def gatts_write(self, handle, data, send_update=False):
        self.buffers[handle] = bytearray(data)
        self.writes.append((handle, bytes(data)))

    def gap_advertise(self, interval_us, adv_data=None, *, resp_data=None,
                      connectable=True):
        self.advertise_calls.append(
            (interval_us, adv_data, resp_data, connectable)
        )
        self.advertising = interval_us is not None
        if adv_data is not None:
            self.adv_data = bytes(adv_data)
        if resp_data is not None:
            self.resp_data = bytes(resp_data)

    def gap_disconnect(self, conn_handle):
        self.disconnections.append(conn_handle)
        return True

    def connect(self, conn_handle=None):
        if conn_handle is not None:
            self.conn_handle = conn_handle
        self.irq_handler(1, (self.conn_handle, 0, b"\x00" * 6))

    def disconnect(self, conn_handle=None):
        self.irq_handler(
            2, (conn_handle or self.conn_handle, 0, b"\x00" * 6)
        )

    def write(self, data, handle=None):
        if handle is None:
            handle = self._cmd_handle()
        self.buffers[handle].extend(data)
        self.irq_handler(3, (self.conn_handle, handle))

    def _cmd_handle(self):
        return 3


class FakeBluetooth:
    def __init__(self):
        self.ble = FakeBLE()

    def UUID(self, value):
        return FakeUUID(value)

    def BLE(self):
        return self.ble

    def __getattr__(self, name):
        return getattr(self.ble, name)

    FLAG_READ = 0x0002
    FLAG_WRITE = 0x0008
    FLAG_NOTIFY = 0x0010


class BleClock:
    def __init__(self, fake):
        self.fake = fake

    def now_ms(self):
        return self.fake.now_ms

    def elapsed(self, start, now):
        return clock.elapsed(start, now)


@pytest.fixture
def bluetooth(monkeypatch, fake_clock):
    module = FakeBluetooth()
    monkeypatch.setattr(ble_setup, "bluetooth", module)
    monkeypatch.setattr(ble_setup, "clock", BleClock(fake_clock))
    return module


def _start(module, handler=None, code="1234", **kwargs):
    calls = []
    logs = []

    def default_handler(command):
        calls.append(command)
        return {"id": command.get("id"), "ok": True, "error": ""}

    setup = ble_setup.BleSetup(
        kwargs.pop("name", "heare-panel-ab12"),
        handler or default_handler,
        code,
        status_provider=kwargs.pop("status_provider", None),
        scan_provider=kwargs.pop("scan_provider", None),
        window_ms=kwargs.pop("window_ms", 600_000),
        out=logs.append,
        **kwargs,
    )
    setup.start()
    return setup, calls, logs


def _connect(module, setup):
    if not setup.connected:
        module.connect()
        setup.poll()


def _frame(module, setup, command):
    _connect(module, setup)
    body = json.dumps(command).encode()
    chunks = [body[i:i + ble_setup.MAX_CHUNK]
              for i in range(0, len(body), ble_setup.MAX_CHUNK)]
    total = len(chunks)
    for seq, chunk in enumerate(chunks):
        module.write(bytes((seq, total)) + chunk)
        setup.poll()


def _auth(module, setup, code="1234"):
    _frame(module, setup, {"id": 1, "cmd": "auth", "code": code})


def _result(module, setup):
    return json.loads(module.gatts_read(setup.handles[3]))


def _status(module, setup):
    return json.loads(module.gatts_read(setup.handles[0]))


def _scan(module, setup):
    return json.loads(module.gatts_read(setup.handles[1]))


def test_advertising_has_flags_and_the_service_uuid(bluetooth):
    _setup, _calls, logs = _start(bluetooth)
    ble = bluetooth.ble

    assert ble.advertising
    assert ble.adv_data[:3] == b"\x02\x01\x06"
    assert ble.adv_data[3:6] == bytes((17, 0x07)) + b"\x11"
    assert ble.adv_data[5:] == ble_setup.SERVICE_BYTES_LE
    assert b"heare-panel-ab12" in ble.resp_data
    assert ("code=1234") in logs


def test_service_characteristics_and_buffer_are_registered(bluetooth):
    setup, _calls, _logs = _start(bluetooth)
    ble = bluetooth.ble

    service_uuid, characteristics = ble.services[0]
    assert service_uuid.value == ble_setup.SERVICE_UUID
    assert [characteristic[0].value for characteristic in characteristics] == [
        ble_setup.STATUS_UUID,
        ble_setup.SCAN_UUID,
        ble_setup.CMD_UUID,
        ble_setup.RESULT_UUID,
    ]
    assert ble.buffer_sizes[setup.handles[2]] == (512, True)
    assert ble.config_calls[-1][1] == {"gap_name": "heare-panel-ab12"}


def test_start_is_idempotent(bluetooth):
    setup, _calls, logs = _start(bluetooth)

    assert setup.start() is False
    assert bluetooth.ble.registrations == 1
    assert logs.count("code=1234") == 1


def test_auth_is_required_for_every_other_command(bluetooth):
    setup, calls, _logs = _start(bluetooth)

    _frame(bluetooth, setup, {"id": 9, "cmd": "save", "ssid": "home"})

    assert _result(bluetooth, setup) == {
        "id": 9, "ok": False, "error": "auth"
    }
    assert calls == []


def test_three_wrong_codes_stop_advertising(bluetooth):
    setup, calls, logs = _start(bluetooth)

    for identifier in (1, 2, 3):
        _frame(
            bluetooth, setup,
            {"id": identifier, "cmd": "auth", "code": "9999"},
        )

    assert calls == []
    assert setup.running is False
    assert bluetooth.ble.active_calls[-1] is False
    assert bluetooth.ble.advertising is False
    assert "ble auth_failed" in logs


def test_a_good_code_after_two_failures_resets_the_count(bluetooth):
    setup, _calls, _logs = _start(bluetooth)
    for _ in range(2):
        _auth(bluetooth, setup, "9999")
    assert setup.running is True

    _auth(bluetooth, setup)
    assert _result(bluetooth, setup)["ok"] is True
    assert setup.running is True


def test_a_command_runs_only_after_auth_and_echoes_the_id(bluetooth):
    setup, calls, _logs = _start(bluetooth)
    _auth(bluetooth, setup)

    _frame(
        bluetooth, setup,
        {"id": 42, "cmd": "save", "ssid": "home", "password": "hunter2"},
    )

    assert calls[-1]["cmd"] == "save"
    assert _result(bluetooth, setup) == {
        "id": 42, "ok": True, "error": ""
    }
    assert setup.running is False


def test_forget_calls_the_handler_and_keeps_the_window(bluetooth):
    setup, calls, _logs = _start(bluetooth)
    _auth(bluetooth, setup)

    _frame(bluetooth, setup, {"id": 7, "cmd": "forget"})

    assert calls[-1]["cmd"] == "forget"
    assert _result(bluetooth, setup)["ok"] is True
    assert setup.running is True


def test_handler_failure_and_exception_are_reported(bluetooth):
    def handler(command):
        if command["cmd"] == "forget":
            return {"ok": False, "error": "nvm"}
        raise RuntimeError("boom")

    setup, _calls, _logs = _start(bluetooth, handler=handler)
    _auth(bluetooth, setup)

    _frame(bluetooth, setup, {"id": 1, "cmd": "forget"})
    assert _result(bluetooth, setup) == {
        "id": 1, "ok": False, "error": "nvm"
    }

    _frame(bluetooth, setup, {"id": 2, "cmd": "save"})
    assert _result(bluetooth, setup) == {
        "id": 2, "ok": False, "error": "handler"
    }


def test_unknown_command_does_not_reach_the_handler(bluetooth):
    setup, calls, _logs = _start(bluetooth)
    _auth(bluetooth, setup)

    _frame(bluetooth, setup, {"id": 3, "cmd": "launch"})

    assert _result(bluetooth, setup) == {
        "id": 3, "ok": False, "error": "command"
    }
    assert calls == []


def test_handler_extras_are_dropped_from_the_result(bluetooth):
    def handler(command):
        return {
            "id": command.get("id"),
            "ok": True,
            "error": "",
            "password": "hunter2",
            "token": "part-secret",
        }

    setup, _calls, _logs = _start(bluetooth, handler=handler)
    _auth(bluetooth, setup)
    _frame(bluetooth, setup, {"id": 5, "cmd": "forget"})

    text = bluetooth.ble.gatts_read(setup.handles[3]).decode()
    assert "hunter2" not in text
    assert "part-secret" not in text
    assert json.loads(text) == {"id": 5, "ok": True, "error": ""}


def test_status_has_the_contract_keys_and_no_secrets(bluetooth):
    setup, _calls, _logs = _start(
        bluetooth,
        status_provider=lambda: {
            "state": "SETUP",
            "ip": "192.168.1.20",
            "ssid": "home",
            "pid": "esp32c3-ab12",
            "password": "hunter2",
            "token": "part-secret",
            "code": "1234",
        },
        code="1234",
    )
    setup.poll()

    text = bluetooth.ble.gatts_read(setup.handles[0]).decode()
    assert "hunter2" not in text
    assert "part-secret" not in text
    assert "1234" not in text
    data = json.loads(text)
    assert data == {
        "v": 1,
        "state": "SETUP",
        "name": "heare-panel-ab12",
        "ip": "192.168.1.20",
        "ssid": "home",
        "pid": "esp32c3-ab12",
        "error": "",
    }


def test_status_provider_failure_is_a_field_not_an_exception(bluetooth):
    def status_provider():
        raise RuntimeError("boom")

    setup, _calls, _logs = _start(
        bluetooth, status_provider=status_provider
    )
    setup.poll()

    assert _status(bluetooth, setup)["error"] == "status"


def test_status_json_is_capped_without_becoming_invalid(bluetooth):
    setup, _calls, _logs = _start(
        bluetooth,
        status_provider=lambda: {
            "state": "S" * 400,
            "ip": "1" * 400,
            "ssid": "A" * 400,
            "pid": "B" * 400,
            "error": "E" * 400,
        },
    )
    setup.poll()

    text = bluetooth.ble.gatts_read(setup.handles[0]).decode()
    assert len(text.encode()) <= ble_setup.MAX_JSON
    assert json.loads(text)["v"] == 1


def test_scan_uses_the_provider_and_increments_seq(bluetooth):
    found = [{"ssid": "home", "rssi": -40}, {"ssid": "cafe", "rssi": -70}]
    setup, _calls, _logs = _start(
        bluetooth, scan_provider=lambda: found
    )
    _auth(bluetooth, setup)

    _frame(bluetooth, setup, {"id": 1, "cmd": "scan"})
    first = _scan(bluetooth, setup)
    _frame(bluetooth, setup, {"id": 2, "cmd": "scan"})
    second = _scan(bluetooth, setup)

    assert first["seq"] + 1 == second["seq"]
    assert second["list"] == found
    assert _result(bluetooth, setup)["ok"] is True


def test_scan_accepts_a_dict_and_drops_bad_entries(bluetooth):
    setup, _calls, _logs = _start(
        bluetooth,
        scan_provider=lambda: {
            "list": [
                {"ssid": "home", "rssi": "-30"},
                {"ssid": "cafe", "rssi": "not-a-number"},
                "nope",
                {"rssi": -55},
            ]
        },
    )
    _auth(bluetooth, setup)
    _frame(bluetooth, setup, {"id": 1, "cmd": "scan"})

    assert _scan(bluetooth, setup)["list"] == [
        {"ssid": "home", "rssi": -30},
        {"ssid": "cafe", "rssi": 0},
        {"ssid": "", "rssi": -55},
    ]


def test_scan_json_is_capped_by_dropping_entries(bluetooth):
    found = [
        {"ssid": f"network-{index:03d}", "rssi": -index}
        for index in range(100)
    ]
    setup, _calls, _logs = _start(
        bluetooth, scan_provider=lambda: found
    )
    _auth(bluetooth, setup)
    _frame(bluetooth, setup, {"id": 1, "cmd": "scan"})

    text = bluetooth.ble.gatts_read(setup.handles[1]).decode()
    assert len(text.encode()) <= ble_setup.MAX_JSON
    data = json.loads(text)
    assert data["seq"] == 1
    assert 0 < len(data["list"]) < 100


def test_scan_provider_failure_is_reported(bluetooth):
    def scan_provider():
        raise RuntimeError("boom")

    setup, _calls, _logs = _start(
        bluetooth, scan_provider=scan_provider
    )
    _auth(bluetooth, setup)
    _frame(bluetooth, setup, {"id": 4, "cmd": "scan"})

    assert _result(bluetooth, setup) == {
        "id": 4, "ok": False, "error": "scan"
    }


def test_out_of_order_duplicate_total_mismatch_and_oversize(bluetooth):
    setup, _calls, _logs = _start(bluetooth)
    _connect(bluetooth, setup)

    bluetooth.write(bytes((1, 2)) + b"{}")
    setup.poll()
    assert _result(bluetooth, setup)["error"] == "chunk"

    bluetooth.write(bytes((0, 3)) + b"a" * 18)
    setup.poll()
    bluetooth.write(bytes((1, 3)) + b"b" * 18)
    setup.poll()
    bluetooth.write(bytes((1, 3)) + b"c" * 18)
    setup.poll()
    assert _result(bluetooth, setup)["error"] == "chunk"

    bluetooth.write(bytes((0, 2)) + b"a" * 18)
    setup.poll()
    bluetooth.write(bytes((1, 3)) + b"b" * 18)
    setup.poll()
    assert _result(bluetooth, setup)["error"] == "chunk"

    for seq in range(29):
        bluetooth.write(bytes((seq, 30)) + b"x" * 18)
        setup.poll()
    assert _result(bluetooth, setup)["error"] == "chunk"

    bluetooth.write(bytes((0, 1)) + b"x" * 19)
    setup.poll()
    assert _result(bluetooth, setup)["error"] == "chunk"


def test_a_reset_on_seq_zero_recovers_from_a_lost_first_chunk(bluetooth):
    setup, _calls, _logs = _start(bluetooth)
    _connect(bluetooth, setup)

    bluetooth.write(bytes((0, 2)) + b"garbage")
    setup.poll()
    _auth(bluetooth, setup)

    assert _result(bluetooth, setup)["ok"] is True
    assert setup.running is True


def test_bad_json_gets_a_json_error(bluetooth):
    setup, _calls, _logs = _start(bluetooth)
    _connect(bluetooth, setup)
    _auth(bluetooth, setup)
    bluetooth.write(bytes((0, 1)) + b"not json")
    setup.poll()

    assert _result(bluetooth, setup)["error"] == "json"


def test_window_timeout_stops_the_radio(fake_clock, bluetooth):
    setup, _calls, logs = _start(bluetooth, window_ms=1000)

    fake_clock.advance(999)
    assert setup.poll() is True
    fake_clock.advance(1)
    assert setup.poll() is False

    assert bluetooth.ble.active_calls[-1] is False
    assert bluetooth.ble.advertising is False
    assert "ble timeout" in logs


def test_poll_before_start_does_nothing(bluetooth):
    setup = ble_setup.BleSetup("heare-panel-ab12", lambda command: {}, "1234")

    assert setup.poll() is False


def test_one_connection_stops_advertising_until_disconnect(bluetooth):
    setup, _calls, _logs = _start(bluetooth, one_connection=True)

    bluetooth.connect()
    setup.poll()
    assert setup.connected is True
    assert bluetooth.ble.advertising is False

    bluetooth.disconnect()
    setup.poll()
    assert setup.connected is False
    assert bluetooth.ble.advertising is True


def test_more_than_one_connection_keeps_advertising(bluetooth):
    setup, _calls, _logs = _start(bluetooth, one_connection=False)

    bluetooth.connect()
    setup.poll()

    assert setup.connected is True
    assert bluetooth.ble.advertising is True


def test_stop_and_restart(bluetooth):
    setup, _calls, _logs = _start(bluetooth)
    _connect(bluetooth, setup)

    assert setup.stop() is True
    assert bluetooth.ble.active_calls[-1] is False
    assert bluetooth.ble.advertising is False
    assert bluetooth.ble.disconnections == [1]

    assert setup.stop() is False
    assert setup.start() is True
    assert bluetooth.ble.registrations == 2
    assert bluetooth.ble.active_calls[-1] is True


def test_a_non_four_digit_code_is_refused(bluetooth):
    with pytest.raises(ValueError):
        ble_setup.BleSetup("heare-panel-ab12", lambda command: {}, "12a4")


def test_make_code_is_four_digits_in_range():
    for _ in range(50):
        code = ble_setup.make_code()
        assert len(code) == 4
        assert code.isdigit()
        assert 1000 <= int(code) <= 9999


def test_no_command_payload_reaches_the_logs(bluetooth):
    setup, _calls, logs = _start(bluetooth)
    _auth(bluetooth, setup)

    _frame(
        bluetooth, setup,
        {"id": 1, "cmd": "save", "ssid": "home", "password": "hunter2"},
    )

    assert "hunter2" not in "\n".join(logs)
    assert "1234" not in "\n".join(logs[2:])


def test_clock_is_used_for_the_window(bluetooth, fake_clock):
    setup, _calls, _logs = _start(bluetooth, window_ms=5000)
    assert setup._start_ms == fake_clock.now_ms

    fake_clock.advance(4999)
    assert setup.poll() is True
    fake_clock.advance(1)
    assert setup.poll() is False
