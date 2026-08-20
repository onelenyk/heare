"""What the surroundings are allowed to make it say.

`changes` is a pure function of two readings, which is the only reason
these can be a table of cases instead of a day spent working next to it
waiting to see whether it interrupts.

The thing under test is mostly restraint. A watcher that remarks on every
window switch is switched off within the hour, so almost every case here
asserts silence — and the few that do not are the ones that had to earn
it.
"""

from __future__ import annotations

import asyncio

from src.spine.environment import (
    AWAY_S,
    DEEP_S,
    PRIVATE,
    SETTLED_S,
    Change,
    Environment,
    EnvironmentWatch,
    changes,
    observe,
)

NOW = 1_000_000.0


def env(**kw) -> Environment:
    base = dict(now=NOW, app="Superset", apps=("Superset", "Chrome"), idle_s=2.0)
    base.update(kw)
    return Environment(**base)


def kinds(found: list[Change]) -> list[str]:
    return [c.kind for c in found]


# ── silence, which is most of the job ─────────────────────────────────


def test_the_first_reading_says_nothing() -> None:
    """With nothing to compare against, everything looks like a change."""
    assert changes(None, env(), app_since=NOW) == []


def test_staying_put_is_not_news() -> None:
    before = env(now=NOW - 60)
    assert changes(before, env(), app_since=NOW - 600) == []


def test_passing_through_something_is_not_leaving_it() -> None:
    """Switching to a window for a minute and back out is navigation. If
    that earned a remark the assistant would narrate the whole day."""
    before = env(now=NOW - 5, app="Chrome")
    found = changes(before, env(app="Superset"), app_since=NOW - 120)
    assert found == []


def test_a_short_absence_is_not_a_return() -> None:
    before = env(now=NOW - 60, idle_s=90.0)
    assert changes(before, env(idle_s=1.0), app_since=NOW - 60) == []


# ── the three things it will speak about ──────────────────────────────


def test_leaving_something_you_were_really_in() -> None:
    before = env(now=NOW - 5, app="Android Studio")
    found = changes(
        before, env(app="Chrome"), app_since=NOW - SETTLED_S - 300
    )
    assert kinds(found) == ["switched"]
    assert "Android Studio" in found[0].text
    assert "Chrome" in found[0].text


def test_coming_back_after_being_properly_away() -> None:
    """Read against the previous sample on purpose: the idle counter
    resets the instant a key is touched, so by the time it notices you
    are back, the gap it should report is already gone."""
    before = env(now=NOW - 5, idle_s=AWAY_S + 600)
    found = changes(before, env(idle_s=1.0), app_since=NOW - 60)
    assert kinds(found) == ["returned"]
    assert "хв" in found[0].text or "год" in found[0].text


def test_still_in_one_thing_long_past_the_point() -> None:
    before = env(now=NOW - 5)
    found = changes(before, env(), app_since=NOW - DEEP_S - 60)
    assert kinds(found) == ["deep"]
    assert "Superset" in found[0].text


def test_it_says_that_once_per_stretch() -> None:
    """Otherwise it repeats itself every five seconds for the rest of the
    afternoon."""
    before = env(now=NOW - 5)
    found = changes(before, env(), app_since=NOW - DEEP_S - 60, deep_said=True)
    assert found == []


def test_durations_are_said_the_way_a_person_says_them() -> None:
    """"годину" for ninety minutes is the kind of small inaccuracy that
    makes an assistant sound like it was not really paying attention —
    which is the one impression a watcher cannot afford."""
    from src.spine.environment import _span

    assert _span(25 * 60) == "25 хв"
    assert _span(60 * 60) == "годину"
    assert _span(90 * 60) == "півтори години"
    assert _span(120 * 60) == "2 год"
    assert _span(150 * 60) == "2 з половиною год"


# ── privacy is a property, not a setting ──────────────────────────────


def test_it_does_not_report_what_it_will_not_look_at() -> None:
    """A private application is not redacted after the fact — nothing is
    recorded and nothing is emitted, in either direction."""
    before = env(now=NOW - 5, app="Android Studio")
    into_private = changes(
        before, env(app=PRIVATE), app_since=NOW - DEEP_S
    )
    out_of_private = changes(
        env(now=NOW - 5, app=PRIVATE), env(app="Chrome"), app_since=NOW - DEEP_S
    )
    assert into_private == []
    assert out_of_private == []


def test_a_private_app_never_reaches_a_snapshot_by_name() -> None:
    from src.spine.environment import _front_and_apps

    app, apps = _front_and_apps()
    assert app != "1Password"
    assert "1Password" not in apps


# ── what it holds between readings ────────────────────────────────────


def test_the_clock_on_an_application_restarts_when_you_leave_it() -> None:
    watch = EnvironmentWatch()
    watch.feed(env(now=NOW - DEEP_S, app="Superset"))
    watch.feed(env(now=NOW, app="Chrome"))
    assert watch.app_since == NOW

    # ...and an hour in Chrome is an hour in Chrome, not in Superset
    found = watch.feed(env(now=NOW + DEEP_S + 60, app="Chrome"))
    assert kinds(found) == ["deep"]
    assert "Chrome" in found[0].text


def test_the_long_stretch_remark_is_spent_and_renewed() -> None:
    watch = EnvironmentWatch()
    watch.feed(env(now=NOW - DEEP_S - 60))
    assert kinds(watch.feed(env(now=NOW))) == ["deep"]
    assert watch.feed(env(now=NOW + 5)) == []

    watch.feed(env(now=NOW + 10, app="Chrome"))
    assert kinds(watch.feed(env(now=NOW + 10 + DEEP_S + 60, app="Chrome"))) == [
        "deep"
    ]


def test_forgetting_is_complete() -> None:
    """A watcher that cannot be told to forget is not a watcher."""
    watch = EnvironmentWatch()
    watch.feed(env(now=NOW - DEEP_S - 60))
    watch.forget()

    assert watch.previous is None
    assert watch.feed(env(now=NOW)) == []  # first reading again


# ── sampling ──────────────────────────────────────────────────────────


def test_a_broken_sensor_still_returns_a_reading() -> None:
    """This runs on a timer beside a live conversation. A watcher that can
    take the assistant down with it is worse than no watcher."""

    async def boom(fn):
        raise OSError("no window server")

    got = asyncio.run(observe(now=NOW, in_thread=boom))

    assert got.now == NOW
    assert got.app == ""
    assert got.idle_s == 0.0


def test_it_reads_the_real_machine_without_asking_permission() -> None:
    """The whole point of this layer: no TCC dialog, no paid call, no
    model. If this ever starts prompting, it stops being free."""
    got = asyncio.run(observe(now=NOW))

    assert got.app, "no front application — pyobjc missing or unusable"
    assert got.apps, "no applications with windows"
    assert got.clipboard_seq >= 0
