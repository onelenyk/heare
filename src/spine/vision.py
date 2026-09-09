"""Looking at the screen — the expensive, on-demand counterpart to
``environment.py``.

``environment.observe()`` samples the surroundings constantly and cheaply:
which app is in front, how long since a key was pressed, whether the
pasteboard changed. It never looks at *content*, which is what makes it
affordable to run every few seconds.

This module is the other half. It costs a screenshot, a round trip and
about four hundredths of a cent, so it happens only when someone asks —
and then it answers with what is actually on the glass: the numbers in
the table, the error in the dialog, the name of the tab.

Two constraints shape everything here:

* **No new dependencies.** Capture is ``/usr/sbin/screencapture`` and
  scaling is ``sips``, both in every macOS, reached through
  ``subprocess`` exactly the way ``environment.py`` reaches ``osascript``.
  ``Quartz`` (and with it ``CGPreflightScreenCaptureAccess``) is not
  installed here — only ``pyobjc-framework-cocoa`` is — so the permission
  is checked by looking at what came back, not by asking first.
* **The screenshot leaves the machine.** It goes to the vision provider
  and nowhere else, it is never written into the database, and the file
  is deleted before this function returns. That is the whole reason the
  verb is explicit rather than something the assistant does on its own
  initiative: a look at the screen is a decision, and the person asking
  is the one making it.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import tempfile
from typing import Any

import httpx

logger = logging.getLogger("heare.spine.vision")

# Long enough for a retina screen and a slow model, short enough that a
# voice turn does not die of old age. Measured: capture + scale is well
# under a second, the model answered in 1.8 s.
CAPTURE_TIMEOUT_S = 10.0
MODEL_TIMEOUT_S = 45.0

# The longest edge the screenshot is scaled to before it is sent. Image
# cost is charged by dimensions, not by bytes, and past roughly this
# width the extra pixels buy nothing a model can read that it could not
# read already.
MAX_EDGE_PX = 1400

# Below this, whatever came back is not a screen. A capture that was
# refused permission does not fail — it succeeds and returns a picture
# of the wallpaper — so size is a floor, not a permission check.
MIN_PLAUSIBLE_BYTES = 4096

# What the model is told it is doing. Deliberately short: the answer is
# spoken verbatim, so the rules that matter are the voice rules.
SYSTEM = (
    "Ти дивишся на екран комп'ютера користувача і відповідаєш на його "
    "питання про те, що там. Відповідай українською, одним-двома "
    "реченнями, як у розмові вголос — без списків, без розмітки. "
    "Називай конкретне: числа, назви, помилки. Якщо того, про що "
    "питають, на екрані немає — так і скажи, коротко."
)

DEFAULT_QUESTION = "Що зараз на екрані?"


class VisionUnavailable(Exception):
    """Raised when the look could not happen at all. Carries a sentence
    already fit to be spoken — every caller here is a voice path."""


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)


def _capture_and_scale() -> bytes:
    """One JPEG of the main display, scaled down, as bytes.

    The file exists for as long as this function runs and no longer:
    a screenshot of someone's desktop is not something to leave in
    ``/tmp`` because it was convenient.
    """
    tmpdir = tempfile.mkdtemp(prefix="heare-look-")
    shot = os.path.join(tmpdir, "screen.jpg")
    try:
        # -x: no shutter sound. A voice assistant that clicks every time
        # it looks is one you stop asking to look.
        res = _run(["/usr/sbin/screencapture", "-x", "-t", "jpg", shot], CAPTURE_TIMEOUT_S)
        if res.returncode != 0 or not os.path.exists(shot):
            err = (res.stderr or b"").decode(errors="replace").strip()
            raise VisionUnavailable(
                "Не вдалося зняти екран. "
                + ("Схоже, немає дозволу на запис екрана." if not err else "")
            )
        _run(["/usr/bin/sips", "-Z", str(MAX_EDGE_PX), shot, "--out", shot], CAPTURE_TIMEOUT_S)
        data = open(shot, "rb").read()
        if len(data) < MIN_PLAUSIBLE_BYTES:
            raise VisionUnavailable(
                "Знімок екрана вийшов порожній — найпевніше, "
                "у налаштуваннях не дано дозволу на запис екрана."
            )
        return data
    finally:
        try:
            if os.path.exists(shot):
                os.remove(shot)
            os.rmdir(tmpdir)
        except OSError:  # pragma: no cover — best effort
            logger.warning("vision: could not clean up %s", tmpdir)


def resolve_vision(settings: Any) -> tuple[str, str, str]:
    """(base_url, api_key, model) for the look, or raise.

    Separate from ``spine.llm.resolve_llm`` on purpose: the conversation
    model and the eye are different choices. DeepSeek — the conversation
    model here — cannot take an image at all, so falling back to "whatever
    answers text" would produce a confident answer about a picture nobody
    looked at.
    """
    from src.agent.llm.providers import PROVIDERS

    name = (getattr(settings, "vision_provider", "") or "openrouter").lower()
    cfg = PROVIDERS.get(name)
    if cfg is None:
        raise VisionUnavailable(f"Провайдера зору «{name}» я не знаю.")
    api_key = getattr(settings, cfg.api_key_attr, None)
    if not api_key:
        raise VisionUnavailable(
            f"Щоб дивитись на екран, потрібен ключ {cfg.display_name}."
        )
    model = getattr(settings, "vision_model", "") or ""
    if not model:
        raise VisionUnavailable("Не налаштовано модель зору.")
    return cfg.base_url, api_key, model


async def look(
    question: str = "",
    *,
    settings: Any,
    usage: Any = None,
) -> str:
    """Look at the screen and answer, in one sentence fit to be spoken.

    Raises ``VisionUnavailable`` with a speakable message when the look
    cannot happen — no key, no permission, no model. Never raises
    anything else into a voice turn.
    """
    base_url, api_key, model = resolve_vision(settings)
    q = (question or "").strip() or DEFAULT_QUESTION

    data = await asyncio.to_thread(_capture_and_scale)
    b64 = base64.b64encode(data).decode()
    logger.info("vision: looking (%d KB) — %r", len(data) // 1024, q[:60])

    payload = {
        "model": model,
        "max_tokens": 300,
        # OpenRouter reports what the call actually cost, but only when
        # asked. That number beats any price table copied by hand — see
        # the comment in spine/usage.py about why this matters here.
        "usage": {"include": True},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": q},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=MODEL_TIMEOUT_S) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.warning("vision: transport failed: %s", exc)
        raise VisionUnavailable("Не додзвонився до моделі зору.") from exc

    if resp.status_code >= 400:
        logger.warning("vision: %s said %s: %.200s", model, resp.status_code, resp.text)
        raise VisionUnavailable("Модель зору відмовила.")

    try:
        body = resp.json()
        text = (body["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("vision: unreadable answer: %.200s", resp.text)
        raise VisionUnavailable("Модель зору відповіла незрозуміло.") from exc

    _record(usage, settings, model, body)

    if not text:
        raise VisionUnavailable("Модель подивилась, але нічого не сказала.")
    return text


def _record(usage: Any, settings: Any, model: str, body: dict) -> None:
    """Put the look in the ledger. Best-effort, like the rest of usage.

    A look is the most expensive single thing this assistant does per
    call. Leaving it uncounted is how the last accounting hole started.
    """
    u = body.get("usage") or {}
    try:
        if usage is None:
            from src.spine.usage import SpineUsage

            db_path = getattr(settings, "db_path", None)
            if not db_path:
                return
            usage = SpineUsage(db_path)
        usage.llm(
            model=model,
            input_tokens=int(u.get("prompt_tokens") or 0),
            output_tokens=int(u.get("completion_tokens") or 0),
            provider=(getattr(settings, "vision_provider", "") or "openrouter"),
            cost_usd=u.get("cost"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("vision: failed to record usage (non-fatal)")
