"""What this machine is to heare: which parts run here, and whether the
keys it holds work.

The setup form used to answer this once, by running the installer. But a
part is a folder the keeper reads: turning one on is writing that folder,
turning it off is removing it, and the keeper starts or stops the process
within seconds. Nothing is installed again, so the same answers can be
changed any day — the installer and the settings are one page.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class Part:
    name: str
    label: str
    what: str
    channel: str
    forms: tuple[str, ...]
    about: str
    # The platform it can live on; None for any.
    only: str | None = None


PARTS = {part.name: part for part in (
    Part("voice", "Голос", "слухає й говорить", "voice", ("input", "output"),
         "мікрофон і динамік"),
    Part("eyes", "Очі", "що спереду, чи ти тут, чи щось грає", "eyes", ("input",),
         "очі: застосунок, простій, чи щось грає", only="darwin"),
    Part("sources", "Джерела", "погода й інше: що зараз навколо", "sources", ("input",),
         "джерела: погода — що зараз надворі"),
    Part("hands", "Руки", "агент діє в системі: код, файли, команди", "opencode",
         ("executor",), "агент у терміналі: код, файли, команди, пошук в інтернеті"),
    Part("native", "Native hand", "швидкі дії з тілами: панель, світло, піни",
         "native", ("executor",),
         "швидкі прості дії з тілами heare: панель на столі, світло, піни"),
)}

# Where a key is checked: a list of models answers only to a key that works.
KEY_CHECKS = {
    "DEEPSEEK_API_KEY": "https://api.deepseek.com/v1/models",
    "GROQ_API_KEY": "https://api.groq.com/openai/v1/models",
}
CHECK_TIMEOUT_S = 10.0


def command(name: str, bin_dir: Path | None = None) -> list[str] | None:
    """How to start a part here, or None when it is not on this machine.

    Installed as tools, each part is a script beside the keeper's own; in
    an app every part shares the keeper's Python and is a module of it."""
    bin_dir = bin_dir or Path(sys.argv[0]).absolute().parent
    script = bin_dir / f"heare-{name}"
    if script.exists():
        return [str(script)]
    import importlib.util

    if importlib.util.find_spec(f"heare.{name}") is not None:
        return ["python", "-m", f"heare.{name}"]
    return None


def why_not(name: str, bin_dir: Path | None = None) -> str | None:
    """Why a part cannot run here, in the person's words; None if it can."""
    part = PARTS[name]
    if part.only and sys.platform != part.only:
        return "лише на Mac" if part.only == "darwin" else f"лише на {part.only}"
    if command(name, bin_dir) is None:
        return "не встановлена на цій машині"
    return None


def running(folder: Path) -> set[str]:
    return {name for name in PARTS if (folder / name / "heare.json").exists()}


def turn(folder: Path, name: str, on: bool, bin_dir: Path | None = None) -> None:
    """Write or remove a part's manifest; the keeper does the rest."""
    part = PARTS[name]
    home = folder / name
    if not on:
        shutil.rmtree(home, ignore_errors=True)
        return
    reason = why_not(name, bin_dir)
    if reason:
        raise ValueError(f"{part.label}: {reason}")
    home.mkdir(parents=True, exist_ok=True)
    manifest = {"channel": part.channel, "forms": list(part.forms),
                "about": part.about, "run": command(name, bin_dir)}
    # Written aside and moved in: the keeper reads this folder every few
    # seconds and must never see half a manifest.
    fresh = home / "heare.json.new"
    fresh.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    fresh.replace(home / "heare.json")


def parts(folder: Path, bin_dir: Path | None = None) -> list[dict]:
    """Every part this app knows: on or off here, and why not if it cannot."""
    on = running(folder)
    return [{"name": p.name, "label": p.label, "what": p.what,
             "on": p.name in on, "why_not": why_not(p.name, bin_dir)}
            for p in PARTS.values()]


def check_key(name: str, value: str) -> tuple[bool, str]:
    """Whether a key works, asked of its provider; and what to tell."""
    url = KEY_CHECKS.get(name)
    if url is None:
        return True, "не перевіряю — збережено"
    try:
        answer = httpx.get(url, headers={"Authorization": f"Bearer {value.strip()}"},
                           timeout=CHECK_TIMEOUT_S)
    except httpx.HTTPError:
        return False, "немає зв'язку з провайдером — перевір інтернет"
    if answer.status_code in (401, 403):
        return False, "ключ не підходить"
    if answer.status_code >= 400:
        return False, f"провайдер відповів {answer.status_code}"
    return True, "живий"


def main(argv: list[str] | None = None) -> int:
    """Turn parts on or off from a shell: install.sh does it this way, so
    a manifest it writes is the very one the page writes.

        python -m heare.machine --bin ~/.local/bin on voice hands
    """
    import argparse

    from heare.config import Settings

    parser = argparse.ArgumentParser(prog="python -m heare.machine")
    parser.add_argument("--bin", type=Path, default=None,
                        help="where the parts' scripts are (default: beside this Python)")
    parser.add_argument("switch", choices=["on", "off"])
    parser.add_argument("names", nargs="+", choices=sorted(PARTS))
    args = parser.parse_args(argv)
    # Run as a module, argv[0] is this file: the scripts are beside the
    # interpreter, not beside it.
    bin_dir = args.bin or Path(sys.executable).parent
    folder = Path(Settings().parts_dir)
    failed = 0
    for name in args.names:
        try:
            turn(folder, name, args.switch == "on", bin_dir)
        except (ValueError, OSError) as error:
            print(f"не ввімкнув: {error}", file=sys.stderr)
            failed += 1
            continue
        print(f"{name}: {'увімкнено' if args.switch == 'on' else 'вимкнено'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
