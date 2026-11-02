"""Is the live path well, right now? Reads only: writes nothing to the talk.

The brain answers through its address; the parts run and do not fall; none
waits for a key; and how often the stream broke in the last hours, by the
minute, so that a pattern can be seen. It starts no parts and spends nothing.
"""

from __future__ import annotations

import collections
import datetime
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("live", Path(__file__).with_name("live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
HOURS = 6


def main() -> int:
    bad = 0
    brain = live.remote_brain() or live.BASE
    well = live.brain_answers(brain)
    print(f"{'так' if well else 'НІ '}  мозок {brain}")
    bad += not well
    health = live.json.loads((live.MACHINE / "parts_health.json").read_text(encoding="utf-8"))
    for name in live.wanted_parts():
        part = health.get(name) or {}
        fine = part.get("state") == "running"
        print(f"{'так' if fine else 'НІ '}  {name}: {part.get('state')}, "
              f"перезапусків {part.get('restarts', 0)}")
        bad += not fine
    start = (datetime.datetime.now() - datetime.timedelta(hours=HOURS)).strftime("%Y-%m-%d %H:%M")
    log = live.HOME / "logs" / "app-keeper.log"
    lines = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
             if line[:16] >= start]
    broke = collections.Counter(line[:16] for line in lines if "the stream broke" in line)
    last = next((line for line in reversed(lines) if "allowed on the page" in line
                 or "дозволять ключі" in line), "")
    print(f"     обриви за {HOURS} год: {sum(broke.values())} у {len(broke)} моменти")
    for minute, count in sorted(broke.items())[-10:]:
        print(f"       {minute}  ×{count}")
    if last:
        print(f"     останній запит ключів: {last[:16]} (чи дозволено, видно на пульті)")
    print("усе добре" if not bad else f"проблем: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
