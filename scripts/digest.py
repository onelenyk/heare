"""What irritated the person this week, from the journal it already keeps.

G7 asks for a list of annoyances, but the application has nowhere to type
one in. The journal, meanwhile, has been recording them all along: halted
work, cut replies, failed turns, unusable verbs, unanswered questions,
facts the person rejected, reminders they cancelled. This report only reads
that and counts it.

Manual and read-only: nothing is written, nothing is paid for. Files older
than ``--days`` are skipped by mtime, malformed lines are skipped silently,
and the output is plain text so it can be piped.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

from heare.config import Settings


@dataclass(frozen=True)
class Signal:
    at: str
    day: str
    kind: str
    detail: str


def _signal(row: dict, kind: str, payload: dict) -> Signal | None:
    at = str(row.get("at", ""))
    day = at[:10]
    if kind == "halted":
        return Signal(at, day, "halted", f"{payload.get('tasks', 0)} task(s)")
    if kind == "voice_stopped":
        return Signal(at, day, "voice_stopped", "reply cut")
    if kind == "failed":
        return Signal(at, day, "failed", str(payload.get("channel") or "turn"))
    if kind == "cannot":
        why = str(payload.get("why", ""))
        what = payload.get("what")
        detail = why if what is None else f"{why}: {what}"
        return Signal(at, day, "cannot", detail)
    if kind == "question_closed":
        return Signal(at, day, "question_closed", "no answer")
    if kind == "feature":
        return Signal(at, day, "feature", str(payload.get("feature", "")) + " failed")
    if kind == "task":
        status = payload.get("status")
        if status not in {"failed", "stopped"}:
            return None
        request = str(payload.get("request", ""))[:40]
        detail = str(payload.get("executor") or request)
        return Signal(at, day, f"task {status}", detail)
    if kind == "mind":
        what = str(payload.get("what", ""))
        if what == "fact" and payload.get("action") == "rejected":
            return Signal(at, day, "fact rejected", f"#{payload.get('id')}")
        if what == "reminder" and payload.get("action") == "cancelled":
            return Signal(at, day, "reminder cancelled", f"#{payload.get('id')}")
        return None
    return None


def _signals(events_dir: Path, days: int) -> list[Signal]:
    cutoff = time.time() - days * 86400
    signals: list[Signal] = []
    for path in events_dir.glob("*.jsonl"):
        if path.stat().st_mtime < cutoff:
            continue
        with path.open("r", encoding="utf-8", errors="replace") as lines:
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    continue
                signal = _signal(row, str(row.get("kind", "")), payload)
                if signal is not None:
                    signals.append(signal)
    return signals


def _counts(signals: list[Signal]) -> list[tuple[str, int]]:
    counted: dict[str, int] = {}
    for signal in signals:
        counted[signal.kind] = counted.get(signal.kind, 0) + 1
    return sorted(counted.items(), key=lambda item: (-item[1], item[0]))


def _per_day(signals: list[Signal]) -> list[tuple[str, int]]:
    counted: dict[str, int] = {}
    for signal in signals:
        counted[signal.day] = counted.get(signal.day, 0) + 1
    return sorted(counted.items())


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7,
                        help="how many days back to read, by file mtime")
    parser.add_argument("--top", type=int, default=10,
                        help="how many candidates to show")
    return parser.parse_args()


def main() -> int:
    args = _args()
    signals = _signals(Path(Settings().events_dir), args.days)
    if not signals:
        print(f"nothing to look at in the last {args.days} days")
        return 0

    print(f"{args.days} days: {len(signals)} signal(s)")
    width = max(len(signal.kind) for signal in signals)
    for kind, count in _counts(signals):
        print(f"  {kind:<{width}}  {count}")

    days = _per_day(signals)
    line = "  |  ".join(f"{day[8:10]}.{day[5:7]}  {count}" for day, count in days)
    print(f"per day:  {line}")

    print("candidates:")
    newest = sorted(signals, key=lambda signal: signal.at, reverse=True)
    for signal in newest[:args.top]:
        print(f"{signal.at[:16]}  {signal.kind:<16} {signal.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
