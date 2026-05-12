"""``python -m src.overlay`` — launch the overlay window."""
from __future__ import annotations

import sys

from src.overlay.app import run


if __name__ == "__main__":
    sys.exit(run())
