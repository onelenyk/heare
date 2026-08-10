"""Settings — one dataclass, one TOML read, one env override loop.

``src/config.py`` is 878 lines for roughly forty numbers, because every
field is declared four times: as a dataclass field, in a TOML mapping
table, in an env mapping table, and in a range-clamp table. Here a field
is declared once and the loader walks the annotations.

Reads the same ``~/.heare/config.toml`` the daemon already uses, so keys
and device choices carry over untouched.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

HEARE_HOME = Path(os.environ.get("HEARE_HOME", Path.home() / ".heare"))

# field name -> environment variable
_ENV = {
    "groq_api_key": "GROQ_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "zai_api_key": "ZAI_API_KEY",
    "serper_api_key": "SERPER_API_KEY",
}


@dataclass
class Settings:
    # --- keys ---------------------------------------------------------
    groq_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    zai_api_key: str = ""
    zai_model: str = "glm-4.6"
    zai_base_url: str = "https://api.z.ai/api/coding/paas/v4"
    serper_api_key: str = ""

    # --- audio --------------------------------------------------------
    groq_language: str = "uk"
    tts_voice: str = "uk-UA-OstapNeural"
    tts_sample_rate: int = 24000
    audio_input_device: str = ""
    audio_output_device: str = ""
    vad_confidence: float = 0.5
    vad_start_secs: float = 0.2
    vad_stop_secs: float = 0.2
    vad_min_volume: float = 0.2

    # --- echo suppression (consumed by the reused AEC/gate stages) -----
    echo_gate_threshold: float = 0.15
    echo_gate_cooldown_seconds: float = 0.35
    echo_gate_peak_decay: float = 0.85
    echo_gate_peak_threshold: float = 0.08
    # Measured on this machine by cross-correlating what we sent against
    # what the microphone heard: ~120 ms, not the 30 ms that was assumed.
    # Point AEC3 at the wrong offset and it looks for the echo where the
    # echo is not, which caps suppression at a noisy 10-20 dB.
    aec_stream_delay_ms: int = 120
    aec_noise_suppression: bool = True

    # --- paths --------------------------------------------------------
    workspace_dir: Path = field(default_factory=lambda: HEARE_HOME / "workspace")
    db_path: Path = field(default_factory=lambda: HEARE_HOME / "core.db")

    # --- behaviour ----------------------------------------------------
    bash_timeout_secs: float = 120.0
    tool_timeout_secs: float = 180.0


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip("'\"")
    return out


def load_settings(path: Path | None = None) -> Settings:
    """Read config.toml, then env, then coerce to the declared types."""
    s = Settings()
    cfg = path or HEARE_HOME / "config.toml"

    raw: dict = {}
    if cfg.exists():
        with cfg.open("rb") as fh:
            raw = tomllib.load(fh)
    # Flat keys plus the one [audio] section the daemon writes.
    flat = {k: v for k, v in raw.items() if not isinstance(v, dict)}
    flat.update(raw.get("audio", {}))

    known = {f.name: f.type for f in fields(s)}
    for key, value in flat.items():
        if key in known:
            setattr(s, key, value)

    # Keys live in ~/.heare/.env, not in config.toml — the process
    # environment wins over the file so a shell override still works.
    dotenv = _read_dotenv(HEARE_HOME / ".env")
    for name, env in _ENV.items():
        value = os.environ.get(env) or dotenv.get(env, "")
        if value:
            setattr(s, name, value)

    # Coerce: TOML gives str/int/float, the dataclass declares the truth.
    for f in fields(s):
        value = getattr(s, f.name)
        if f.type is Path or f.name.endswith("_dir") or f.name.endswith("_path"):
            setattr(s, f.name, Path(str(value)).expanduser())
        elif f.type == "float" and not isinstance(value, float):
            setattr(s, f.name, float(value))
        elif f.type == "int" and not isinstance(value, int):
            setattr(s, f.name, int(value))
        elif f.type == "str" and value is None:
            setattr(s, f.name, "")

    s.workspace_dir.mkdir(parents=True, exist_ok=True)
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    return s
