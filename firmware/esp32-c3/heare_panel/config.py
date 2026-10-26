"""The panel's configuration: how it joins a network and finds its brain.

Pure and small enough for the board: one import (``json``), plain dicts,
no dataclasses. Every value that arrives from the captive-portal form is
untrusted text, so it passes through ``validate`` before it is stored or
used. The Wi-Fi password and the one-time pair code are secrets: they never
appear in an error message, in ``public_view``, or in ``repr``.

The stored shape is fixed and deterministic::

    {"version": 1,
     "wifi": {"ssid": str, "password": str},
     "brain": {"host": "192.168.1.10", "port": 9790},
     "device": {"name": "kitchen"},
     "pair": {"code": "ABCD2345"}}   # optional

An older firmware can read a newer file: unknown keys are dropped on load.
"""

import json

VERSION = 1
DEFAULT_PORT = 9790
PAIR_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
PAIR_LENGTH = 8
NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"
HEX_CHARS = "0123456789abcdef"
SECRET = "***"


class ConfigError(Exception):
    """A configuration the panel cannot use. The message never holds a secret."""


class Config(dict):
    """The validated configuration: a dict whose text form hides secrets."""

    def __repr__(self):
        return "Config(" + json.dumps(_safe(self)) + ")"


def _safe(config):
    """A copy with the password and pair code replaced, for repr/logging."""
    wifi = config.get("wifi") or {}
    brain = config.get("brain") or {}
    device = config.get("device") or {}
    safe = {
        "version": config.get("version", VERSION),
        "wifi": {
            "ssid": wifi.get("ssid", ""),
            "password": SECRET if wifi.get("password") else "",
        },
        "brain": {
            "host": brain.get("host", ""),
            "port": brain.get("port", DEFAULT_PORT),
        },
        "device": {"name": device.get("name", "")},
    }
    pair = config.get("pair") or {}
    if pair:
        safe["pair"] = {"code": SECRET if pair.get("code") else ""}
    return safe


def _plain(config):
    """A plain dict in the canonical key order that ``dumps`` writes."""
    plain = {
        "version": config["version"],
        "wifi": {
            "ssid": config["wifi"]["ssid"],
            "password": config["wifi"]["password"],
        },
        "brain": {
            "host": config["brain"]["host"],
            "port": config["brain"]["port"],
        },
        "device": {"name": config["device"]["name"]},
    }
    if "pair" in config:
        plain["pair"] = {"code": config["pair"]["code"]}
    return plain


def _ipv4(value):
    """A strict dotted quad: four decimal octets, no signs, no leading zeros."""
    if not isinstance(value, str):
        return False
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not 1 <= len(part) <= 3:
            return False
        for ch in part:
            if ch not in "0123456789":
                return False
        if len(part) > 1 and part[0] == "0":
            return False
        if int(part) > 255:
            return False
    return True


def _port(value):
    """An int 1-65535, a port as the form sends it, or None when unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 65535 else None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return DEFAULT_PORT
        for ch in text:
            if ch not in "0123456789":
                return None
        number = int(text)
        return number if 1 <= number <= 65535 else None
    return None


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 24:
        return False
    for ch in value:
        if ch not in NAME_CHARS:
            return False
    return True


def _code(value):
    if not isinstance(value, str) or len(value) != PAIR_LENGTH:
        return False
    for ch in value:
        if ch not in PAIR_ALPHABET:
            return False
    return True


def validate(config):
    """The config as a ``Config``, or ``ConfigError`` naming the bad field.

    Required sections are ``wifi``, ``brain`` and ``device``; ``pair`` is
    optional. A missing port becomes ``DEFAULT_PORT``, and a missing
    password becomes the empty string (an open network).
    """
    if not isinstance(config, dict):
        raise ConfigError("config must be a mapping")

    version = config.get("version", VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError("config version must be 1")
    if version != VERSION:
        raise ConfigError("config version must be 1")

    wifi = config.get("wifi")
    if not isinstance(wifi, dict):
        raise ConfigError("wifi section is required")
    ssid = wifi.get("ssid")
    if not isinstance(ssid, str) or not 1 <= len(ssid) <= 32:
        raise ConfigError("wifi ssid must be 1-32 characters")
    password = wifi.get("password", "")
    if not isinstance(password, str):
        raise ConfigError("wifi password must be 8-63 characters or empty")
    if password and not 8 <= len(password) <= 63:
        raise ConfigError("wifi password must be 8-63 characters or empty")

    brain = config.get("brain")
    if not isinstance(brain, dict):
        raise ConfigError("brain section is required")
    host = brain.get("host")
    if not _ipv4(host):
        raise ConfigError("brain host must be an IPv4 address")
    port = _port(brain.get("port", DEFAULT_PORT))
    if port is None:
        raise ConfigError("brain port must be 1-65535")

    device = config.get("device")
    if not isinstance(device, dict):
        raise ConfigError("device section is required")
    name = device.get("name")
    if not _name(name):
        raise ConfigError("device name must match [a-z0-9-]{1,24}")

    clean = {
        "version": VERSION,
        "wifi": {"ssid": ssid, "password": password},
        "brain": {"host": host, "port": port},
        "device": {"name": name},
    }

    pair = config.get("pair")
    if pair:
        if not isinstance(pair, dict):
            raise ConfigError("pair must be an object")
        code = pair.get("code", "")
        if isinstance(code, str):
            code = code.strip().upper()
        if not _code(code):
            raise ConfigError(
                "pair code must be 8 characters from the pair alphabet"
            )
        clean["pair"] = {"code": code}

    return Config(clean)


def loads(text):
    """Parse and validate stored JSON. Unknown keys are dropped."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise ConfigError("config is not valid JSON")
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    return validate(data)


