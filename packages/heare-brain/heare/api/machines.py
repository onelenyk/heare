"""The machines the brain knows: what each keeper last said, and what it
should run.

The brain never writes a part's manifest. It says what it wants for a
machine; the keeper on that machine turns the manifest on or off and the
next report says what happened. Desired and actual are two different
things on purpose: the page shows «pending» while they disagree, instead of
pretending the switch already worked.

A machine is a keeper's key (``machine@name``), not a pid of a part: one
machine may run parts of many channels. The file is forgiving, like the
intents — an unknown field is dropped, a missing one takes its default, a
broken file is a brain that has not heard from anyone yet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# A keeper reports this often; a report older than three of these is away.
REPORT_EVERY_S = 30.0
AWAY_AFTER_S = 3 * REPORT_EVERY_S

# A machine cannot make the brain hold a book: a report is a few parts and
# a line of numbers, and anything past these is cut, not stored.
MAX_PARTS = 32
MAX_TEXT = 120
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_PART = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _text(value: object, limit: int = MAX_TEXT) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _load(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    return [number for number in (_number(item) for item in value[:3]) if number is not None]


def _part(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    name = _text(row.get("name"), 40)
    if not _PART.match(name):
        return None
    return {
        "name": name,
        "label": _text(row.get("label")) or name,
        "what": _text(row.get("what")),
        "channel": _text(row.get("channel"), 40),
        "on": bool(row.get("on")),
        "state": _text(row.get("state"), 40),
        "why": _text(row.get("why")),
    }


def _row(raw: object) -> dict | None:
    """One machine as the file has it; forgiving to every field."""
    if not isinstance(raw, dict):
        return None
    pid = _text(raw.get("pid"), 60)
    if not pid.startswith("machine@"):
        return None
    desired = raw.get("desired")
    return {
        "pid": pid,
        "host": _text(raw.get("host"), 60) or pid.split("@", 1)[-1],
        "version": _text(raw.get("version"), 40),
        "platform": _text(raw.get("platform"), 40),
        "arch": _text(raw.get("arch"), 40),
        "load": _load(raw.get("load")),
        "disk_free": _int(raw.get("disk_free")),
        "disk_total": _int(raw.get("disk_total")),
        "seen": _number(raw.get("seen")) or 0.0,
        "alias": _text(raw.get("alias"), 40),
        "mem_free": _int(raw.get("mem_free")),
        "mem_total": _int(raw.get("mem_total")),
        "parts": [part for part in (_part(item) for item in raw.get("parts") or [])
                  if part is not None][:MAX_PARTS],
        "desired": {str(name): bool(on) for name, on in desired.items()
                    if isinstance(name, str) and isinstance(on, bool)}
        if isinstance(desired, dict) else {},
    }


class Machines:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._rows: dict[str, dict] = {}
        # A restart is a one-shot word, not a state: it does not outlive the
        # brain, and it is said once, in the next report.
        self._restart: dict[str, set[str]] = {}
        self._load()

    def _load(self) -> None:
        try:
            rows = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for raw in rows if isinstance(rows, list) else []:
            row = _row(raw)
            if row is not None:
                self._rows[row["pid"]] = row

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(list(self._rows.values()), ensure_ascii=False),
                             encoding="utf-8")
        temporary.replace(self._path)

    # -- what a keeper says ------------------------------------------------

    def report(self, pid: str, data: dict, now: float) -> dict[str, bool]:
        """Take a keeper's word for its machine; answer what it should run."""
        if not pid.startswith("machine@"):
            raise ValueError("a report is a machine's")
        row = self._rows.get(pid) or {"pid": pid, "host": pid.split("@", 1)[-1],
                                      "parts": [], "desired": {}}
        host = _text(data.get("name"), 60)
        if host:
            row["host"] = host
        row.update({
            "version": _text(data.get("version"), 40),
            "platform": _text(data.get("platform"), 40),
            "arch": _text(data.get("arch"), 40),
            "load": _load(data.get("load")),
            "disk_free": _int(data.get("disk_free")),
            "disk_total": _int(data.get("disk_total")),
            "mem_free": _int(data.get("mem_free")),
            "mem_total": _int(data.get("mem_total")),
            "seen": now,
            "parts": [part for part in (_part(item) for item in data.get("parts") or [])
                      if part is not None][:MAX_PARTS],
        })
        self._rows[pid] = row
        self._save()
        again = sorted(self._restart.pop(pid, set()))
        return {"desired": dict(row["desired"]), "restart": again}

    def desire(self, pid: str, part: str, on: bool) -> bool:
        """What the person asked for; False when the name is not a part."""
        if not part or not _PART.match(part):
            return False
        row = self._rows.get(pid)
        if row is None:
            if not pid.startswith("machine@"):
                return False
            row = self._rows[pid] = _empty(pid)
        row["desired"][part] = on
        self._save()
        return True

    def wanted(self, pid: str) -> dict[str, bool]:
        return dict((self._rows.get(pid) or {}).get("desired") or {})

    def restart(self, pid: str, part: str) -> bool:
        """Ask for one part to be started again; the next report carries it."""
        if not part or not _PART.match(part):
            return False
        self._restart.setdefault(pid, set()).add(part)
        return True

    def rename(self, pid: str, alias: str) -> bool:
        """A name for the page; the key stays, so no part is re-keyed."""
        row = self._rows.get(pid)
        if row is None:
            if not pid.startswith("machine@"):
                return False
            row = self._rows[pid] = _empty(pid)
        row["alias"] = _text(alias, 40)
        self._save()
        return True

    def forget(self, pid: str) -> bool:
        """Out of the list: a revoked machine leaves no card behind."""
        if self._rows.pop(pid, None) is None:
            return False
        self._restart.pop(pid, None)
        self._save()
        return True

    # -- what the page shows ------------------------------------------------

    def view(self, known: dict[str, dict], now: float,
             paired: tuple[str, ...] = ()) -> list[dict]:
        """Every machine, from its report, its parts on the wire, and the
        keys it was paired with (a machine may be paired and never report)."""
        machines: dict[str, dict] = {}
        for pid in paired:
            if pid.startswith("machine@"):
                machines[pid] = _blank(pid, pid.split("@", 1)[-1])
        for pid, row in self._rows.items():
            machines[pid] = _from_report(row, now)
        for pid, entry in known.items():
            if pid.startswith("window@"):
                continue
            host = str(entry.get("host") or "")
            key = str(entry.get("machine") or f"machine@{host}") if (entry.get("machine") or host) else ""
            if not key.startswith("machine@"):
                continue
            machine = machines.get(key)
            if machine is None:
                machine = machines[key] = _blank(key, host or key.split("@", 1)[-1])
            channel = str(entry.get("channel") or "")
            part = next((one for one in machine["parts"] if one["channel"] == channel), None)
            if part is None:
                part = {
                    "name": _text(entry.get("name"), 40) or channel,
                    "label": _text(entry.get("name")) or channel,
                    "what": _text(entry.get("about")),
                    "channel": channel, "on": True,
                    "state": str(entry.get("state") or ""), "why": "",
                    "reported": False,
                }
                machine["parts"].append(part)
            part["online"] = True
            part["pids"] = (part.get("pids") or []) + [pid]
            machine["version"] = machine["version"] or _text(entry.get("version"), 40)
            if machine["state"] != "online":
                machine["state"] = "online" if entry.get("state") == "approved" else machine["state"]

        for machine in machines.values():
            wanted = dict(machine.get("desired") or {})
            for part in machine["parts"]:
                part["desired"] = wanted.get(part["name"])
            machine["parts"].sort(key=lambda part: (not part["on"], part["name"]))
            machine.pop("desired", None)
        return sorted(machines.values(), key=lambda machine: machine["name"].lower())


