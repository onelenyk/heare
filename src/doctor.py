"""`make doctor` — what is actually connected, printed by the program.

    uv run python -m src.doctor

Written on 9 September 2026, the day after one was spent working the same
question out by hand: which of this assistant's capabilities are wired?
The answer was about two thirds, and it took a day because no layer could
say. Every surface a person can look at — the config, the dashboard, the
tool list the model is offered — reports what was *asked for*. Nothing
reported what was *built*.

So this builds the whole engine with no microphone and no speaker, the way
`--check` does, and then asks the live objects rather than the config:

    plugins   the register in src/plugins.py — each feature that has
              moved into src/features/ answers health() for itself
    features  wired_features(): `loop.aec is not None`, not
              `spine_features.aec`. A subsystem that failed to build
              reads OFF here, which is the whole point of the instrument
    tools     what the worker would offer the model, and what it hides
              because nothing in this process could carry it out

It opens no device and writes nothing down, so it is safe to run beside a
live daemon — but it reports *this* process. A subsystem that connects
after boot (MCP servers take a cold `npx`) reads off here and is on there.
The dashboard's features card is the one that watches the running daemon.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _rule(title: str) -> str:
    return f"\n{title}\n" + "─" * max(len(title), 48)


def _plugin_section() -> list[str]:
    from src.plugins import report

    rows = report()
    if not rows:
        return [
            "  (порожньо — жодна фіча не зареєстрована)",
            "  docs/core-and-plugins.md — у якому порядку вони їдуть і чому",
        ]
    width = max(len(name) for name, _ in rows)
    lines = []
    for name, health in sorted(rows, key=lambda r: (r[1].ok, r[0])):
        detail = f"  — {health.detail}" if health.detail else ""
        lines.append(f"  {name:<{width}}  {health.verdict:<12}{detail}")
    return lines


# Names this process cannot honestly judge, and why. `_build_loop` opens no
# audio device and is not the daemon runner, so three subsystems read OFF
# here for a reason that has nothing to do with whether they work. Saying
# "off" about those would be the exact sin this instrument exists to catch,
# one level up.
_NOT_OURS_TO_SAY: dict[str, str] = {
    "aec": "потребує мікрофона — доктор його не відкриває",
    "mcp": "будується демоном (src/daemon/spine_engine.py), не тут",
    "telemetry": "будується демоном (src/daemon/spine_engine.py), не тут",
}


async def _survey(settings) -> tuple[list[str], list[str]]:
    """Build the engine once and ask everything while it is standing.

    Plugins have to be asked before the engine is closed: closing is what
    releases the ledger's connection, and a health() read afterwards would
    report the shutdown rather than the state.
    """
    from src.daemon.spine_engine import wired_features
    from src.spine.main import _build_loop, _close_loop

    loop = await _build_loop(settings, audio=None, voice="", hold_s=1.3, full=True)
    try:
        plugin_lines = _plugin_section()
        entries = wired_features(loop)
    finally:
        await _close_loop(loop)
    return plugin_lines, _feature_lines(entries)


def _feature_lines(entries: list[dict]) -> list[str]:
    """The old way of answering, for whatever has not moved yet.

    A feature answered by its plugin is left out here — it is in the
    section above, in its own words. Watching rows leave this list is how
    the migration shows.
    """
    from src.plugins import get as get_plugin

    entries = [e for e in entries if get_plugin(e["name"]) is None]
    if not entries:
        return ["  (усі фічі вже відповідають за себе самі)"]
    width = max(len(e["name"]) for e in entries)
    lines = []
    for entry in sorted(entries, key=lambda e: (e["on"], e["name"])):
        name = entry["name"]
        if not entry["on"] and name in _NOT_OURS_TO_SAY:
            lines.append(f"  {name:<{width}}  {'?':<12}— {_NOT_OURS_TO_SAY[name]}")
            continue
        mark = "on" if entry["on"] else "OFF"
        # `observed` is the difference between a fact and a restatement of
        # the config. Anything unobserved is labelled, because an
        # instrument that cannot tell the two apart is the thing being
        # replaced here.
        source = "" if entry["observed"] else "  (з конфігу, не з об'єкта)"
        cost = f"  — {entry['cost']}" if not entry["on"] else ""
        lines.append(f"  {name:<{width}}  {mark:<12}{cost}{source}")
    return lines


def _tool_section() -> list[str]:
    from src.agent.hands import unreachable_tools
    from src.agent.tools.system import TOOLS, VOICE_TOOLS

    hidden = unreachable_tools()
    names = {t.name for t in TOOLS if t.enabled}
    offered = names - hidden

    lines = [
        f"  оголошено      {len(names)}",
        f"  доручається    {len(offered)}",
        f"  голосу прямо   {len(VOICE_TOOLS & names)}",
    ]
    if hidden:
        from src.agent.hands import _BROWSER_TOOLS

        browser = hidden & _BROWSER_TOOLS
        other = hidden - _BROWSER_TOOLS
        if browser:
            # Not a verdict: the bridge is built by the daemon, and this
            # process is not it. Whether Chrome is paired is the
            # dashboard's badge to answer, not the doctor's.
            lines.append(
                f"  браузер        {len(browser)} — будується демоном, не тут; "
                "чи спарено Chrome, каже значок на дашборді:"
            )
            for name in sorted(browser):
                lines.append(f"                   {name}")
        if other:
            lines.append(f"  сховано        {len(other)} — нічим виконати в цьому процесі:")
            for name in sorted(other):
                lines.append(f"                   {name}")
    disabled = {t.name for t in TOOLS if not t.enabled}
    if disabled:
        lines.append(f"  вимкнено       {', '.join(sorted(disabled))}")
    return lines


async def _amain(args: argparse.Namespace) -> int:
    from src.config import load_env, load_settings

    load_env()
    settings = load_settings()

    try:
        plugin_lines, feature_lines = await _survey(settings)
    except Exception as exc:  # noqa: BLE001 - report it, do not raise it
        # The engine did not build; the plugins can still say what they
        # know, which is at least that nothing was wired.
        plugin_lines = _plugin_section()
        feature_lines = [f"  двигун не зібрався: {exc}"]

    out: list[str] = []
    out.append(_rule("ПЛАГІНИ  (src/features/ — відповідають самі за себе)"))
    out += plugin_lines

    out.append(_rule("ФІЧІ  (ще по-старому: що збудувалось, а не що просили)"))
    out += feature_lines

    out.append(_rule("ІНСТРУМЕНТИ"))
    out += _tool_section()

    print("\n".join(out))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="what is actually connected in this process"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
