"""The brain's small abilities, each with a live state and named actions.

This is an assembly module: it knows memory and the brain's own passes, and
the door knows none of them. The brain hands the registry over the same way
it hands over the mind — the door draws what it is given, never a feature by
name.

A feature never draws HTML and never holds a job queue. Its ``read`` says
what is true now; its ``write`` performs one named action and may raise
``ValueError`` for a bad name or value. Work that outlives a request is
started by the registry, which reports ``running`` and then ``done`` or
``error`` as events.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from heare.memory.review import last_reviewed
from heare.core.i18n import N_, _

if TYPE_CHECKING:
    from heare.brain import Brain

logger = logging.getLogger("heare.features")

# The drawers the page shows. A feature names one; a group without features
# is not sent. The number is the drawer's place, not the feature's.
GROUPS: dict[str, tuple[str, int]] = {"memory": (N_("Memory"), 10)}


@dataclass(frozen=True)
class Feature:
    """One ability: what it is, and how to read and change it."""

    id: str
    title: str
    about: str
    group: str
    order: int
    read: Callable[[], dict[str, object]]
    write: Callable[[str, object], Awaitable[None]]


class Features:
    """Every feature the brain has, and the work it may be running."""

    def __init__(self, emit: Callable[..., object]) -> None:
        self._emit = emit
        self._features: dict[str, Feature] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        # The last failure of each feature, shown until it works again.
        self._errors: dict[str, str] = {}

    def add(self, feature: Feature) -> None:
        if feature.id in self._features:
            raise ValueError(f"a feature named {feature.id!r} already exists")
        self._features[feature.id] = feature

    def has(self, id: str) -> bool:
        return id in self._features

    def _view(self, feature: Feature) -> dict[str, object]:
        base: dict[str, object] = {
            "id": feature.id, "title": feature.title, "about": feature.about,
            "group": feature.group, "order": feature.order,
        }
        try:
            view = dict(feature.read())
        except Exception:  # noqa: BLE001 — one broken feature must not hide the rest
            logger.exception("feature %s could not be read", feature.id)
            return {
                **base, "state": "error", "note": _("could not read it"),
                "controls": [],
            }
        if feature.id in self._errors:
            view["note"] = self._errors[feature.id]
        task = self._running.get(feature.id)
        if task is not None and not task.done():
            view["state"] = "running"
        return {**base, **view}

    def list(self) -> dict[str, object]:
        ordered = sorted(
            self._features.values(),
            key=lambda feature: (
                _group_order(feature.group), feature.order, feature.title
            ),
        )
        groups: list[dict[str, object]] = []
        seen: set[str] = set()
        for feature in ordered:
            if feature.group in seen:
                continue
            seen.add(feature.group)
            title, order = GROUPS.get(
                feature.group, (feature.group, _group_order(feature.group)))
            groups.append(
                {"id": feature.group, "title": title, "order": order})
        return {
            "groups": groups,
            "features": [self._view(feature) for feature in ordered],
        }

    async def act(self, id: str, name: object,
                  value: object) -> dict[str, object]:
        """Perform one named action and return the feature as it is after."""
        feature = self._features.get(id)
        if feature is None:
            raise ValueError("unknown feature")
        if not isinstance(name, str) or not name:
            raise ValueError("an action needs a name")
        self._errors.pop(id, None)
        await feature.write(name, value)
        self._emit("feature", id=id, state="changed")
        return {"ok": True, "feature": self._view(feature)}

    def start(self, id: str,
              work_factory: Callable[[], Awaitable[object]]) -> bool:
        """Run work in the background unless this feature is already running.

        A factory, never a coroutine: a start that is refused must not leave
        an un-awaited coroutine behind. The task is kept here so it is not
        collected before it finishes.
        """
        feature = self._features.get(id)
        if feature is None:
            raise ValueError("unknown feature")
        running = self._running.get(id)
        if running is not None and not running.done():
            return False
        task = asyncio.create_task(self._run(id, work_factory))
        self._running[id] = task
        self._emit("feature", id=id, state="running")
        return True

    async def _run(self, id: str,
                   work_factory: Callable[[], Awaitable[object]]) -> None:
        state = "done"
        try:
            await work_factory()
        except Exception as exc:  # noqa: BLE001 — a feature fails alone
            logger.exception("feature %s failed", id)
            self._errors[id] = str(exc) or _("error")
            state = "error"
        finally:
            if state == "done":
                self._errors.pop(id, None)
            self._running.pop(id, None)
            self._emit("feature", id=id, state=state)


def _group_order(group: str) -> int:
    return GROUPS.get(group, (group, 0))[1]


def _hour(value: object) -> int:
    """A clock hour: an integer 0..23, and a plain numeric string as well."""
    if isinstance(value, bool):
        raise ValueError("hour must be an integer from 0 to 23")
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise ValueError("hour must be an integer from 0 to 23")
        value = int(text)
    if not isinstance(value, int) or not 0 <= value <= 23:
        raise ValueError("hour must be an integer from 0 to 23")
    return value


def _consolidated_at(root: Path) -> datetime | None:
    try:
        moment = (root / "consolidate.json").stat().st_mtime
        return datetime.fromtimestamp(moment).replace(microsecond=0)
    except (OSError, OverflowError, ValueError):
        return None


def build_features(brain: Brain) -> Features:
    """The first slice: the two memory passes, as the page sees them."""
    features = Features(brain.core.state.emit)

    def review_read() -> dict[str, object]:
        enabled = bool(brain.settings.review_enabled)
        last = last_reviewed(Path(brain.settings.memory_dir))
        if not enabled:
            note = _("off")
        elif last is None:
            note = _("not yet")
        else:
            note = _("last: {when}").format(when=f"{last:%d.%m}")
        return {
            "state": "ready" if enabled else "off",
            "note": note,
            "controls": [
                {
                    "kind": "toggle", "name": "enabled", "label": N_("on"),
                    "value": enabled,
                },
                {
                    "kind": "slider", "name": "hour", "label": N_("hour"),
                    "value": int(brain.settings.review_hour),
                    "min": 0, "max": 23, "step": 1, "unit": N_("h"),
                },
                {"kind": "button", "name": "run", "label": N_("Review now")},
                {
                    "kind": "label", "name": "last", "label": N_("last"),
                    "value": f"{last:%d.%m}" if last is not None else "—",
                },
            ],
        }

    async def review_run() -> None:
        report = await brain.review_once()
        if report is not None and report.error:
            raise RuntimeError(report.error)

    async def review_write(name: str, value: object) -> None:
        if name == "enabled":
            if not isinstance(value, bool):
                raise ValueError("enabled must be true or false")
            brain.settings.set("review_enabled", value)
        elif name == "hour":
            brain.settings.set("review_hour", _hour(value))
        elif name == "run":
            features.start("review", review_run)
        else:
            raise ValueError(f"unknown action: {name}")

    features.add(Feature(
        id="review", title=N_("Daily review"),
        about=N_("Once a day it rereads yesterday's conversation and remembers what was missed."),
        group="memory", order=10, read=review_read, write=review_write,
    ))

    def consolidation_read() -> dict[str, object]:
        last = _consolidated_at(Path(brain.settings.memory_dir))
        if last is None:
            note, shown = _("not yet"), "—"
        else:
            stamp = f"{last:%d.%m, %H:%M}"
            note, shown = _("last pass: {when}").format(when=stamp), stamp
        return {
            "state": "ready",
            "note": note,
            "controls": [
                {"kind": "button", "name": "run", "label": N_("Remember now")},
                {
                    "kind": "label", "name": "last", "label": N_("last"),
                    "value": shown,
                },
            ],
        }

    async def consolidation_write(name: str, value: object) -> None:
        if name != "run":
            raise ValueError(f"unknown action: {name}")
        features.start("consolidation", brain.consolidate_once)

    features.add(Feature(
        id="consolidation", title=N_("Remembering"),
        about=N_("Every 5 minutes it reads what was newly said and remembers facts about you."),
        group="memory", order=20,
        read=consolidation_read, write=consolidation_write,
    ))
    return features
