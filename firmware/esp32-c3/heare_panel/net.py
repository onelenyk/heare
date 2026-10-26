"""The panel's radio: join a network, scan, and host the setup AP.

``network`` is optional and the radio is injected, so the same adapter runs
against ``FakeWLAN`` on a laptop and the real WLAN on the board. The connect
loop polls instead of blocking, which keeps the 15-second default inside the
board's watchdog budget.

The setup AP is never open: it always carries a WPA2 password of 8-12
characters, a fixed channel and two client slots, because it exists only long
enough for one phone to open the portal.
"""

try:
    import network
except ImportError:
    network = None

try:
    import time
except ImportError:
    time = None

DEFAULT_TIMEOUT_MS = 15_000
POLL_MS = 100
# Cheap ESP32-C3 "Super Mini" boards (this one included) have a poorly
# matched PCB antenna: at the default/max TX power the radio can fail to
# see or join networks at all. A lower power fixes it; agentlight's
# firmware carries the same workaround for the same hardware.
TX_POWER_DBM = 8.5
# The panel answers as heare-panel.local (MicroPython's built-in mDNS uses
# this name); it must be set before the station interface comes up.
HOSTNAME = "heare-panel"
AP_PREFIX = "heare-panel-"
AP_CHANNEL = 6
AP_MAX_CLIENTS = 2
AP_PASSWORD_MIN = 8
AP_PASSWORD_MAX = 12
HEX = "0123456789abcdef"


class WifiError(Exception):
    pass


def _constant(name, fallback):
    if network is None:
        return fallback
    return getattr(network, name, fallback)


def _interface(name, fallback):
    if network is None:
        raise WifiError("network is unavailable")
    return network.WLAN(_constant(name, fallback))


def _active(radio, value):
    method = getattr(radio, "active", None)
    if method is not None:
        method(value)


def _hostname(name):
    setter = getattr(network, "hostname", None) if network is not None else None
    if setter is None:
        return
    try:
        setter(name)
    except (OSError, ValueError):
        pass


def _gave_up():
    """Statuses after which the driver has stopped trying to join."""
    return (
        _constant("STAT_NO_AP_FOUND", 201),
        _constant("STAT_CONNECT_FAIL", 203),
    )


def _sleep_ms(ms):
    if time is None:
        return
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(ms)
    else:
        time.sleep(ms / 1000)


def _decode(value):
    if isinstance(value, str):
        return value
    try:
        return bytes(value).decode("utf-8", "replace")
    except (TypeError, ValueError):
        return ""


def _mac_hex(radio):
    mac = None
    if network is not None:
        try:
            mac = network.WLAN(_constant("STA_IF", 0)).config("mac")
        except (AttributeError, OSError, ValueError):
            mac = None
    if not mac:
        try:
            mac = radio.config("mac")
        except (AttributeError, OSError, ValueError):
            return "0000"
    if not mac:
        return "0000"
    return "".join(f"{byte:02x}" for byte in mac)


def _tag(tag, radio):
    if tag is None:
        tag = _mac_hex(radio)
    compact = str(tag).lower().replace(":", "")
    if len(compact) < 4:
        raise ValueError("AP tag must contain at least 4 hex digits")
    for char in compact:
        if char not in HEX:
            raise ValueError("AP tag must contain at least 4 hex digits")
    return compact[:4] if len(compact) <= 8 else compact[-4:]


class Sta:
    def __init__(self, radio=None, sleep=None):
        self.radio = radio if radio is not None else _interface("STA_IF", 0)
        self._sleep = sleep or _sleep_ms
        _hostname(HOSTNAME)
        _active(self.radio, True)
        # Scan needs the lowered TX power as much as connect does; setting
        # it only inside connect() left scan() running at full (bad) power.
        self._configure()

    def connect(self, ssid, password, timeout_ms=DEFAULT_TIMEOUT_MS):
        """Join ``ssid`` and return its IP, or raise ``WifiError``.

        ``isconnected`` is polled once per ``POLL_MS``; the loop is bounded by
        ``timeout_ms // POLL_MS`` attempts, so a wrong password costs the
        timeout instead of hanging the panel.
        """
        self._configure()
        self._join(ssid, password)

        status = None
        attempts = max(1, int(timeout_ms) // POLL_MS)
        for attempt in range(attempts):
            if self.radio.isconnected():
                return self.ip()
            status = self.status()
            if attempt + 1 < attempts:
                if status in _gave_up():
                    # At a weak signal one association scan can miss the AP
                    # (NO_AP_FOUND) although the next one sees it; the driver
                    # does not retry by itself, so start over within budget.
                    self._rejoin(ssid, password)
                self._sleep(POLL_MS)
        raise WifiError(
            f"wifi did not connect within {timeout_ms} ms (status {status})"
        )

    def _join(self, ssid, password):
        if password:
            self.radio.connect(ssid, password)
        else:
            self.radio.connect(ssid)

    def _rejoin(self, ssid, password):
        try:
            self.radio.disconnect()
        except (AttributeError, OSError):
            pass
        self._join(ssid, password)

    def configure(self):
        self._configure()

    def _configure(self):
        try:
            self.radio.config(pm=_constant("PM_NONE", 0))
        except (AttributeError, OSError, ValueError):
            pass
        try:
            self.radio.config(reconnects=-1)
        except (AttributeError, OSError, ValueError):
            pass
        try:
            self.radio.config(txpower=TX_POWER_DBM)
        except (AttributeError, OSError, ValueError):
            pass

    def status(self):
        return self.radio.status()

    def isconnected(self):
        return self.radio.isconnected()

    def ip(self):
        return self.radio.ifconfig()[0]

    def scan(self):
        """Visible networks as ``{ssid, rssi, auth, hidden}`` dicts.

        SSIDs arrive as bytes and may contain invalid UTF-8 or be empty (a
        hidden network); both are decoded with replacement and kept, because
        dropping one would hide a network the person may need to pick.
        """
        found = []
        for entry in self.radio.scan():
            ssid, _bssid, _channel, rssi, auth, hidden = entry
            found.append(
                {
                    "ssid": _decode(ssid),
                    "rssi": rssi,
                    "auth": auth,
                    "hidden": bool(hidden),
                }
            )
        return found

    def disconnect(self):
        self.radio.disconnect()


class Ap:
    def __init__(self, radio=None, password="", tag=None):
        if (
            not isinstance(password, str)
            or not AP_PASSWORD_MIN <= len(password) <= AP_PASSWORD_MAX
        ):
            raise ValueError("AP password must be 8-12 characters")
        self.radio = radio if radio is not None else _interface("AP_IF", 1)
        self.password = password
        self.ssid = AP_PREFIX + _tag(tag, self.radio)
        self.authmode = _constant("AUTH_WPA2_PSK", 3)
        self.started = False

    def start(self):
        _active(self.radio, True)
        self.radio.config(
            essid=self.ssid,
            password=self.password,
            authmode=self.authmode,
            channel=AP_CHANNEL,
            max_clients=AP_MAX_CLIENTS,
        )
        self.started = True
        return self.ssid

    def stop(self):
        _active(self.radio, False)
        self.started = False
