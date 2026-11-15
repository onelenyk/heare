"""The entry code, carried to a phone: what the page mints, Telegram delivers.

A browser gets in with a one-time code minted where trust already is — the
⚙ → Devices panel of a paired page, or `heare-brain code` in a terminal. The
person is often not near that machine, and a code read out in a terminal is
useless on a phone. So the code is also posted to a Telegram chat.

The bot token is a key (`TELEGRAM_BOT_TOKEN`: the store never shows it); the
chat and its topic are plain settings. Without both, nothing is sent — a brain
that never asked for this mints codes exactly as before.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Callable

from heare import keys
from heare.core.i18n import _

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_S = 8.0
Sender = Callable[[str, dict[str, object]], None]


def text(code: str, minutes: int = 5) -> str:
    """What the message says: the code itself, and how long it lives."""
    lease = _("{minutes} min, once. ⚙ → Devices on the brain's page.").format(
        minutes=minutes)
    return f"<b>{_('heare: entry code')}</b>\n\n<code>{code}</code>\n\n{lease}"


def carry(code: str, settings: object | None = None,
          send: Sender | None = None) -> bool:
    """Post the code to Telegram; False when nobody said where to post it.

    ``settings`` is anything that answers for the chat and its topic — the
    caller's own settings, read here by name: a carrier knows nothing of the
    file they come from.
    """
    token = keys.get("TELEGRAM_BOT_TOKEN")
    chat = str(getattr(settings, "telegram_chat_id", "") or "").strip()
    if not token or not chat:
        return False
    payload: dict[str, object] = {
        "chat_id": chat,
        "text": text(code),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    thread = str(getattr(settings, "telegram_thread_id", "") or "").strip()
    if thread.isdigit():
        payload["message_thread_id"] = int(thread)
    try:
        (send or _post)(token, payload)
    except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        # A code that did not reach the phone is still a good code: the page
        # that minted it showed it, and the next mint carries another. The log
        # names what went wrong — never the code, never the token.
        log.warning("telegram did not take the code: %s",
                    getattr(exc, "code", None) or type(exc).__name__)
        return False
    return True


def _post(token: str, payload: dict[str, object]) -> None:
    request = urllib.request.Request(
        API.format(token=token),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:
        if answer.status != 200:
            raise ValueError(f"HTTP {answer.status}")
