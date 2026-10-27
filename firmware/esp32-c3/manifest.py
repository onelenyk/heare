# The panel's code frozen into the firmware: its bytecode is read from flash,
# not loaded into RAM, which leaves that RAM to Wi-Fi (27 October: ~100 KB of
# bytecode left Wi-Fi 7 KB at its worst, and the panel went deaf).
# scripts/panel_deploy.py stages the code in build/frozen, next to this file.
include("$(PORT_DIR)/boards/manifest.py")  # noqa: F821 - given by MicroPython's manifest
freeze("build/frozen")  # noqa: F821
