"""Put firmware/esp32-c3 on the panel as precompiled .mpy, keeping its settings.

Why not ``mpremote cp``: on this board the sources compiled on the device
grew MicroPython's heap into the memory Wi-Fi needs, and once ``main`` has
started the 30 s watchdog a long copy is cut off by a reset. So this script
never talks to the REPL. It reads the board's filesystem image over the ROM
bootloader (``esptool``), swaps the code inside it on this Mac, and writes it
back:

* every ``heare_panel``/``lib`` module is compiled with ``mpy-cross`` (the
  board runs MicroPython 1.29, mpy v6.3) and its ``.py`` is removed;
* modules that no longer exist in the checkout are removed from the board;
* ``main.py`` becomes a two-line launcher of ``panel_main.mpy``;
* everything else (``config.json``, ``display.json``, ``boot.py``) is kept,
  so a paired panel stays paired. The part key lives in NVS, untouched.

The image read from the board is saved first; ``--restore FILE`` writes it
back.

    make panel-deploy                      # PORT=/dev/cu.usbmodem214201
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from littlefs import LittleFS

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware" / "esp32-c3"
BACKUPS = Path(tempfile.gettempdir()) / "heare-panel-backups"
# MicroPython's generic ESP32-C3 build keeps its filesystem after the app:
# factory ends at 0x200000 and the flash is 4 MB.
VFS_OFFSET = 0x200000
VFS_SIZE = 0x200000
BLOCK = 4096
PACKAGES = ("heare_panel", "lib")
FRESH = "fresh_code"
LAUNCHER = b"import panel_main\npanel_main.run()\n"


def _esptool(port: str, *args: str, reset: bool = True) -> None:
    # Every reset into the app is a boot the panel counts; three that never
    # get healthy send it to Bluetooth setup. Reading stays in the bootloader
    # so a deploy costs the panel one boot, after the write.
    after = "hard-reset" if reset else "no-reset"
    subprocess.run(
        [sys.executable, "-m", "esptool", "--port", port, "--after", after, *args],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def _mount(image: bytes) -> LittleFS:
    fs = LittleFS(block_size=BLOCK, block_count=len(image) // BLOCK, mount=False)
    fs.context.buffer = bytearray(image)
    fs.mount()
    return fs


def _compile(source: Path, target: Path) -> bytes:
    subprocess.run(["mpy-cross", "-o", str(target), str(source)], check=True)
    return target.read_bytes()


def build(fs: LittleFS, work: Path) -> list[str]:
    """Swap the code inside ``fs``; return what changed, for the log."""
    changed = []
    for package in PACKAGES:
        sources = sorted((FIRMWARE / package).glob("*.py"))
        wanted = {source.stem for source in sources}
        if package not in fs.listdir("/"):
            fs.mkdir("/" + package)
        for name in fs.listdir("/" + package):
            stem, _, suffix = name.rpartition(".")
            if suffix == "py" or (suffix == "mpy" and stem not in wanted):
                fs.remove(f"/{package}/{name}")
                if stem not in wanted:
                    changed.append(f"- {package}/{name}")
        for source in sources:
            data = _compile(source, work / f"{package}.{source.stem}.mpy")
            with fs.open(f"/{package}/{source.stem}.mpy", "wb") as handle:
                handle.write(data)
            changed.append(f"+ {package}/{source.stem}.mpy")
    with fs.open("/panel_main.mpy", "wb") as handle:
        handle.write(_compile(FIRMWARE / "main.py", work / "panel_main.mpy"))
    with fs.open("/main.py", "wb") as handle:
        handle.write(LAUNCHER)
    # New code is a fresh start: the panel clears its count of failed boots
    # when it finds this, so a fix lands on a panel that gave up on the old.
    with fs.open("/" + FRESH, "wb") as handle:
        handle.write(b"1")
    changed.append("+ panel_main.mpy, main.py (launcher), " + FRESH)
    return changed


def deploy(port: str) -> None:
    if shutil.which("mpy-cross") is None:
        sys.exit("mpy-cross is missing: run this through `make panel-deploy`")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        current = work / "vfs.bin"
        print(f"reading the panel's filesystem from {port}…")
        _esptool(
            port, "read-flash", hex(VFS_OFFSET), hex(VFS_SIZE), str(current), reset=False
        )

        BACKUPS.mkdir(exist_ok=True)
        backup = BACKUPS / time.strftime("vfs-%Y%m%d-%H%M%S.bin")
        shutil.copy(current, backup)
        print(f"backup: {backup}")

        fs = _mount(current.read_bytes())
        kept = [name for name in fs.listdir("/")
                if name not in (*PACKAGES, "main.py", "panel_main.mpy", FRESH)]
        for line in build(fs, work):
            print("  " + line)
        print("  kept: " + ", ".join(sorted(kept)))
        fs.unmount()

        image = work / "vfs-new.bin"
        image.write_bytes(bytes(fs.context.buffer))
        print("writing…")
        _esptool(port, "write-flash", hex(VFS_OFFSET), str(image))
    print("done; the panel has been reset")


def restore(port: str, image: Path) -> None:
    _mount(image.read_bytes())  # refuse a file that is not a filesystem image
    _esptool(port, "write-flash", hex(VFS_OFFSET), str(image))
    print(f"restored {image}; the panel has been reset")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True)
    parser.add_argument("--restore", type=Path, help="write a saved image back")
    args = parser.parse_args()
    if args.restore:
        restore(args.port, args.restore)
    else:
        deploy(args.port)


if __name__ == "__main__":
    main()
