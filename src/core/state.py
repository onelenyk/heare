"""Runtime knobs — a plain object living in the process.

The old ``src/state.py`` persisted every knob to sqlite, and tools reached
it by POSTing to ``http://127.0.0.1:9778/state`` — an HTTP round trip from
the daemon to itself to change a number in its own memory.

Nothing here needs to survive a restart or cross a process boundary, so
nothing here does. ``get``/``set`` keep the shape ``SwitchableLLMService``
expects; the rest is attribute access.
"""

from __future__ import annotations

_TRUTHY = {"1", "true", "yes", "on"}


class State:
    """Mutable settings read while the pipeline runs."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self.output_volume: float = 1.0
        self.input_gain: float = 1.0
        self.muted: bool = False

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def get_bool(self, key: str) -> bool:
        return self._values.get(key, "").strip().lower() in _TRUTHY

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._values[key])
        except (KeyError, ValueError):
            return default

    async def set(self, key: str, value: str) -> None:
        self._values[key] = str(value)

    def set_cache_only(self, key: str, value: str) -> None:
        self._values[key] = str(value)

    def snapshot(self) -> dict[str, str]:
        return dict(self._values)
