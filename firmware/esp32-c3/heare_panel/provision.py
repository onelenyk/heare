"""The provisioning transaction, independent of the transport that asks.

Bluetooth, the pairing page or a serial REPL all want the same sequence: merge the
form into the stored config, join Wi-Fi, and only then write the config; pair
with a one-time brain code and let ``link`` keep the part token. Keeping that
order in one place is what makes a wrong Wi-Fi password harmless: nothing is
written until the station has an address, so the panel still remembers the
network it was using.

Pairing is deliberately a second step. A refused or throttled code leaves the
Wi-Fi config in place and no part token behind, so the person can try another
code without typing the password again. The machine token is never touched
here; it lives for one knock inside ``link.pair``.

The transport supplies the store, the station, the link factory and the clock;
this module imports no radio or socket itself. Results and log lines name a
failure by code (``wifi``, ``config``, ``pair``) and never echo the password,
the pair code or a token.
"""

import os

from heare_panel import config as schema
from heare_panel import journal
from heare_panel import link as link_module
from heare_panel import net, portal
from heare_panel import store as storage

ERROR_COMMAND = "command"
ERROR_CONFIG = "config"
ERROR_PAIR = "pair"
ERROR_SCAN = "scan"
ERROR_WIFI = "wifi"

STATE_IDLE = "idle"
STATE_PAIR = "pair"
STATE_READY = "ready"

SCAN_LIMIT = portal.MAX_NETWORKS


class Provisioner:
    """One setup transaction at a time, over an injected store and station.

    ``link_factory(config, store)`` returns something with ``pair(code)`` and
    a ``pid`` after success; production passes ``Link`` and tests pass a
    recorder. ``clock`` is accepted so the caller can share the panel's clock
    with later steps; this transaction needs no time of its own.
    """

    def __init__(self, store, sta, link_factory, clock=None, out=journal.say) -> None:
        self.store = store
        self.sta = sta
        self.link_factory = link_factory
        self.clock = clock
        self.out = out
        self.ip = ""
        self.error = ""

    def handle(self, command: dict) -> dict:
        if not isinstance(command, dict):
            return {"ok": False, "error": ERROR_COMMAND}
        name = command.get("cmd")
        if name is None:
            name = command.get("command")
        if name == "save":
            return self._save(command)
        if name == "scan":
            return self._scan()
        if name == "forget":
            return self._forget()
        if name == "status":
            return self._status()
        return {"ok": False, "error": ERROR_COMMAND}

    def _save(self, command: dict) -> dict:
        form = _form(command)
        base = self._stored_config() or {}
        try:
            candidate = schema.merge(base, form)
        except schema.ConfigError:
            return self._fail(ERROR_CONFIG)

        wifi = candidate["wifi"]
        ip = None
        if "ssid" not in form and "password" not in form:
            # Pairing from the panel's own page: it is on that network now,
            # and rejoining would drop the very connection the page uses.
            ip = self._live_ip()
        try:
            if not ip:
                ip = self.sta.connect(wifi["ssid"], wifi["password"])
        except (net.WifiError, OSError, ValueError):
            return self._fail(ERROR_WIFI)
        if not ip:
            return self._fail(ERROR_WIFI)

        try:
            saved = self.store.save_config(candidate)
        except (OSError, ValueError, schema.ConfigError):
            return self._fail(ERROR_CONFIG)
        if not isinstance(saved, dict):
            saved = candidate
        self.ip = str(ip)
        if "brain" not in saved or "pair_code" not in form:
            # Bluetooth setup stops at Wi-Fi; the brain comes from the page
            # the panel serves once it is on the network. A code kept in the
            # config from an earlier pairing is spent: only a code sent with
            # this save is ever redeemed.
            self.error = ""
            self.out(f"save ok ip={self.ip}")
            return {"ok": True, "ip": self.ip, "pid": ""}

        code = ""
        if "pair" in candidate:
            code = candidate["pair"]["code"]
        had_token = self._token()
        try:
            link = self.link_factory(saved, self.store)
            link.pair(code)
        except (link_module.NeedsProvisioning, link_module.Throttled):
            return self._pair_failed(had_token)
        except (link_module.LinkError, OSError, ValueError, KeyError):
            return self._pair_failed(had_token)

        pid = getattr(link, "pid", None) or self._stored_pid() or ""
        self.error = ""
        self.out(f"save ok ip={self.ip}")
        return {"ok": True, "ip": self.ip, "pid": pid}

    def _scan(self) -> dict:
        try:
            found = self.sta.scan()
        except (OSError, ValueError):
            return self._fail(ERROR_SCAN)

        networks = []
        for item in found or []:
            if not isinstance(item, dict):
                continue
            ssid = item.get("ssid")
            if not isinstance(ssid, str):
                continue
            networks.append({"ssid": ssid, "rssi": item.get("rssi", 0)})
            if len(networks) >= SCAN_LIMIT:
                break
        return {"ok": True, "list": networks}

    def _forget(self) -> dict:
        _quietly(self.store.clear_part_token)
        _erase_key(self.store, storage.KEY_PID)
        _erase_key(self.store, storage.KEY_CONFIG_VER)
        _remove_config(self.store)
        self.ip = ""
        self.error = ""
        self.out("forget ok")
        return {"ok": True}

    def _status(self) -> dict:
        stored = self._stored_config() or {}
        wifi = stored.get("wifi") or {}
        device = stored.get("device") or {}
        return {
            "state": self._state(stored),
            "name": _text(device.get("name")),
            "ip": self._current_ip(),
            "ssid": _text(wifi.get("ssid")),
            "pid": self._stored_pid() or "",
            "error": self.error,
        }

    def _state(self, stored: dict) -> str:
        if self._token():
            return STATE_READY
        if stored:
            return STATE_PAIR
        return STATE_IDLE

    def _live_ip(self) -> str:
        """The station's address if the radio says it is connected now."""
        try:
            if self.sta.isconnected():
                return self.sta.ip() or ""
        except (AttributeError, OSError, ValueError):
            pass
        return ""

    def _current_ip(self) -> str:
        if self.ip:
            return self.ip
        ip = getattr(self.sta, "ip", None)
        if ip is None:
            return ""
        connected = getattr(self.sta, "isconnected", None)
        try:
            if connected is not None and not connected():
                return ""
            value = ip()
        except (AttributeError, OSError, ValueError):
            return ""
        return value if isinstance(value, str) else ""

    def _stored_config(self):
        try:
            return self.store.load_config()
        except (OSError, ValueError, schema.ConfigError):
            return None

    def _token(self):
        try:
            return self.store.get_part_token()
        except (OSError, AttributeError, ValueError):
            return None

    def _stored_pid(self):
        try:
            return self.store.get_pid()
        except (OSError, AttributeError, ValueError):
            return None

    def _pair_failed(self, had_token) -> dict:
        if not had_token:
            _quietly(self.store.clear_part_token)
        return self._fail(ERROR_PAIR)

    def _fail(self, code: str) -> dict:
        self.error = code
        self.out(f"save failed error={code}")
        return {"ok": False, "error": code}


