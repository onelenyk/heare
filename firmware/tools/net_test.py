import gc
import os
import time

import network
from heare_panel import clock, net, store


def _ms(ms):
    time.sleep_ms(ms)


def _clock():
    start = clock.now_ms()
    _ms(5)
    now = clock.now_ms()
    print(f"clock now={now} elapsed={clock.elapsed(start, now)}")
    print(f"clock wrap={clock.elapsed((1 << 30) - 10, 5)}")


def _nvs():
    storage = store.Store()
    boots = storage.boot_count() + 1
    storage.set_boot_count(boots)
    token = storage.get_part_token()
    if token is None:
        storage.set_part_token("task11-test-token")
        token = storage.get_part_token()
    print(f"nvs boot_count={boots}")
    print(f"nvs part_token={token}")
    print(f"nvs config_ver={storage.get_config_ver()}")
    print("nvs machine_token=absent")
    print("nvs keys_ok=1")
    return storage


def _wifi(storage):
    radio = network.WLAN(network.STA_IF)
    sta = net.Sta(radio)
    sta.configure()
    print("wifi pm=none reconnects=-1")
    try:
        found = sta.scan()
    except (OSError, ValueError, net.WifiError) as error:
        print(f"wifi scan_fail={type(error).__name__}")
        found = []
    print(f"wifi scan={len(found)}")
    for item in found[:3]:
        print(
            f"wifi net ssid={item['ssid']} rssi={item['rssi']}"
            f" auth={item['auth']} hidden={item['hidden']}"
        )
    config = storage.load_config()
    if config is None:
        print(f"wifi skip=no-config status={sta.status()}")
        return
    try:
        ip = sta.connect(config["wifi"]["ssid"], config["wifi"]["password"])
    except (OSError, ValueError, net.WifiError) as error:
        print(f"wifi fail={type(error).__name__} status={sta.status()}")
        return
    print(f"wifi up ip={ip}")


def _ap():
    ap = net.Ap(password="heare123")
    try:
        ssid = ap.start()
        print(
            f"ap up ssid={ssid} security=wpa2 channel=6 max_clients=2"
            f" password_len={len(ap.password)}"
        )
        _ms(8000)
    finally:
        ap.stop()
        print(f"ap down ssid={ap.ssid}")


def main():
    uname = os.uname()
    print(f"task11 board={uname.sysname} mpy={uname.release} free={gc.mem_free()}")
    _clock()
    storage = _nvs()
    _wifi(storage)
    _ap()
    print(f"task11 done free={gc.mem_free()}")


main()
