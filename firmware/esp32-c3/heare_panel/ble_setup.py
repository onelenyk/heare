"""Setup over BLE: a tiny GATT server for the Web Bluetooth page.

The board has no ``aioble``, so this module speaks to ``bluetooth`` directly:
one service with four characteristics (status, scan, cmd, result). The page
writes a JSON command as numbered chunks and reads the answer back; nothing
here starts an access point, joins Wi-Fi, writes NVS, or knows about the
brain. Those are the integrator's job through ``handler`` and the providers.

Two rules shape the code. The interrupt handler only copies bytes and records
which event happened; every scan, save, NVS write and JSON encode runs later
in ``poll()``. And the four-digit setup code is shown once, through ``out``,
but it is never part of the status, the result, or an error string.

The wire format is fixed by the page:

    service 8f2a0001-7d3b-4f6e-9a1c-2b5e7c9d0a11
    status  8f2a0002-...  READ   {v, state, name, ip, ssid, pid, error}
    scan    8f2a0003-...  READ   {seq, list: [{ssid, rssi}]}
    cmd     8f2a0004-...  WRITE  [seq, total] + UTF-8 JSON, <= 18 data bytes
    result  8f2a0005-...  READ   {id, ok, error}
"""

import json
import random

from heare_panel import clock

try:
    import bluetooth
except ImportError:
    bluetooth = None

SERVICE_UUID = "8f2a0001-7d3b-4f6e-9a1c-2b5e7c9d0a11"
STATUS_UUID = "8f2a0002-7d3b-4f6e-9a1c-2b5e7c9d0a11"
SCAN_UUID = "8f2a0003-7d3b-4f6e-9a1c-2b5e7c9d0a11"
CMD_UUID = "8f2a0004-7d3b-4f6e-9a1c-2b5e7c9d0a11"
RESULT_UUID = "8f2a0005-7d3b-4f6e-9a1c-2b5e7c9d0a11"

# The canonical UUID with the dashes removed; advertising wants it reversed.
SERVICE_BYTES = (
    b"\x8f\x2a\x00\x01\x7d\x3b\x4f\x6e\x9a\x1c\x2b\x5e\x7c\x9d\x0a\x11"
)
SERVICE_BYTES_LE = (
    b"\x11\x0a\x9d\x7c\x5e\x2b\x1c\x9a\x6e\x4f\x3b\x7d\x01\x00\x2a\x8f"
)

ADVERTISE_US = 100_000
DEFAULT_WINDOW_MS = 600_000
MAX_CHUNK = 18
MAX_MESSAGE = 512
MAX_JSON = 500
MAX_AUTH_FAILURES = 3

# MicroPython does not put the IRQ constants on the module.
_IRQ_CENTRAL_CONNECT = 1
_IRQ_CENTRAL_DISCONNECT = 2
_IRQ_GATTS_WRITE = 3


def make_code():
    try:
        return str(random.randint(1000, 9999))
    except AttributeError:
        return str(1000 + (random.getrandbits(14) % 9000))


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")
        except (TypeError, ValueError):
            return ""
    return str(value)


def _dumps(value):
    return json.dumps(value)


def _fit_status(data):
    """Compact status JSON, dropping optional fields before losing validity.

    A BLE read is one value; the page parses it. Rather than truncate bytes
    and hand the page broken JSON, drop the least important fields and, if a
    provider still returned monstrous text, shorten ``state`` and ``name``.
    """
    text = _dumps(data)
    if len(text) <= MAX_JSON:
        return text
    for key in ("error", "ssid", "ip", "pid"):
        data.pop(key, None)
        text = _dumps(data)
        if len(text) <= MAX_JSON:
            return text
    data["state"] = _text(data.get("state", "setup"))[:16]
    data["name"] = _text(data.get("name", ""))[:24]
    text = _dumps(data)
    if len(text) <= MAX_JSON:
        return text
    return _dumps({"v": 1, "state": "setup", "name": data["name"]})


def _fit_scan(data):
    """Compact scan JSON by dropping list entries, never by cutting bytes."""
    text = _dumps(data)
    while len(text) > MAX_JSON and data["list"]:
        data["list"].pop()
        text = _dumps(data)
    if len(text) > MAX_JSON:
        data["list"] = []
        text = _dumps(data)
    return text


