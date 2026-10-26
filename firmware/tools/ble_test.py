"""Prove the BLE setup transport on the panel, without writing config.

Run it on the board, never on the host::

    uvx mpremote connect /dev/cu.usbmodem214201 mount firmware/esp32-c3 \
        run firmware/tools/ble_test.py

It starts the real GATT server, prints the advertised name and the OLED code,
checks the status/result/cmd characteristics locally, then advertises for a
short window so a laptop can connect and read/write. It touches no config and
does not reboot; ``stop()`` turns the radio off before the script returns.
"""

import json
import time

import machine
from heare_panel import ble_setup

DURATION_MS = 30_000
POLL_MS = 100


def _tag():
    unique = machine.unique_id()
    return "".join(f"{byte:02x}" for byte in unique[-2:])


def main():
    tag = _tag()
    name = "heare-panel-" + tag
    code = ble_setup.make_code()
    commands = []

    def handler(command):
        commands.append(command.get("cmd"))
        return {"id": command.get("id"), "ok": True, "error": ""}

    def scan():
        return [
            {"ssid": "self-check", "rssi": -40},
            {"ssid": "hidden", "rssi": 0},
        ]

    def status():
        return {
            "v": 1,
            "state": "setup",
            "name": name,
            "ip": "0.0.0.0",
            "ssid": "",
            "pid": "esp32c3-" + tag,
            "error": "",
        }

    setup = ble_setup.BleSetup(
        name,
        handler,
        code,
        status_provider=status,
        scan_provider=scan,
        out=print,
    )
    setup.start()

    ble = setup.ble
    handles = setup.handles
    print(
        f"ble active={ble.active()} handles={len(handles)}"
        f" service={ble_setup.SERVICE_UUID}"
    )
    print(f"ble window_ms={setup.window_ms} interval_us={ble_setup.ADVERTISE_US}")

    status_text = ble.gatts_read(handles[0]).decode()
    status_data = json.loads(status_text)
    print(
        f"gatt status bytes={len(status_text)} json=ok"
        f" keys={','.join(status_data.keys())}"
    )
    print(
        "gatt status has_code={} has_password={}".format(
            "code" in status_data, "password" in status_data
        )
    )

    ble.gatts_write(handles[3], b'{"id":0,"ok":true,"error":""}')
    result = json.loads(ble.gatts_read(handles[3]).decode())
    print(f"gatt result write_read ok={result.get('ok') is True}")

    chunk = bytes((0, 1)) + b'{"cmd":"status"}'
    ble.gatts_write(handles[2], chunk)
    echo = ble.gatts_read(handles[2])
    print(
        f"gatt cmd write_read bytes={len(echo)} match={echo == chunk}"
        f" chunk_max={ble_setup.MAX_CHUNK}"
    )

    started = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), started) < DURATION_MS:
        setup.poll()
        time.sleep_ms(POLL_MS)

    setup.stop()
    print(f"ble commands_seen={len(commands)}")
    print(f"ble done active={ble.active()}")


main()
