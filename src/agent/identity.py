"""Auto-generates heare's persona on first run by asking an LLM to invent one."""
from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, TypedDict

import httpx

if TYPE_CHECKING:
    from src.config import Settings


logger = logging.getLogger("heare.identity")

BootstrapFn = Callable[[str], Awaitable[dict]]


class Identity(TypedDict):
    name: str
    creature: str
    vibe: str
    emoji: str
    tagline: str
    generated_at: str


REQUIRED_KEYS = ("name", "creature", "vibe", "emoji", "tagline")

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _validate(payload: dict) -> Identity:
    missing = [k for k in REQUIRED_KEYS if not payload.get(k)]
    if missing:
        raise ValueError(
            f"identity bootstrap payload is missing required keys: {missing}"
        )
    return {
        "name": str(payload["name"]),
        "creature": str(payload["creature"]),
        "vibe": str(payload["vibe"]),
        "emoji": str(payload["emoji"]),
        "tagline": str(payload["tagline"]),
        "generated_at": str(
            payload.get(
                "generated_at",
                dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        ),
    }


def load_identity(path: Path) -> Identity | None:
    if not path.exists():
        return None
    try:
        return _validate(json.loads(path.read_text()))
    except (json.JSONDecodeError, ValueError):
        return None


def _coerce_bootstrap_arg(arg) -> BootstrapFn:
    """Accept either a callable or a backend exposing ``bootstrap_identity``.

    The attribute lookup wins when both apply (e.g. ``AsyncMock`` instances
    are themselves callable, but the caller intends ``bootstrap_identity``).
    """
    fn = getattr(arg, "bootstrap_identity", None)
    if fn is not None and callable(fn):
        return fn
    if callable(arg):
        return arg  # type: ignore[return-value]
    raise TypeError(
        "ensure_identity expected a callable(prompt)->dict or an object "
        "with an async bootstrap_identity(prompt) method"
    )


async def ensure_identity(bootstrap, settings: "Settings") -> Identity:
    """Load an existing identity or bootstrap one via the supplied callable.

    ``bootstrap`` is either an async callable ``(prompt: str) -> dict`` or an
    object exposing such a method as ``bootstrap_identity``.
    """
    existing = load_identity(settings.identity_file)
    if existing is not None:
        return existing

    bootstrap_fn = _coerce_bootstrap_arg(bootstrap)
    prompt_file = Path(__file__).parent.parent.parent / "prompts" / "identity-bootstrap.txt"
    prompt = prompt_file.read_text()
    raw = await bootstrap_fn(prompt)
    identity = _validate(raw)

    settings.identity_file.parent.mkdir(parents=True, exist_ok=True)
    settings.identity_file.write_text(json.dumps(identity, ensure_ascii=False, indent=2))
    return identity


def render_persona(template: str, identity: Identity) -> str:
    return template.format(
        name=identity["name"],
        creature=identity["creature"],
        vibe=identity["vibe"],
        emoji=identity["emoji"],
    )


def reset_identity(settings: "Settings") -> Path | None:
    path = settings.identity_file
    if not path.exists():
        return None
    idx = 0
    while True:
        backup = path.with_name(f"identity_{idx}.backup.json")
        if not backup.exists():
            break
        idx += 1
    shutil.move(str(path), str(backup))
    return backup


def build_openrouter_bootstrap(
    *,
    api_key: str,
    model: str,
    timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BootstrapFn:
    """Return an async ``bootstrap(prompt) -> dict`` backed by OpenRouter.

    Asks the model for a single JSON object via /chat/completions and
    extracts the first ``{...}`` block from ``choices[0].message.content``.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/onelenyk/heare",
        "X-Title": "heare",
    }

    async def _bootstrap(prompt: str) -> dict:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are inventing a small voice-assistant persona. "
                        "Reply with ONLY one compact JSON object — no prose, "
                        "no code fence."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 400,
            "temperature": 0.9,
        }
        kwargs: dict = {"timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(_OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        try:
            raw = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"openrouter identity bootstrap: malformed response: {e}"
            ) from e
        if not isinstance(raw, str):
            raise RuntimeError("openrouter identity bootstrap: non-string content")
        text = raw.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(
                "openrouter identity bootstrap: no JSON object in reply"
            )
        return json.loads(text[start : end + 1])

    return _bootstrap
