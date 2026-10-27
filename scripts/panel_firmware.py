"""Build MicroPython with the panel's code frozen in, and put it on the panel.

Why frozen: bytecode loaded from the filesystem lives in RAM, and on the
ESP32-C3 ~100 KB of it left Wi-Fi as little as 7 KB; the panel went deaf to
the network while still drawing its clock (27 October). Frozen bytecode is
read from flash, so that RAM goes back to Wi-Fi.

The build runs in Docker (``espressif/idf``), against a MicroPython checkout
in ``~/.cache/heare/micropython``. Flashing writes only the app region below
the filesystem, so ``config.json`` and the part key in NVS stay; then the
code that is now frozen is removed from the filesystem, where it would win
over the frozen copy (``''`` comes before ``.frozen`` in ``sys.path``).

    make panel-deploy PORT=/dev/cu.usbmodem14101   # DEBUG=1 to read its log
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import panel_deploy as deploy

FIRMWARE = deploy.FIRMWARE
STAGE = FIRMWARE / "build" / "frozen"
MICROPYTHON = Path.home() / ".cache" / "heare" / "micropython"
MP_TAG = "v1.29.0"
IDF_IMAGE = "espressif/idf:v5.4.2"
BOARD = "ESP32_GENERIC_C3"
BUILT = MICROPYTHON / "ports" / "esp32" / f"build-{BOARD}" / "firmware.bin"
# The pieces, each at its own address. Not the merged firmware.bin: it spans
# NVS (0x9000-0xF000) with blanks, and NVS holds the part key — written whole,
# it unpaired the panel (27 October).
PIECES = (("0x0", "bootloader/bootloader.bin"),
          ("0x8000", "partition_table/partition-table.bin"),
          ("0x10000", "micropython.bin"))


def stage() -> None:
    """The code as the firmware will hold it: main.py is ``panel_main``."""
    shutil.rmtree(STAGE, ignore_errors=True)
    (STAGE / "heare_panel").mkdir(parents=True)
    for source in (FIRMWARE / "heare_panel").glob("*.py"):
        shutil.copy(source, STAGE / "heare_panel" / source.name)
    # /lib is on the board's path, so its modules are top-level when frozen.
    for source in (FIRMWARE / "lib").glob("*.py"):
        shutil.copy(source, STAGE / source.name)
    shutil.copy(FIRMWARE / "main.py", STAGE / "panel_main.py")


def build() -> Path:
    if not MICROPYTHON.exists():
        MICROPYTHON.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", MP_TAG,
                        "https://github.com/micropython/micropython.git",
                        str(MICROPYTHON)], check=True)
    stage()
    script = (
        "git config --global --add safe.directory '*' && "
        "make -C /mp/mpy-cross >/dev/null && "
        f"make BOARD={BOARD} FROZEN_MANIFEST=/fw/manifest.py submodules >/dev/null && "
        f"make BOARD={BOARD} FROZEN_MANIFEST=/fw/manifest.py"
    )
    subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{MICROPYTHON}:/mp", "-v", f"{FIRMWARE}:/fw",
         "-w", "/mp/ports/esp32", IDF_IMAGE, "bash", "-c", script],
        check=True,
    )
    print(f"built {BUILT} ({BUILT.stat().st_size // 1024} KB)")
    return BUILT


def unfreeze_filesystem(port: str, debug: bool = False) -> None:
    """Leave only the launcher of the code: the .mpy copies would shadow it."""
    with tempfile.TemporaryDirectory() as tmp:
        current = Path(tmp) / "vfs.bin"
        deploy._esptool(port, "read-flash", hex(deploy.VFS_OFFSET),
                        hex(deploy.VFS_SIZE), str(current), reset=False)
        fs = deploy._mount(current.read_bytes())
        for package in deploy.PACKAGES:
            if package in fs.listdir("/"):
                for name in fs.listdir("/" + package):
                    if name.endswith((".py", ".mpy")):
                        fs.remove(f"/{package}/{name}")
                        print(f"  - {package}/{name}")
                # Even empty, the folder is a package that hides the frozen one.
                if not fs.listdir("/" + package):
                    fs.rmdir("/" + package)
                    print(f"  - {package}/")
        if "panel_main.mpy" in fs.listdir("/"):
            fs.remove("/panel_main.mpy")
            print("  - panel_main.mpy")
        with fs.open("/main.py", "wb") as handle:
            handle.write(deploy.LAUNCHER)
        with fs.open("/" + deploy.FRESH, "wb") as handle:
            handle.write(b"1")
        # Printing waits for someone to read the port; only a debugged panel prints.
        if debug:
            with fs.open("/" + deploy.DEBUG, "wb") as handle:
                handle.write(b"1")
        elif deploy.DEBUG in fs.listdir("/"):
            fs.remove("/" + deploy.DEBUG)
        fs.unmount()
        image = Path(tmp) / "vfs-new.bin"
        image.write_bytes(bytes(fs.context.buffer))
        deploy._esptool(port, "write-flash", hex(deploy.VFS_OFFSET), str(image),
                        reset=False)


def flash(port: str, firmware: Path, debug: bool = False) -> None:
    deploy.BACKUPS.mkdir(exist_ok=True)
    backup = deploy.BACKUPS / time.strftime("app-%Y%m%d-%H%M%S.bin")
    print(f"backup of the current firmware: {backup}")
    deploy._esptool(port, "read-flash", "0x0", hex(deploy.VFS_OFFSET), str(backup),
                    reset=False)
    if firmware.stat().st_size > deploy.VFS_OFFSET:
        sys.exit("the firmware would overwrite the filesystem")
    print("removing the code the firmware now holds from the filesystem…")
    unfreeze_filesystem(port, debug)
    print("writing the firmware…")
    pieces = []
    for address, name in PIECES:
        pieces += [address, str(firmware.parent / name)]
    deploy._esptool(port, "write-flash", *pieces)
    print("done; the panel has been reset")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="flash it too")
    parser.add_argument("--debug", action="store_true",
                        help="the panel prints its log to the port (read it, or it stalls)")
    parser.add_argument("--restore", type=Path, help="write a saved app image back")
    args = parser.parse_args()
    if args.restore:
        # The backup holds NVS as it was: the key the panel was paired with.
        deploy._esptool(args.port, "write-flash", "0x0", str(args.restore))
        return
    firmware = build()
    if args.port:
        flash(args.port, firmware, args.debug)


if __name__ == "__main__":
    main()