def dumps(config):
    """Write the canonical JSON: ``version`` first, then wifi/brain/device/pair.

    The order is the order ``_plain`` builds, not a sort, so it is the same
    on MicroPython and CPython and needs no ``sort_keys`` argument.
    """
    return json.dumps(_plain(validate(config)))


def merge(base, form):
    """Overlay the captive-portal form on a config and validate the result.

    The form speaks flat names: ``ssid``, ``password``, ``brain_host``,
    ``brain_port``, ``name`` and ``pair_code``. Unknown form keys are
    ignored. An empty ``pair_code`` removes the pair section.
    """
    if not isinstance(base, dict):
        base = {}
    if not isinstance(form, dict):
        raise ConfigError("form must be a mapping")

    wifi = dict(base.get("wifi") or {})
    brain = dict(base.get("brain") or {})
    device = dict(base.get("device") or {})
    pair = dict(base.get("pair") or {})

    if form.get("ssid") is not None:
        wifi["ssid"] = form["ssid"]
    if form.get("password") is not None:
        wifi["password"] = form["password"]
    if form.get("brain_host") is not None:
        brain["host"] = form["brain_host"]
    if form.get("brain_port") is not None:
        brain["port"] = form["brain_port"]
    if form.get("name") is not None:
        device["name"] = form["name"]
    if "pair_code" in form:
        code = form["pair_code"]
        pair = {} if code is None or code == "" else {"code": code}

    raw = {
        "version": base.get("version", VERSION),
        "wifi": wifi,
        "brain": brain,
        "device": device,
    }
    if pair:
        raw["pair"] = pair
    return validate(raw)


def public_view(config):
    """What the portal may show: no Wi-Fi password and no pair code."""
    clean = validate(config)
    return {
        "version": clean["version"],
        "wifi": {"ssid": clean["wifi"]["ssid"]},
        "brain": {
            "host": clean["brain"]["host"],
            "port": clean["brain"]["port"],
        },
        "device": {"name": clean["device"]["name"]},
    }


def identity(mac_hex):
    """Stable names for this board, from its MAC or short board tag.

    A full MAC (12 hex digits, with or without colons) is named by its last
    two bytes, the part that differs between boards. An 8-digit board tag
    (as ``machine.unique_id`` is often printed) is named by its first four,
    and a bare 4-digit tag is used as given; both match the plan's example
    ``identity("a1b2c3d4")`` -> ``a1b2``.

    Returns ``{"channel", "pid", "host"}``; none contains a user-entered
    name, so the identity is stable across reflashes and provisioning.
    """
    if not isinstance(mac_hex, str):
        raise ConfigError("mac must contain at least 4 hex digits")
    compact = mac_hex.lower().replace(":", "")
    if len(compact) < 4:
        raise ConfigError("mac must contain at least 4 hex digits")
    for ch in compact:
        if ch not in HEX_CHARS:
            raise ConfigError("mac must contain at least 4 hex digits")
    if len(compact) <= 8:
        mac4 = compact[:4]
    else:
        mac4 = compact[-4:]
    channel = "oled-" + mac4
    return {
        "channel": channel,
        "pid": channel + "@esp32c3-" + mac4,
        "host": "esp32c3-" + mac4,
    }
