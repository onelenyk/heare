"""The plugin contract, and the register that answers "what actually works?".

This exists because of a day spent finding out by hand which of the
assistant's capabilities were connected. The answer was about two thirds,
and no layer could say so itself. `browser_bridge_enabled` was True, the
token was on disk, the dashboard rendered a pairing code — and no line in
`src/` had ever constructed a `BrowserBridge`. Every surface a person can
look at claimed the feature was on.

Half the contract already existed, spread out. `features.Feature` carries
a name and what switching it off costs. `system.TOOLS` carries the verbs.
`spine/main.py` does the wiring, by hand, in a 720-line function where
every subsystem is attached its own way. What has never existed anywhere
is the fourth part, and it is the reason for the other three:

    health() — declared? wired? is the thing it needs actually there?

Each of those three questions is one this project has already shipped a
bug against:

    declared   nineteen tools the model could offer and nothing could run
    wired      the bridge and the sub-agents: set_*() called only in tests
    reachable  web search answering «нічого не знайшов» to a captcha page

A plugin that cannot answer them does not load. That is what turns
`unreachable_tools()` from a hand-written list into a question, and
`make doctor` into a print of the answer.

Plugins live in src/features/<name>/, and src/features/__init__.py is the
one list of what is installed. See docs/core-and-plugins.md for the order
things move in, and why the order is that one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("heare.plugins")


@dataclass(frozen=True)
class Health:
    """Three questions, and what to say when the answer is no.

    They are separate on purpose, because the three failures look
    identical from outside and are fixed in different places:

    ``declared``   the feature is asked for — the switch is on, the key
                   is set, the config says yes. False means a person
                   turned it off, which is not a fault.
    ``wired``      an object exists in this process. False after
                   ``declared`` is the silent one: the config asked and
                   nothing was built, and every surface still says "on".
    ``reachable``  what it depends on answers — the socket is paired, the
                   key is accepted, the server responded. False here is
                   the one that reports success while doing nothing.

    ``detail`` is for a person, in their language, and is required when
    anything is False: a red cell that does not say why sends someone
    back to reading source, which is the thing this replaces.
    """

    declared: bool
    wired: bool
    reachable: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ok and not self.detail.strip():
            raise ValueError(
                "Health with a False answer needs a detail saying why — "
                "a table of red cells with no reasons is not an instrument."
            )

    @property
    def ok(self) -> bool:
        return self.declared and self.wired and self.reachable

    @property
    def verdict(self) -> str:
        """The one word for the table's first column."""
        if not self.declared:
            return "off"
        if not self.wired:
            return "NOT WIRED"
        if not self.reachable:
            return "UNREACHABLE"
        return "ok"

    @classmethod
    def off(cls, detail: str = "switched off") -> Health:
        return cls(declared=False, wired=False, reachable=False, detail=detail)

    @classmethod
    def working(cls) -> Health:
        return cls(declared=True, wired=True, reachable=True)


@dataclass(frozen=True)
class WireContext:
    """What a plugin is handed when it is attached — and nothing more.

    ``enabled`` is the switch already resolved by the composition root
    (env > --without > config.toml > default, see src/spine/features.py).
    The plugin is told, not asked to work it out: there is one place that
    decides what runs, and a plugin reading the config for itself would be
    a second one.

    Deliberately small. A plugin that needs something else from the core
    should get it added here by name, in a commit that says why — not
    reach for it through the conductor.
    """

    settings: Any
    enabled: bool


