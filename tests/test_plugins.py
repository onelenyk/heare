"""The contract, and the one rule that makes it worth having.

`register()` refuses a plugin that cannot answer for itself, and `Health`
refuses a "no" with no reason. Both refusals are the point: this project's
recurring failure is not a feature that breaks loudly, it is a feature that
is absent and reports nothing — `browser_bridge_enabled = True`, a token on
disk, a pairing code in the dashboard, and no `BrowserBridge` constructed
anywhere in `src/`. A blank in a table reads as "fine".
"""

from __future__ import annotations

import pytest

from src.plugins import (
    Health,
    PluginBase,
    plugins,
    register,
    report,
    unregister,
)


@pytest.fixture(autouse=True)
def _clean_register():
    """The register is module state; a test that leaves a plugin in it
    changes what `make doctor` prints for every test after."""
    before = {p.name for p in plugins()}
    yield
    for name in {p.name for p in plugins()} - before:
        unregister(name)


class _Fine(PluginBase):
    name = "fine"
    cost = "нічого не станеться"

    def wire(self, core):
        return object()

    def health(self):
        return Health.working()


# ── Health ────────────────────────────────────────────────────────────


def test_a_no_must_say_why() -> None:
    """A red cell with no reason sends someone back to reading source,
    which is the day this whole thing exists to prevent."""
    with pytest.raises(ValueError):
        Health(declared=True, wired=False, reachable=False)


def test_a_yes_needs_no_reason() -> None:
    assert Health.working().ok


def test_the_three_failures_are_told_apart() -> None:
    """They look identical from outside and are fixed in different places:
    a switch, a composition root, and someone else's server."""
    assert Health.off().verdict == "off"
    assert Health(True, False, False, "не збудувався").verdict == "NOT WIRED"
    assert Health(True, True, False, "розширення не спарувалось").verdict == (
        "UNREACHABLE"
    )
    assert Health.working().verdict == "ok"


def test_off_is_not_a_fault() -> None:
    """Someone turned it off. That is an answer, not a problem."""
    assert not Health.off().declared


# ── the register ──────────────────────────────────────────────────────


def _row(name: str):
    return next(health for n, health in report() if n == name)


def test_a_plugin_that_answers_is_taken() -> None:
    register(_Fine())
    assert "fine" in [p.name for p in plugins()]
    assert _row("fine").ok


@pytest.mark.parametrize("missing", ["name", "cost", "wire", "health"])
def test_a_plugin_that_cannot_answer_is_refused(missing: str) -> None:
    """Refusing at import beats being counted. The alternative is a row
    that renders blank, and blank reads like working."""
    plugin = _Fine()
    setattr(plugin, missing, "" if missing in ("name", "cost") else None)
    before = plugins()
    with pytest.raises(TypeError, match=missing):
        register(plugin)
    assert plugins() == before


def test_two_plugins_cannot_share_a_name() -> None:
    register(_Fine())
    with pytest.raises(ValueError):
        register(_Fine())


def test_a_broken_probe_is_a_finding_not_a_crash() -> None:
    """The one moment this is read is the moment something is already
    wrong, so a health() that raises must appear in the table — as the
    failure it is — rather than take the table down with it."""

    class _Raises(_Fine):
        name = "raises"

        def health(self):
            raise RuntimeError("сокет закритий")

    register(_Raises())
    health = _row("raises")
    assert health.verdict == "NOT WIRED"
    assert "сокет закритий" in health.detail


def test_install_is_idempotent_but_register_is_not() -> None:
    """The composition root installs on every engine it builds; the same
    object twice is one plugin. A different object under a taken name is
    still a collision."""
    from src.plugins import install

    fine = _Fine()
    install(fine)
    install(fine)
    assert [p.name for p in plugins()].count("fine") == 1
    with pytest.raises(ValueError):
        install(_Fine())


def test_the_optional_half_defaults_to_nothing() -> None:
    """A plugin with no verbs, no settings and no routes writes two
    methods, not five."""
    plugin = _Fine()
    assert (plugin.tools(), plugin.settings(), plugin.routes()) == ((), (), ())
