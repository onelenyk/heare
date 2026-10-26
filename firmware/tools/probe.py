"""Identify the board, its I2C bus, and an attached OLED.

Run it on the board, never on the host::

    uvx mpremote run firmware/tools/probe.py

Every line is one JSON object, so a laptop can parse the log without
guessing. The last line repeats the important values as ``key=value``
tokens because a person reading a serial log should not have to decode
JSON.

The probe never writes config, never creates a manifest, and never
flashes. It only reads identity, scans candidate pin pairs, and (when a
driver module is present) draws a full-pixel test so a photo can settle
the geometry. ``photo=required`` is deliberate: an SSD1306 128x64 and a
72x40 panel often accept the same init sequence, so the driver alone
cannot prove the size.
"""

import gc
import json
import machine
import os
import sys
import time

import esp


CANDIDATES = (
    (5, 6),
    (8, 9),
    (4, 5),
    (6, 7),
    (0, 1),
    (2, 3),
    (20, 21),
)

DISPLAY_ADDRESSES = (0x3C, 0x3D)


def emit(value):
    print(json.dumps(value))


def as_text(value):
    if isinstance(value, bytes):
        try:
            return value.decode()
        except Exception:
            return repr(value)
    return str(value)


def error_name(error):
    name = type(error).__name__
    detail = as_text(error)
    if detail:
        return "%s: %s" % (name, detail)
    return name


def call(getter):
    try:
        return getter()
    except Exception:
        return None


def call_attr(source, name):
    try:
        return getattr(source, name)()
    except Exception:
        return None


def hex_address(address):
    return "0x%02x" % address


def implementation():
    source = sys.implementation
    result = {}
    for name in ("name", "version", "_machine", "_mpy", "_build", "_thread"):
        if hasattr(source, name):
            value = getattr(source, name)
            result[name] = list(value) if isinstance(value, tuple) else value
    return result


def uname():
    value = call_attr(os, "uname")
    if value is None:
        return None
    return [as_text(part) for part in value]


def mac_hex():
    value = call_attr(machine, "unique_id")
    if value is None:
        return None
    try:
        return value.hex()
    except Exception:
        return None


def mac_colons(value):
    if value is None or len(value) != 12:
        return None
    return ":".join(value[index : index + 2] for index in range(0, 12, 2))


def board_name():
    value = call_attr(os, "uname")
    if value is not None and len(value) >= 5:
        return as_text(value[4])
    value = call(lambda: sys.implementation._machine)
    return as_text(value) if value is not None else "unknown"


def scan(sda, scl):
    result = {"sda": sda, "scl": scl, "addresses": [], "error": None}
    bus = None
    try:
        bus = machine.I2C(0, scl=machine.Pin(scl), sda=machine.Pin(sda), freq=400000)
        for address in bus.scan():
            result["addresses"].append(hex_address(address))
    except (OSError, ValueError) as error:
        result["error"] = error_name(error)
    finally:
        if bus is not None:
            try:
                bus.deinit()
            except Exception:
                pass
    return result


def ssd1306_module():
    try:
        import ssd1306
    except ImportError:
        return None
    return ssd1306


def sh1106_module():
    try:
        import sh1106
    except ImportError:
        return None
    return sh1106


def full_pixels(display):
    display.fill(1)
    display.show()
    time.sleep(1)
    display.fill(0)
    display.show()


def try_ssd1306(module, bus, sda, scl, address, width, height, col_offset=None):
    result = {
        "driver": "ssd1306",
        "width": width,
        "height": height,
        "sda": sda,
        "scl": scl,
        "addr": hex_address(address),
        "offset": None,
        "status": "failed",
        "error": None,
    }
    display = None
    try:
        if col_offset is None:
            display = module.SSD1306_I2C(
                width, height, bus, addr=address, external_vcc=False
            )
        else:
            try:
                display = module.SSD1306_I2C(
                    width,
                    height,
                    bus,
                    addr=address,
                    external_vcc=False,
                    col_offset=col_offset,
                )
                result["offset"] = col_offset
            except TypeError:
                display = module.SSD1306_I2C(
                    width, height, bus, addr=address, external_vcc=False
                )
                if hasattr(display, "col_offset"):
                    display.col_offset = col_offset
                    result["offset"] = col_offset
                else:
                    result["offset"] = "unsupported"
        full_pixels(display)
        result["status"] = "ok"
    except Exception as error:
        result["error"] = error_name(error)
    return result