class BleSetup:
    def __init__(
        self,
        name,
        handler,
        code,
        window_ms=DEFAULT_WINDOW_MS,
        status_provider=None,
        scan_provider=None,
        out=print,
        one_connection=True,
    ):
        self.name = _text(name)
        self.handler = handler
        self.code = _text(code)
        if len(self.code) != 4 or not self.code.isdigit():
            raise ValueError("setup code must be four digits")
        self.window_ms = int(window_ms)
        self.status_provider = status_provider
        self.scan_provider = scan_provider
        self.out = out or print
        self.one_connection = bool(one_connection)

        self.ble = None
        self.handles = None
        self.running = False
        self.connected = False

        self._start_ms = 0
        self._events = []
        self._chunk = bytearray()
        self._chunk_total = 0
        self._chunk_next = 0
        self._authed = False
        self._failures = 0
        self._scan_seq = 0
        self._conn = None

    def start(self):
        if self.running:
            return False
        if bluetooth is None:
            raise RuntimeError("bluetooth is unavailable")
        if self.ble is None:
            self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.config(gap_name=self.name)
        # Advertising must be stopped before services are (re)registered.
        try:
            self.ble.gap_advertise(None)
        except (OSError, ValueError):
            pass

        service = (
            bluetooth.UUID(SERVICE_UUID),
            (
                (bluetooth.UUID(STATUS_UUID), bluetooth.FLAG_READ),
                (bluetooth.UUID(SCAN_UUID), bluetooth.FLAG_READ),
                (bluetooth.UUID(CMD_UUID), bluetooth.FLAG_WRITE),
                (bluetooth.UUID(RESULT_UUID), bluetooth.FLAG_READ),
            ),
        )
        self.handles = self.ble.gatts_register_services((service,))[0]
        self.ble.gatts_set_buffer(self.handles[2], MAX_MESSAGE, True)
        self.ble.irq(self._irq)

        self.running = True
        self.connected = False
        self._conn = None
        self._authed = False
        self._failures = 0
        self._scan_seq = 0
        self._events = []
        self._reset_chunk()
        self._start_ms = clock.now_ms()
        self._publish_status()
        self._advertise()
        self.out("ble up name=" + self.name)
        self.out("code=" + self.code)
        return True

    def stop(self):
        if not self.running:
            return False
        self.running = False
        if self.ble is not None:
            try:
                self.ble.gap_advertise(None)
            except (OSError, ValueError):
                pass
            if self._conn is not None:
                try:
                    self.ble.gap_disconnect(self._conn)
                except (OSError, ValueError):
                    pass
            try:
                self.ble.active(False)
            except (OSError, ValueError):
                pass
        self._conn = None
        self.connected = False
        self._authed = False
        self._events = []
        self._reset_chunk()
        self.out("ble stop")
        return True

    def poll(self):
        if not self.running:
            return False
        if clock.elapsed(self._start_ms, clock.now_ms()) >= self.window_ms:
            self.out("ble timeout")
            self.stop()
            return False

        self._publish_status()

        while self._events:
            event = self._events.pop(0)
            kind = event[0]
            if kind == "connect":
                self._on_connect(event[1])
            elif kind == "disconnect":
                self._on_disconnect(event[1])
            elif kind == "write" and self._conn is not None:
                self._ingest(event[1])
            if not self.running:
                return False
        return True

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            self._events.append(("connect", data[0]))
        elif event == _IRQ_CENTRAL_DISCONNECT:
            self._events.append(("disconnect", data[0]))
        elif event == _IRQ_GATTS_WRITE:
            _conn_handle, value_handle = data
            if value_handle == self.handles[2]:
                try:
                    chunk = self.ble.gatts_read(value_handle)
                except (OSError, ValueError):
                    chunk = b""
                self._events.append(("write", bytes(chunk)))

    def _on_connect(self, conn_handle):
        self._conn = conn_handle
        self.connected = True
        self._authed = False
        self._reset_chunk()
        if self.one_connection:
            try:
                self.ble.gap_advertise(None)
            except (OSError, ValueError):
                pass
        self.out("ble connect")

    def _on_disconnect(self, conn_handle):
        if self._conn != conn_handle:
            return
        self._conn = None
        self.connected = False
        self._authed = False
        self._events = [event for event in self._events if event[0] != "write"]
        self._reset_chunk()
        self.out("ble disconnect")
        if self.running:
            self._advertise()

    def _advertise(self):
        flags = b"\x02\x01\x06"
        uuid = bytes((17, 0x07)) + SERVICE_BYTES_LE
        name = self.name.encode()[:20]
        response = bytes((len(name) + 1, 0x09)) + name
        self.ble.gap_advertise(
            ADVERTISE_US, adv_data=flags + uuid, resp_data=response
        )

    def _reset_chunk(self):
        self._chunk = bytearray()
        self._chunk_total = 0
        self._chunk_next = 0

    def _chunk_error(self):
        self._reset_chunk()
        self._publish_result({"id": None, "ok": False, "error": "chunk"})

    def _ingest(self, data):
        if len(data) < 2:
            return self._chunk_error()
        seq = data[0]
        total = data[1]
        body = bytes(data[2:])
        if len(body) > MAX_CHUNK or total < 1 or seq >= total:
            return self._chunk_error()

        if seq == 0:
            self._chunk = bytearray()
            self._chunk_total = total
            self._chunk_next = 0
        elif (
            self._chunk_total == 0
            or total != self._chunk_total
            or seq != self._chunk_next
        ):
            return self._chunk_error()

        if len(self._chunk) + len(body) > MAX_MESSAGE:
            return self._chunk_error()

        self._chunk.extend(body)
        self._chunk_next = seq + 1
        if self._chunk_next < self._chunk_total:
            return
        if self._chunk_next > self._chunk_total:
            return self._chunk_error()

        message = bytes(self._chunk)
        self._reset_chunk()
        self._command(message)

    def _command(self, message):
        try:
            command = json.loads(message.decode("utf-8"))
        except ValueError:
            return self._publish_result(
                {"id": None, "ok": False, "error": "json"}
            )
        if not isinstance(command, dict):
            return self._publish_result(
                {"id": None, "ok": False, "error": "json"}
            )

        identifier = command.get("id")
        if not isinstance(identifier, (int, str)):
            identifier = None

        if not self._authed:
            if command.get("cmd") != "auth":
                return self._publish_result(
                    {"id": identifier, "ok": False, "error": "auth"}
                )
            if _text(command.get("code")) == self.code:
                self._authed = True
                self._failures = 0
                return self._publish_result(
                    {"id": identifier, "ok": True, "error": ""}
                )
            self._failures += 1
            self._publish_result(
                {"id": identifier, "ok": False, "error": "auth"}
            )
            if self._failures >= MAX_AUTH_FAILURES:
                self.out("ble auth_failed")
                self.stop()
            return

        result = self._dispatch(command, identifier)
        self._publish_result(result)
        if result["ok"] and command.get("cmd") == "save":
            self.stop()

    def _dispatch(self, command, identifier):
        cmd = command.get("cmd")
        if cmd == "status":
            if self.status_provider is not None:
                try:
                    self._publish_status()
                except Exception:  # noqa: BLE001 - a provider must not kill BLE
                    return {"id": identifier, "ok": False, "error": "status"}
            return {"id": identifier, "ok": True, "error": ""}

        if cmd == "scan":
            if self.scan_provider is not None:
                try:
                    found = self.scan_provider()
                except Exception:  # noqa: BLE001 - a provider must not kill BLE
                    return {"id": identifier, "ok": False, "error": "scan"}
                self._publish_scan(found)
                return {"id": identifier, "ok": True, "error": ""}
            return self._call_handler(command, identifier)

        if cmd in ("save", "forget"):
            return self._call_handler(command, identifier)

        return {"id": identifier, "ok": False, "error": "command"}

    def _call_handler(self, command, identifier):
        try:
            answer = self.handler(command)
        except Exception:  # noqa: BLE001 - an integrator callback is untrusted
            return {"id": identifier, "ok": False, "error": "handler"}
        return self._result(answer, identifier)

    def _result(self, answer, identifier):
        if not isinstance(answer, dict):
            answer = {}
        error = answer.get("error", "")
        if not isinstance(error, str):
            error = _text(error)
        return {
            "id": identifier,
            "ok": bool(answer.get("ok")),
            "error": error[:64],
        }

    def _publish_result(self, result):
        if self.handles is None:
            return
        text = _dumps(result)
        if len(text) > MAX_JSON:
            text = _dumps({"id": result.get("id"), "ok": False, "error": "big"})
        self.ble.gatts_write(self.handles[3], text.encode())

    def _publish_status(self):
        if self.handles is None:
            return
        raw = {}
        error = ""
        if self.status_provider is not None:
            try:
                raw = self.status_provider()
            except Exception:  # noqa: BLE001 - a provider must not kill BLE
                error = "status"
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        data = {
            "v": 1,
            "state": _text(raw.get("state", "setup")),
            "name": _text(raw.get("name", self.name)),
            "ip": _text(raw.get("ip", "")),
            "ssid": _text(raw.get("ssid", "")),
            "pid": _text(raw.get("pid", "")),
            "error": _text(raw.get("error", error)),
        }
        self.ble.gatts_write(self.handles[0], _fit_status(data).encode())

    def _scan_list(self, found):
        if isinstance(found, dict):
            found = found.get("list", [])
        if not isinstance(found, (list, tuple)):
            return []
        entries = []
        for item in found[:64]:
            if not isinstance(item, dict):
                continue
            try:
                rssi = int(item.get("rssi", 0))
            except (TypeError, ValueError):
                rssi = 0
            entries.append(
                {"ssid": _text(item.get("ssid", ""))[:32], "rssi": rssi}
            )
        return entries

    def _publish_scan(self, found):
        if self.handles is None:
            return
        self._scan_seq += 1
        data = {"seq": self._scan_seq, "list": self._scan_list(found)}
        self.ble.gatts_write(self.handles[1], _fit_scan(data).encode())
