"""Auto-generates heare's persona on first run by asking `claude -p` to invent one."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from .claude_backend_common import ClaudeBackend
    from .config import Settings


class Identity(TypedDict):
    name: str
    creature: str
    vibe: str
    emoji: str
    tagline: str
    generated_at: str


REQUIRED_KEYS = ("name", "creature", "vibe", "emoji", "tagline")


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


async def ensure_identity(claude_cli: "ClaudeBackend", settings: "Settings") -> Identity:
    existing = load_identity(settings.identity_file)
    if existing is not None:
        return existing

    prompt_file = Path(__file__).parent.parent / "prompts" / "identity-bootstrap.txt"
    prompt = prompt_file.read_text()
    raw = await claude_cli.bootstrap_identity(prompt)
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