def try_sh1106(module, bus, sda, scl, address):
    result = {
        "driver": "sh1106",
        "width": 128,
        "height": 64,
        "sda": sda,
        "scl": scl,
        "addr": hex_address(address),
        "offset": None,
        "status": "failed",
        "error": None,
    }
    try:
        display = module.SH1106_I2C(128, 64, bus, addr=address, external_vcc=False)
        full_pixels(display)
        result["status"] = "ok"
    except Exception as error:
        result["error"] = error_name(error)
    return result


def display_tokens(attempt):
    tokens = [
        "display=%s %dx%d sda=%d scl=%d addr=%s"
        % (
            attempt["driver"],
            attempt["width"],
            attempt["height"],
            attempt["sda"],
            attempt["scl"],
            attempt["addr"],
        )
    ]
    if attempt["offset"] not in (None, "unsupported"):
        tokens.append("offset=%s" % attempt["offset"])
    tokens.append("photo=required")
    return tokens


def main():
    machine_mac = mac_hex()
    board = board_name()

    emit({"probe": "start", "firmware": "micropython"})
    emit({"sys_implementation": implementation()})
    emit({"os_uname": uname()})
    emit({"board": board, "mac": machine_mac, "mac_colon": mac_colons(machine_mac)})
    emit({"heap": call_attr(gc, "mem_free"), "heap_alloc": call_attr(gc, "mem_alloc")})
    emit({"flash_size": call_attr(esp, "flash_size")})
    emit({"reset_cause": call_attr(machine, "reset_cause")})

    buses = []
    i2c_tokens = []
    for sda, scl in CANDIDATES:
        result = scan(sda, scl)
        emit({"i2c": result})
        if not result["addresses"]:
            continue
        buses.append((sda, scl, result["addresses"]))
        i2c_tokens.append("%s@%d,%d" % (",".join(result["addresses"]), sda, scl))

    ssd = ssd1306_module()
    sh = sh1106_module()
    for label, module in (("ssd1306", ssd), ("sh1106", sh)):
        if module is None:
            emit(
                {
                    "driver": label,
                    "status": "unavailable",
                    "line": "driver=unavailable module=%s" % label,
                }
            )

    attempts = []
    if ssd is not None or sh is not None:
        for sda, scl, addresses in buses:
            bus = None
            try:
                bus = machine.I2C(
                    0, scl=machine.Pin(scl), sda=machine.Pin(sda), freq=400000
                )
                for address in DISPLAY_ADDRESSES:
                    if hex_address(address) not in addresses:
                        continue
                    if ssd is not None:
                        for width, height, offset in (
                            (128, 64, None),
                            (72, 40, 28),
                        ):
                            attempt = try_ssd1306(
                                ssd, bus, sda, scl, address, width, height, offset
                            )
                            emit({"display": attempt})
                            attempts.append(attempt)
                    if sh is not None:
                        attempt = try_sh1106(sh, bus, sda, scl, address)
                        emit({"display": attempt})
                        attempts.append(attempt)
            except (OSError, ValueError) as error:
                emit(
                    {
                        "display_bus": {
                            "sda": sda,
                            "scl": scl,
                            "error": error_name(error),
                        }
                    }
                )
            finally:
                if bus is not None:
                    try:
                        bus.deinit()
                    except Exception:
                        pass

    working = [attempt for attempt in attempts if attempt["status"] == "ok"]
    tokens = [
        "board=%s" % board,
        "mac=%s" % (machine_mac or "unknown"),
        "heap=%s" % (call_attr(gc, "mem_free") or "unknown"),
        "i2c=%s" % (";".join(i2c_tokens) if i2c_tokens else "none"),
    ]
    if working:
        tokens.extend(display_tokens(working[0]))
    elif ssd is None and sh is None:
        tokens.append("display=unavailable")
    else:
        tokens.append("display=none")

    emit({"summary": " ".join(tokens), "display_candidates": len(working)})


main()