def _empty(pid: str) -> dict:
    """A machine the brain knows of but has not heard from yet."""
    return {
        "pid": pid, "host": pid.split("@", 1)[-1], "alias": "", "version": "",
        "platform": "", "arch": "", "load": [], "disk_free": None,
        "disk_total": None, "mem_free": None, "mem_total": None,
        "seen": 0.0, "parts": [], "desired": {},
    }


def _blank(pid: str, host: str) -> dict:
    return {
        "pid": pid, "name": pid.split("@", 1)[-1], "host": host,
        "state": "away", "seen": 0.0, "version": "", "platform": "", "arch": "",
        "alias": "", "load": [], "disk_free": None, "disk_total": None,
        "mem_free": None, "mem_total": None, "reported": False,
        "parts": [],
    }


def _from_report(row: dict, now: float) -> dict:
    seen = float(row.get("seen") or 0.0)
    state = "online" if seen and now - seen <= AWAY_AFTER_S else "away"
    return {
        "pid": row["pid"],
        "name": row["pid"].split("@", 1)[-1],
        "host": row.get("host") or row["pid"].split("@", 1)[-1],
        "state": state,
        "seen": seen,
        "alias": row.get("alias") or "",
        "version": row.get("version") or "",
        "platform": row.get("platform") or "",
        "arch": row.get("arch") or "",
        "load": list(row.get("load") or []),
        "disk_free": row.get("disk_free"),
        "disk_total": row.get("disk_total"),
        "mem_free": row.get("mem_free"),
        "mem_total": row.get("mem_total"),
        "reported": True,
        "desired": dict(row.get("desired") or {}),
        "parts": [
            {**part, "online": False, "pids": [], "reported": True}
            for part in row.get("parts") or []
        ],
    }
