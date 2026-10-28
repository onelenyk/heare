"""How long a voice turn takes, from the journal it already keeps.

Manual and read-only: nothing is written, nothing is paid for. Only the
turns that called the model are counted — a verbatim line says nothing
about waiting. A turn is over the line when it takes more than ``--limit``
seconds; the five slowest are shown with their model calls and look-ups,
which is where the time went.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

from heare.config import Settings


@dataclass(frozen=True)
class Turn:
    at: str
    seconds: float
    first: float
    calls: int
    looked: int
    text: str


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def _turns(events_dir: Path, days: int) -> list[Turn]:
    cutoff = time.time() - days * 86400
    turns: list[Turn] = []
    for path in events_dir.glob("*.jsonl"):
        if path.stat().st_mtime < cutoff:
            continue
        with path.open("r", encoding="utf-8", errors="replace") as lines:
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("kind") != "said":
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    continue
                calls = payload.get("calls")
                if not isinstance(calls, int) or calls <= 0:
                    continue
                turns.append(Turn(
                    at=str(row.get("at", "")),
                    seconds=float(payload.get("seconds", 0.0)),
                    first=float(payload.get("first", 0.0)),
                    calls=calls,
                    looked=int(payload.get("looked", 0)),
                    text=str(payload.get("text", "")),
                ))
    return turns


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7,
                        help="how many days back to read, by file mtime")
    parser.add_argument("--limit", type=float, default=4.0,
                        help="seconds a turn may take before it is too slow")
    return parser.parse_args()


def main() -> int:
    args = _args()
    turns = _turns(Path(Settings().events_dir), args.days)
    if not turns:
        print(f"no turns with timing in the last {args.days} days")
        return 0

    seconds = [turn.seconds for turn in turns]
    over = [turn for turn in turns if turn.seconds > args.limit]
    print(f"{len(turns)} turns in the last {args.days} days")
    print(f"p50 {_percentile(seconds, 0.50):.2f}s  "
          f"p90 {_percentile(seconds, 0.90):.2f}s  "
          f"p95 {_percentile(seconds, 0.95):.2f}s")
    print(f"{len(over)} over {args.limit:.1f}s ({len(over) / len(turns):.0%})")
    print("\nslowest:")
    for turn in sorted(turns, key=lambda turn: turn.seconds, reverse=True)[:5]:
        flat = " ".join(turn.text.split())[:60]
        print(f"{turn.at:32} {turn.seconds:5.2f}s  first {turn.first:5.2f}s  "
              f"calls {turn.calls}  looked {turn.looked}  {flat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
