"""Run the panel's setup mode over USB: print the AP, serve the form, wait.

The tool is deliberately thin. ``display.open`` and ``create`` are the only
board calls; everything else is the same ``Setup`` object the host tests
drive. Stop it with Ctrl-C, and the AP is closed on the way out.

    uvx mpremote connect /dev/cu.usbmodem214201 \\
        mount firmware/esp32-c3 run firmware/tools/portal_test.py
"""

import gc

from heare_panel import display
from heare_panel import portal_server
from heare_panel import store

RUN_MS = 5 * 60 * 1000


def main():
    panel = display.open()
    storage = store.Store()
    setup = portal_server.Setup(storage, display=panel)
    print("portal board=esp32c3 free={}".format(gc.mem_free()))
    setup.start()
    print("portal ready free={}".format(gc.mem_free()))
    reason = setup.serve(RUN_MS)
    print("portal done reason={} free={}".format(reason, gc.mem_free()))


main()