def _form(command: dict) -> dict:
    """The flat form ``config.merge`` understands.

    ``ssid_manual`` is the page's free-text box and wins over the scan
    ``select``. An empty or absent SSID is not written, so a retry with only
    a new pair code keeps the Wi-Fi config already stored.
    """
    form = {}
    for key in ("password", "brain_host", "brain_port", "name", "pair_code"):
        if key in command:
            form[key] = command[key]
    ssid = command.get("ssid_manual") or command.get("ssid")
    if ssid:
        form["ssid"] = ssid
    return form


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _quietly(action) -> None:
    try:
        action()
    except (OSError, AttributeError, ValueError):
        return


def _erase_key(store, key) -> None:
    """Erase one NVS key through the store's own path, if it has one."""
    if key == storage.KEY_PID:
        clear = getattr(store, "clear_pid", None)
        if clear is not None:
            try:
                clear()
            except (AttributeError, OSError):
                pass
            return
    erase = getattr(store, "erase_key", None)
    if erase is not None:
        try:
            erase(key)
        except (AttributeError, OSError, TypeError):
            pass
        return
    erase = getattr(store, "_erase", None)
    if erase is not None:
        try:
            erase(key)
        except (AttributeError, OSError, TypeError):
            pass
        return
    nvs = getattr(store, "nvs", None)
    erase = getattr(nvs, "erase_key", None) if nvs is not None else None
    if erase is None:
        return
    try:
        erase(key)
    except OSError:
        return
    commit = getattr(nvs, "commit", None)
    if commit is not None:
        commit()


def _remove_config(store) -> None:
    for name in ("clear_config", "remove_config"):
        clear = getattr(store, name, None)
        if clear is not None:
            try:
                clear()
                return
            except (AttributeError, OSError, TypeError):
                break
    path = getattr(store, "path", None)
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass
