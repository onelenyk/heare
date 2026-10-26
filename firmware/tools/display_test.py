"""Draw a full-pixel test and the eleven panel states on the OLED.

Run it on the board, after the firmware is on it::

    uvx mpremote connect /dev/cu.usbmodem214201 run firmware/tools/display_test.py

The controller is 128x64, but only the 72x40 window at (28,24) is visible.
The full frame stays lit for two seconds so a person can confirm the window;
the driver alone cannot, because a 128x64 SSD1306 and a 72x40 panel accept
the same init sequence. Then each state is drawn through the viewport for a
moment. A missing display prints ``display=none`` and exits without an
exception.
"""

import time

import framebuf
from heare_panel import display, render, state
from heare_panel.viewport import Viewport

HOLD_MS = 800


def _show(panel, data):
    if not panel.flush(data):
        print("display=lost")
        return False
    return True


def main():
    panel = display.open()
    if not panel.available:
        return

    size = panel.width * panel.height // 8
    print(
        f"display={panel.driver} {panel.width}x{panel.height} "
        f"addr=0x{panel.addr:02x} offset={panel.offset}"
    )
    print(
        f"view={panel.view_width}x{panel.view_height} xoff={panel.xoff} "
        f"yoff={panel.yoff} controller={panel.width}x{panel.height}"
    )

    if not _show(panel, b"\xff" * size):
        return
    print("full=on")
    time.sleep(2)
    if not _show(panel, b"\x00" * size):
        return
    print("full=off")

    data = bytearray(size)
    canvas = framebuf.FrameBuffer(
        data, panel.width, panel.height, framebuf.MONO_VLSB
    )
    viewport = Viewport(
        canvas, panel.xoff, panel.yoff, panel.view_width, panel.view_height
    )
    for name in state.STATES:
        render.render(name, viewport, panel.view_width, panel.view_height, 0)
        if not _show(panel, data):
            return
        print(f"state={name}")
        time.sleep(HOLD_MS / 1000)

    _show(panel, b"\x00" * size)
    print(f"states={len(state.STATES)} visual=unconfirmed")


main()
