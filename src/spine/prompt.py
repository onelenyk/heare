"""System prompt assembly for the spine.

Composes Ukrainian voice-first prompts with optional persona, memory, and
recent exchanges — the spine's three key ingredients, without daemon machinery.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def build_system_prompt(
    *,
    persona: str = "",
    memory_block: str = "",
    exchanges: list[dict] | None = None,
    now: datetime | None = None,
) -> str:
    """Compose the spine's system prompt.

    Always contains the voice rules (short Ukrainian spoken prose, no markup —
    keep the spirit of loop.py's DEFAULT_SYSTEM_PROMPT). Adds sections ONLY
    when non-empty: persona paragraph; 'Що ти пам'ятаєш:' + memory_block;
    'Останні розмови:' + exchanges rendered as 'Користувач: .../Ти: ...'
    lines (cap each line at 200 chars); current date/time line when now given.

    Deterministic: same inputs -> same string.
    """
    voice_rules = (
        "Ти голосовий асистент на ім'я heare. Тебе слухають вухами, а не "
        "читають: відповідай коротко, українською, простою прозою — без "
        "розмітки, списків і коду. Одна-три фрази, як у живій розмові."
    )

    parts: list[str] = [voice_rules]

    if persona:
        parts.append(persona)

    if memory_block:
        parts.append(f"Що ти пам'ятаєш:\n{memory_block}")

    if exchanges:
        rendered_lines: list[str] = []
        for exchange in exchanges:
            user_text = (exchange.get("user") or "").strip()
            agent_text = (exchange.get("agent") or "").strip()

            if user_text:
                truncated = user_text[:200]
                rendered_lines.append(f"Користувач: {truncated}")

            if agent_text:
                truncated = agent_text[:200]
                rendered_lines.append(f"Ти: {truncated}")

        if rendered_lines:
            parts.append("Останні розмови:\n" + "\n".join(rendered_lines))

    if now is not None:
        date_str = now.strftime("%Y-%m-%d %H:%M:%S")
        parts.append(f"Зараз: {date_str}")

    return "\n\n".join(parts)


def load_persona(settings: object) -> str:
    """Best-effort read of the generated persona text from its file on disk.

    Resolves path from settings.identity_file if available, otherwise tries
    ~/.heare/identity.json. Reads the identity JSON and formats it as a
    persona string. Missing file or any error -> '' (never raises).
    """
    try:
        # Resolve identity file path
        identity_path: Path | None = None
        if hasattr(settings, "identity_file"):
            path_obj = getattr(settings, "identity_file")
            identity_path = Path(path_obj) if path_obj is not None else None

        if identity_path is None:
            # Fallback to default location
            identity_path = Path.home() / ".heare" / "identity.json"

        if not identity_path.exists():
            return ""

        # Read and parse identity JSON
        text = identity_path.read_text()
        data: dict[str, Any] = json.loads(text)

        # Extract identity fields
        name = data.get("name", "").strip()
        creature = data.get("creature", "").strip()
        vibe = data.get("vibe", "").strip()
        emoji = data.get("emoji", "").strip()

        # Format as persona text (return empty if missing required fields)
        if not (name and creature):
            return ""

        # Build persona string: "Я на ім'я [name], я [creature], [vibe] [emoji]"
        parts: list[str] = []
        parts.append(f"Я на ім'я {name}, я {creature}")

        if vibe:
            vibe_part = f"мій стиль — {vibe}"
            if emoji:
                vibe_part += f" {emoji}"
            parts.append(vibe_part)
        elif emoji:
            parts.append(emoji)

        return ", ".join(parts) + "."

    except Exception:
        # Silently catch all errors: missing file, JSON parse error, etc.
        return ""
