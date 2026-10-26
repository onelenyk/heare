"""Which hands each phrase reaches, and how fast — against the live heare.

    make eval-route

Leaves the machine: the live brain, its real model, the real board. Each
phrase is typed as the person would; the task it becomes is read back with
its hands and its time. The work is real — the phrases only show, light,
read, and look at the date.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

from heare.config import Settings

PHRASES = [
    ("виведи на олед екран hello", "native-hands"),
    ("покажи на платі привіт", "native-hands"),
    ("блимни світлом на платі", "native-hands"),
    ("вимкни світло на платі", "native-hands"),
    ("через термінал подивись, яка зараз дата", "opencode"),
]
WAIT_S = 180


def main() -> int:
    settings = Settings()
    token = Path(settings.api_token_file).read_text(encoding="utf-8").strip()
    base = settings.base

    def call(path: str, body: dict | None = None):
        request = urllib.request.Request(
            base + path, headers={"X-Heare-Token": token, "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None)
        return json.load(urllib.request.urlopen(request))

    misses, times = 0, []
    for phrase, wanted in PHRASES:
        before = {t["id"] for t in call("/api/tasks")}
        call("/api/message", {"text": phrase})
        start, found = time.time(), None
        while time.time() - start < WAIT_S:
            new = [t for t in call("/api/tasks") if t["id"] not in before]
            if new and new[-1]["status"] != "running":
                found = new[-1]
                break
            time.sleep(0.5)
        if found is None:
            print(f"✗ {phrase!r}: no task finished")
            misses += 1
            continue
        took = found["ended"] - found["started"]
        reached = found["executor"]
        ok = reached == wanted and found["status"] == "done"
        misses += not ok
        if wanted == "native-hands":
            times.append(took)
        print(f"{'✓' if ok else '✗'} {took:5.1f} s  {reached:12} {phrase!r} → {found['result'][:70]}")
        time.sleep(2)
    if times:
        print(f"тіла: медіана {statistics.median(times):.1f} s")
    print(f"{len(PHRASES) - misses}/{len(PHRASES)}")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
