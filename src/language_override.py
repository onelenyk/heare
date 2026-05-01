"""Language override for testing and manual control.

Similar to the provider file, allows overriding detected language by writing
to ~/.heare/language file. Used for testing and manual language switching.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("heare.language_override")

HEARE_HOME = Path.home() / ".heare"
LANGUAGE_FILE = HEARE_HOME / "language"


def read_language_override() -> str | None:
    """Read language override from ~/.heare/language file.

    Returns:
        Language code ("en", "uk", "ru") or None if file doesn't exist
    """
    if not LANGUAGE_FILE.exists():
        return None
    try:
        lang = LANGUAGE_FILE.read_text().strip().lower()
        if lang in ("en", "uk", "ru"):
            logger.info("[LANGUAGE OVERRIDE] Read from file: %s", lang)
            return lang
    except Exception as e:
        logger.warning("[LANGUAGE OVERRIDE] Error reading file: %s", e)
    return None