@runtime_checkable
class Plugin(Protocol):
    """One optional capability, whole: its name, its price, its verbs, how
    it attaches, and whether it is actually working.

    ``tools``, ``settings`` and ``routes`` exist so that adding a feature
    stops meaning "edit eleven files". Today a new capability touches
    config.py, features.py, spine/main.py, loop.py, spine_engine.py twice,
    tools/system.py three times, tools/direct.py, hands.py, api.py and a
    React component. A plugin that carries its own verbs, its own settings
    and its own routes is the same feature in one directory.

    Only ``name``, ``cost``, ``wire`` and ``health`` are required; the
    other three default to empty (see ``PluginBase``).
    """

    name: str
    cost: str  # what the person loses when this is off, in their language

    def wire(self, ctx: WireContext) -> Any: ...

    def health(self) -> Health: ...

    def tools(self) -> tuple[Any, ...]: ...

    def settings(self) -> tuple[Any, ...]: ...

    def routes(self) -> tuple[Any, ...]: ...


class PluginBase:
    """The three optional halves of the contract, defaulted to nothing.

    Subclassing is not required — the register only checks shape — but it
    keeps a plugin that has no verbs, no settings and no routes down to
    the two methods it actually has to write.
    """

    name: str = ""
    cost: str = ""

    def wire(self, ctx: WireContext) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def health(self) -> Health:  # pragma: no cover - overridden
        raise NotImplementedError

    def tools(self) -> tuple[Any, ...]:
        return ()

    def settings(self) -> tuple[Any, ...]:
        return ()

    def routes(self) -> tuple[Any, ...]:
        return ()


# ── the register ──────────────────────────────────────────────────────

_REGISTERED: dict[str, Plugin] = {}

_REQUIRED = ("name", "cost", "wire", "health")


def register(plugin: Plugin) -> Plugin:
    """Take a plugin, or refuse it.

    Refusing is the point. A plugin that cannot answer ``health()`` would
    appear in the table as a blank, and a blank reads like "fine" — which
    is exactly how a feature that was never wired went on claiming to be
    on. Better to fail at import, loudly, than to be counted.
    """
    for attribute in _REQUIRED:
        if getattr(plugin, attribute, None) in (None, ""):
            raise TypeError(
                f"{plugin!r} cannot be registered: it has no {attribute}. "
                "A plugin that cannot say what it is, what it costs, how it "
                "attaches and whether it works is not one."
            )
    name = plugin.name
    if name in _REGISTERED:
        raise ValueError(f"two plugins named {name!r}")
    _REGISTERED[name] = plugin
    logger.debug("plugin registered: %s", name)
    return plugin


def install(*candidates: Plugin) -> None:
    """Register each plugin once, however many times this is called.

    The composition root runs once per engine it builds, and a process can
    build several (the test suite builds hundreds, `make doctor` builds
    one beside a live daemon). The same object twice is the same plugin;
    a *different* object under a taken name is still refused by
    ``register``.
    """
    for plugin in candidates:
        if _REGISTERED.get(plugin.name) is plugin:
            continue
        register(plugin)


def get(name: str) -> Plugin | None:
    """One plugin by name, or None."""
    return _REGISTERED.get(name)


def unregister(name: str) -> bool:
    """Drop one. For tests; nothing in the running app removes a plugin."""
    return _REGISTERED.pop(name, None) is not None


def plugins() -> tuple[Plugin, ...]:
    """Everything registered, in the order it was registered."""
    return tuple(_REGISTERED.values())


def report() -> list[tuple[str, Health]]:
    """Every plugin's own answer about itself.

    A plugin whose ``health()`` raises is reported as unwired rather than
    allowed to take the report down with it: the one moment this is read
    is the moment something is already wrong.
    """
    rows: list[tuple[str, Health]] = []
    for plugin in plugins():
        try:
            rows.append((plugin.name, plugin.health()))
        except Exception as exc:  # noqa: BLE001 - a broken probe is a finding
            rows.append(
                (
                    plugin.name,
                    Health(
                        declared=True,
                        wired=False,
                        reachable=False,
                        detail=f"health() raised: {exc}",
                    ),
                )
            )
    return rows


__all__ = [
    "Health",
    "Plugin",
    "PluginBase",
    "WireContext",
    "get",
    "install",
    "plugins",
    "register",
    "report",
    "unregister",
]
