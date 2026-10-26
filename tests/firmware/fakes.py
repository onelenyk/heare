"""Host-side stand-ins for the ESP32-C3 panel's hardware.

The firmware runs on MicroPython; these fakes let the pure modules be
exercised on CPython in CI, with no board and no third-party dependency.
They record what the code did rather than pretend to be a real device, so a
test can assert the exact bytes written to a display or a socket.
"""

from __future__ import annotations

from collections.abc import Iterable

# MicroPython's ``time.ticks_ms()`` counts in a 30-bit ring.
TICKS_PERIOD = 1 << 30


class FakeSocket:
    """An in-memory byte stream shaped like a MicroPython socket.

    ``sent`` holds everything the code wrote, ``incoming`` what it can read
    next. ``feed`` is how a test answers a request.
    """

    def __init__(self, incoming: bytes = b"") -> None:
        self.sent = bytearray()
        self.incoming = bytearray(incoming)
        self.timeouts: list[float | None] = []
        self.connections: list[tuple[str, int]] = []
        self.options: list[tuple[object, ...]] = []
        self.closed = False

    def feed(self, data: bytes) -> None:
        self.incoming.extend(data)

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def send(self, data: bytes) -> int:
        self.sendall(data)
        return len(data)

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        return chunk

    def recv_into(self, buffer: bytearray | memoryview, nbytes: int = 0) -> int:
        size = nbytes or len(buffer)
        data = self.recv(size)
        buffer[: len(data)] = data
        return len(data)

    def settimeout(self, seconds: float | None) -> None:
        self.timeouts.append(seconds)

    def setsockopt(self, *args: object) -> None:
        self.options.append(args)

    def connect(self, address: tuple[str, int]) -> None:
        self.connections.append(address)

    def close(self) -> None:
        self.closed = True


class FakeCanvas:
    """A 1-bit canvas that records every primitive instead of drawing."""

    def __init__(self, width: int = 128, height: int = 64) -> None:
        self.width = width
        self.height = height
        self.ops: list[tuple[object, ...]] = []

    def clear(self) -> None:
        self.ops.clear()

    def fill(self, color: int) -> None:
        self.ops.append(("fill", color))

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        self.ops.append(("fill_rect", x, y, w, h, color))

    def rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        self.ops.append(("rect", x, y, w, h, color))

    def text(self, string: str, x: int, y: int, color: int = 1) -> None:
        self.ops.append(("text", string, x, y, color))

    def pixel(self, x: int, y: int, color: int = 1) -> None:
        self.ops.append(("pixel", x, y, color))

    def hline(self, x: int, y: int, w: int, color: int = 1) -> None:
        self.ops.append(("hline", x, y, w, color))

    def vline(self, x: int, y: int, h: int, color: int = 1) -> None:
        self.ops.append(("vline", x, y, h, color))


class FakeI2C:
    """A register file per address, also recording each transfer.

    A display driver can write its command/data stream through ``writeto``
    and read it back with ``readfrom``; Task 10 asserts on ``writes``.
    """

    def __init__(self, addresses: Iterable[int] = (0x3C,), size: int = 256) -> None:
        self.addresses = tuple(addresses)
        self.size = size
        self.registers: dict[tuple[int, int], int] = {}
        self.writes: list[tuple[int, bytes]] = []
        self.reads: list[tuple[int, int]] = []

    def scan(self) -> list[int]:
        return list(self.addresses)

    def writeto(self, address: int, data: bytes) -> None:
        self.writes.append((address, bytes(data)))
        if not data:
            return
        register = data[0]
        for offset, value in enumerate(data[1:]):
            self.registers[(address, (register + offset) % self.size)] = value

    def readfrom(self, address: int, nbytes: int) -> bytes:
        self.reads.append((address, nbytes))
        return bytes(self.registers.get((address, i), 0) for i in range(nbytes))

    def readfrom_into(
        self, address: int, buffer: bytearray | memoryview, start: int = 0
    ) -> None:
        data = self.readfrom(address, len(buffer) - start)
        buffer[start:] = data

    def writeto_mem(self, address: int, register: int, data: bytes) -> None:
        self.writeto(address, bytes([register]) + bytes(data))

    def readfrom_mem(self, address: int, register: int, nbytes: int) -> bytes:
        self.reads.append((address, nbytes))
        return bytes(
            self.registers.get((address, (register + i) % self.size), 0)
            for i in range(nbytes)
        )


class FakeWLAN:
    """A ``network.WLAN`` stand-in with a canned scan result."""

    def __init__(
        self,
        connected: bool = False,
        networks: Iterable[tuple[bytes, bytes, int, int, int, bool]] = (),
    ) -> None:
        self._connected = connected
        self._networks = list(networks)
        self._ifconfig = ("0.0.0.0", "255.255.255.0", "0.0.0.0", "0.0.0.0")
        self._config: dict[str, object] = {"ssid": "", "password": ""}
        self.calls: list[tuple[object, ...]] = []
        self._active = False

    def active(self, value: bool | None = None) -> bool:
        if value is None:
            return self._active
        self.calls.append(("active", value))
        self._active = bool(value)
        return self._active

    def isconnected(self) -> bool:
        return self._connected

    def connect(self, ssid: str | None = None, password: str | None = None) -> None:
        self.calls.append(("connect", ssid, password))
        self._config["ssid"] = ssid or self._config["ssid"]
        self._connected = True

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))
        self._connected = False

    def status(self) -> str:
        return "connected" if self._connected else "disconnected"

    def config(self, *args: object, **kwargs: object) -> object:
        if kwargs:
            self.calls.append(("config", dict(kwargs)))
            self._config.update(kwargs)
            return None
        if args:
            return self._config.get(str(args[0]))
        return dict(self._config)

    def scan(self) -> list[tuple[bytes, bytes, int, int, int, bool]]:
        return list(self._networks)

    def ifconfig(self) -> tuple[str, str, str, str]:
        return self._ifconfig


class FakeNVS:
    """A dict-backed ``esp32.NVS``; keys obey the 15-character limit."""

    def __init__(self, namespace: str = "heare") -> None:
        self.namespace = namespace
        self.blobs: dict[str, bytes] = {}
        self.commits = 0

    def set_blob(self, key: str, value: bytes) -> None:
        assert len(key) <= 15, f"NVS key too long: {key!r}"
        self.blobs[key] = bytes(value)

    def get_blob(self, key: str) -> bytes:
        if key not in self.blobs:
            raise OSError(-2, "ESP_ERR_NVS_NOT_FOUND")
        return self.blobs[key]

    def erase_key(self, key: str) -> None:
        self.blobs.pop(key, None)

    def commit(self) -> None:
        self.commits += 1

    def erase(self) -> None:
        self.blobs.clear()
        self.commits += 1


class FakeClock:
    """A wrap-safe ``time.ticks_ms`` source that only moves when told to."""

    def __init__(self, now_ms: int = 0) -> None:
        self._now_ms = now_ms % TICKS_PERIOD

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def advance(self, ms: int) -> int:
        self._now_ms = (self._now_ms + ms) % TICKS_PERIOD
        return self._now_ms

    def ticks_ms(self) -> int:
        return self._now_ms

    @staticmethod
    def ticks_diff(a: int, b: int) -> int:
        return (a - b + TICKS_PERIOD // 2) % TICKS_PERIOD - TICKS_PERIOD // 2
