"""What happened today — the report you read after living with it.

Everything the assistant does that matters is already written down: the
conversations, what it decided to say unbidden and whether it got to say
it, what each turn cost and how long it took. What was missing is a way
to read it without writing SQL at the end of a long day, which in
practice means it was never read.

Read-only, always. The database is opened `mode=ro` through a URI, not
by convention but so that a mistake in this file cannot cost a day of
conversations — this is the one script whose whole job is to be run
against live data.

    make day            # today
    make day DAYS=3     # the last three days
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Overridable so the tripwire in tests/ can point this at a temp
# database instead of the live one. Nothing else may.
HOME = Path(os.environ.get("HEARE_HOME") or Path.home() / ".heare")
DB = HOME / "heare.db"
TURNS = HOME / "logs" / "turns.jsonl"


def _open() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _since(days: float) -> float:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (start - timedelta(days=days - 1)).timestamp()


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")


def _rows(db: sqlite3.Connection, sql: str, *args) -> list:
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def talking(db: sqlite3.Connection, since: float) -> None:
    said, heard = _rows(
        db,
        "SELECT SUM(agent_spoken), SUM(1 - COALESCE(agent_spoken, 0)) "
        "FROM transcripts WHERE ts >= ?",
        since,
    )[0] or (0, 0)
    closed = _rows(
        db,
        "SELECT COUNT(*), SUM(summary IS NOT NULL AND TRIM(summary) != '') "
        "FROM conversations WHERE end_ts >= ?",
        since,
    )[0]
    print("\n РОЗМОВА")
    print(f"   {heard or 0} реплік твоїх, {said or 0} його")
    print(f"   {closed[0]} розмов закрито, {closed[1] or 0} з них лишили підсумок")


def unbidden(db: sqlite3.Connection, since: float) -> None:
    """The half that has never lived a day. This is the section to read.

    A dropped intent is not a bug — most of what reaches the veto should
    be refused. What would be a bug is *only* dropped ones, which is
    what the last measured state of this gate was: it said no to
    twenty-four probes out of twenty-four.
    """
    print("\n НЕЗАПИТАНЕ")
    intents = _rows(
        db,
        "SELECT kind, text, state, outcome, created_ts, voiced_ts FROM intents "
        "WHERE created_ts >= ? ORDER BY created_ts",
        since,
    )
    if not intents:
        print("   нічого не назбиралось — ні наміру, ні спроби")
        return
    spoke = [i for i in intents if i[2] == "voiced"]
    refused = [i for i in intents if i[3] == "не варте"]
    print(f"   {len(intents)} намірів; сказано {len(spoke)}, "
          f"відхилено моделлю {len(refused)}")
    if len(intents) and not spoke:
        print("   ⚠  жодного разу не заговорило — перевір вето "
              "(docs/live-tests.md, «Вето, яке ніколи не казало так»)")
    for kind, text, state, outcome, created, voiced in intents:
        mark = {"voiced": "🔊", "dropped": "· "}.get(state, "  ")
        when = _clock(voiced or created)
        why = f"  ({outcome})" if outcome else ""
        print(f"   {mark} {when}  [{kind}] {text[:70]}{why}")

    seen = _rows(
        db,
        "SELECT ts, text, said_ts, dismissed FROM observations WHERE ts >= ? "
        "ORDER BY ts",
        since,
    )
    for ts, text, said_ts, dismissed in seen:
        state = "відшито" if dismissed else ("сказано" if said_ts else "лежить")
        print(f"   ◦  {_clock(ts)}  повтор: {text[:60]} — {state}")


def cost(db: sqlite3.Connection, since: float) -> None:
    """Cost per day, which nobody has ever measured on this machine."""
    print("\n ЦІНА")
    rows = _rows(
        db,
        "SELECT kind, COUNT(*), SUM(COALESCE(cost_usd, 0)) FROM usage_events "
        "WHERE ts >= ? GROUP BY kind ORDER BY 3 DESC",
        since,
    )
    if not rows:
        print("   нічого не пораховано")
        return
    for kind, count, spent in rows:
        print(f"   {kind:6} {count:5} викликів   ${spent or 0:.4f}")
    print(f"   {'разом':6} {sum(r[1] for r in rows):5}            "
          f"${sum(r[2] or 0 for r in rows):.4f}")


def speed(since: float) -> None:
    """Latency, and how much of what it heard was never speech.

    Both come free from turns.jsonl and neither has ever been read.
    """
    print("\n ШВИДКІСТЬ")
    if not TURNS.exists():
        print("   turns.jsonl немає — телеметрія вимкнена")
        return
    turns = []
    for line in TURNS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("ts", 0) >= since:
            turns.append(row)
    if not turns:
        print("   жодного ходу за цей період")
        return

    def spread(field: str) -> str:
        got = sorted(t.get(field, 0) for t in turns if t.get(field))
        if not got:
            return "—"
        half = got[len(got) // 2]
        worst = got[int(len(got) * 0.9)] if len(got) > 4 else got[-1]
        return f"середина {half} мс, найгірші 10% від {worst} мс"

    junk = sum(1 for t in turns if t.get("dropped_junk"))
    cut = sum(1 for t in turns if t.get("interrupted"))
    print(f"   {len(turns)} ходів")
    print(f"   почути:  {spread('stt_ms')}")
    print(f"   думати:  {spread('think_ms')}")
    print(f"   до звуку: {spread('total_ms')}")
    print(f"   {junk} викинуто як не-мову, {cut} перебито")


def main() -> int:
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    if not DB.exists():
        print(f"немає {DB}")
        return 1
    since = _since(days)
    print(f"── heare, {'сьогодні' if days == 1 else f'{days:g} дні'} "
          f"(від {_clock(since)}) ──")
    with _open() as db:
        talking(db, since)
        unbidden(db, since)
        cost(db, since)
    speed(since)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
