"""The body behind the intents: seven needs that grow with time.

The engine decides *when* something may be said. This decides *what* and
*how much*: a need that rises by itself, is bound to a thing, and keeps
pushing long after the event that made it is gone. Nothing here speaks; a
need becomes an intent only when it has grown enough and been held long
enough.

The model never sees numbers from here — only a sentence about what presses.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

WEIGHT: dict[str, float] = {
    "closure": 1.4,
    "understanding": 1.1,
    "competence": 1.3,
    "connection": 0.9,
    "attunement": 0.9,
    "integrity": 1.6,
    "rest": 0.7,
}

RISE: dict[str, float] = {
    "closure": 0.00010,
    "understanding": 0.00008,
    "competence": 0.00006,
    "connection": 0.00004,
    "attunement": 0.00003,
    "integrity": 0.00001,
    "rest": 0.00004,
}

ACTION: dict[str, str] = {
    "closure": "finish",
    "understanding": "find out",
    "competence": "check",
    "connection": "say",
    "attunement": "fit in",
    "integrity": "refuse",
    "rest": "fall quiet",
}

PHRASE: dict[str, str] = {
    "closure": "finish",
    "understanding": "understand",
    "competence": "prove it works",
    "connection": "say a word to someone",
    "attunement": "fit in with",
    "integrity": "not go against yourself",
    "rest": "go quiet and rest",
}

# kind → (drive, delta, bound to the thing rather than to the self)
APPRAISAL: dict[str, tuple[tuple[str, float, bool], ...]] = {
    "task_opened": (("closure", 0.20, True),),
    "task_done": (("closure", -0.85, True), ("competence", -0.15, False)),
    "task_failed": (("competence", 0.50, True), ("closure", 0.25, True)),
    "correction": (("competence", 0.35, True), ("understanding", 0.20, True)),
    "new_fact": (("understanding", 0.12, True),),
    "contradiction": (("understanding", 0.55, True),),
    "question_asked": (("understanding", 0.30, True),),
    "interrupted": (("attunement", 0.60, True),),
    "permission_denied": (("integrity", 0.25, False), ("competence", 0.20, False)),
    "silence_long": (("connection", 0.25, False),),
    "spoke": (("connection", -0.60, False), ("rest", 0.12, False)),
    "worked": (("rest", 0.05, False),),
    "rested": (("rest", -0.45, False),),
    "hungry": (("rest", 0.20, False),),
    "burnout": (("rest", 0.50, False), ("competence", 0.20, False)),
}

# A dominant need muffles its neighbours. This is what makes the disposition.
SUPPRESS: dict[str, dict[str, float]] = {
    "closure": {"understanding": 0.35, "connection": 0.25, "rest": 0.20},
    "integrity": {"connection": 0.55, "rest": 0.45, "understanding": 0.30},
    "competence": {"connection": 0.30, "rest": 0.15},
    "rest": {"connection": 0.50, "understanding": 0.30},
    "attunement": {"connection": 0.25},
}

ACTIVATION = 0.35
# A thing started a minute ago is not a thing left undone. It has to sit
# for a while before it may become an intention.
MIN_AGE = 1800.0
HYSTERESIS = 1.15
MIN_DWELL = 300.0
SHOCK_WINDOW = 900.0
COMMIT_TAU = 2000.0
ACTIVE_MARGIN = 1.2
CAPACITY = 12
ABANDON_LOW = 0.05
ABANDON_AFTER = 4500.0
CALM_WINDOW = 60.0

LIFE: dict[str, float] = {
    "closure": 86400.0,
    "understanding": 43200.0,
    "competence": 43200.0,
    "connection": 21600.0,
    "attunement": 7200.0,
    "integrity": 21600.0,
    "rest": 7200.0,
}


@dataclass
class Need:
    """One thing missed, bound to one thing: a drive and what it is about."""

    id: int
    drive: str
    about: str
    label: str
    level: float = 0.0
    urgency: float = 0.0
    effective: float = 0.0
    made: float = 0.0
    touched: float = 0.0
    shock_at: float | None = None
    calm_at: float | None = None


@dataclass
class Intention:
    """A need held long enough to become something to act on."""

    id: int
    need_id: int
    drive: str
    about: str
    action: str
    commitment: float = 0.25
    made: float = 0.0
    last_active: float = 0.0
    until: float | None = None
    status: str = "held"
    attempts: int = 0
    low_since: float = 0.0


VERSION = 1

_NEED_FIELDS = {f.name for f in fields(Need)}
_INTENTION_FIELDS = {f.name for f in fields(Intention)}


def _number(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


@dataclass
class Body:
    """Seven needs, the intention that holds them, and how it all feels.

    ``scale`` folds time: 1 is human time, where a task takes an hour to
    start pressing; 100 is the fast profile used by tests.
    """

    scale: float = 1.0
    t: float = 0.0
    needs: list[Need] = field(default_factory=list)
    intentions: list[Intention] = field(default_factory=list)
    active: int | None = None
    dominant: int | None = None
    dominant_since: float = 0.0
    valence: float = 0.0
    arousal: float = 0.0
    _next_need: int = 1
    _next_intention: int = 1
    _prev_sum: float = 0.0

    def __post_init__(self) -> None:
        self.needs.append(Need(id=1, drive="connection", about="self", label="", level=0.05))
        self._next_need = 2

    def feed(self, kind: str, about: str = "self", label: str = "") -> list[Need]:
        """Take one event in. Returns the needs it touched."""
        rules = APPRAISAL.get(kind)
        if not rules:
            return []
        touched: list[Need] = []
        for drive, delta, bound in rules:
            target = about if bound else "self"
            need = self._need_for(drive, target)
            if need is not None and kind == "task_opened":
                # The same task opening again is not twice as unfinished.
                touched.append(need)
                continue
            if need is None:
                need = Need(
                    id=self._next_need,
                    drive=drive,
                    about=target,
                    label=label if bound else "",
                    made=self.t,
                    touched=self.t,
                )
                self._next_need += 1
                self.needs.append(need)
            need.level = _clamp(need.level + delta, 0.0, 1.0)
            need.touched = self.t
            if delta >= 0.5:
                need.shock_at = self.t
            if delta < 0:
                need.calm_at = self.t
            touched.append(need)
        self._prune()
        for need in touched:
            if need.level > 0.02:
                continue
            for intention in self.intentions:
                if intention.need_id == need.id and intention.status in ("held", "active"):
                    intention.status = "done"
        return touched

    def tick(self, seconds: float) -> None:
        """Let time pass. The only thing that makes a need grow without help."""
        if seconds <= 0:
            return
        step = seconds * self.scale
        self.t += step
        self._prune()
        self._rise(step)
        self._settle()
        self._hold(step)
        self._feel(step)

    def propose(self) -> Intention | None:
        """The one thing worth acting on now, or nothing."""
        for intention in self.intentions:
            if intention.id == self.active and intention.status == "active":
                return intention
        return None

    def perform(self, need_id: int) -> Need | None:
        """Act on a need. The first action eases much, the tenth almost nothing."""
        need = self._need(need_id)
        if need is None:
            return None
        gain = 0.55 * (0.25 + 0.75 * need.level)
        need.level = _clamp(need.level - gain, 0.0, 1.0)
        need.touched = self.t
        need.calm_at = self.t
        for intention in self.intentions:
            if intention.need_id == need.id and intention.status in ("held", "active"):
                intention.status = "done"
        return need

    def state(self) -> str:
        """What presses, in words a model can read. No numbers, no drive names."""
        lines: list[str] = []
        # Read fresh: how hard something presses is a question of this moment,
        # not of when the clock last moved.
        ranked = sorted(self.needs, key=lambda n: n.level * WEIGHT[n.drive], reverse=True)
        for need in ranked[:3]:
            pressure = need.level * WEIGHT[need.drive]
            if pressure <= 0.0:
                continue
            phrase = PHRASE.get(need.drive, need.drive)
            thing = (" «" + need.label + "»") if need.label else ""
            lines.append("You want to " + phrase + thing + " — " + _heat(pressure) + ".")
        if not lines:
            lines.append("Nothing presses. It is quiet inside.")
        lines.append("You feel " + _mood(self.valence) + " and " + _tense(self.arousal) + ".")
        held = self.propose()
        if held is not None:
            lines.append("You have not let go of: " + held.action + " «" + held.about + "».")
        return " ".join(lines)

    def to_dict(self) -> dict:
        """The whole body as plain data, so a restart is a waking up."""
        return {
            "version": VERSION,
            "scale": self.scale,
            "t": self.t,
            "valence": self.valence,
            "arousal": self.arousal,
            "dominant": self.dominant,
            "dominant_since": self.dominant_since,
            "active": self.active,
            "next_need": self._next_need,
            "next_intention": self._next_intention,
            "prev_sum": self._prev_sum,
            "needs": [asdict(n) for n in self.needs],
            "intentions": [asdict(i) for i in self.intentions],
        }

    @classmethod
    def from_dict(cls, raw: object) -> Body:
        """Forgiving on purpose: unknown fields are dropped, missing take defaults."""
        if not isinstance(raw, dict) or raw.get("version") != VERSION:
            return cls()
        body = cls(scale=_number(raw.get("scale"), 1.0) or 1.0)
        body.t = _number(raw.get("t"))
        body.valence = _number(raw.get("valence"))
        body.arousal = _number(raw.get("arousal"))
        body.dominant_since = _number(raw.get("dominant_since"))
        body._prev_sum = _number(raw.get("prev_sum"))
        body.dominant = raw.get("dominant") if isinstance(raw.get("dominant"), int) else None
        body.active = raw.get("active") if isinstance(raw.get("active"), int) else None
        body._next_need = raw.get("next_need") if isinstance(raw.get("next_need"), int) else 1
        body._next_intention = (
            raw.get("next_intention") if isinstance(raw.get("next_intention"), int) else 1
        )
        needs = raw.get("needs")
        if isinstance(needs, list):
            body.needs = [_need(row) for row in needs if isinstance(row, dict)]
        intentions = raw.get("intentions")
        if isinstance(intentions, list):
            body.intentions = [_intention(row) for row in intentions if isinstance(row, dict)]
        return body

    def save(self, path: Path) -> None:
        """Write the body where it can be found again; never leave half a file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> Body:
        """A body from disk, or a new one when there is nothing to wake up."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls.from_dict(raw)

    def _prune(self) -> None:
        """A need that was met and has stayed at nothing is done with."""
        self.needs = [
            n for n in self.needs
            if not (n.level <= 0.002 and n.calm_at is not None and self.t - n.calm_at > CALM_WINDOW)
        ]

    def _need(self, need_id: int) -> Need | None:
        for need in self.needs:
            if need.id == need_id:
                return need
        return None

    def _need_for(self, drive: str, about: str) -> Need | None:
        for need in self.needs:
            if need.drive == drive and need.about == about:
                return need
        return None

    def _rise(self, step: float) -> None:
        for need in self.needs:
            need.level = _clamp(need.level + RISE[need.drive] * step * (1.0 - need.level), 0.0, 1.0)

    def _settle(self) -> None:
        best: Need | None = None
        top = 0.0
        for need in self.needs:
            need.urgency = need.level * WEIGHT[need.drive]
            if need.urgency > top:
                top = need.urgency
                best = need

        if self.dominant is not None:
            current = self._need(self.dominant)
            if current is not None and current.urgency > 0.05 and best is not None:
                dwell = self.t - self.dominant_since
                if best.id != current.id and (dwell < MIN_DWELL or top < current.urgency * HYSTERESIS):
                    best = current

        if best is not None and best.id != self.dominant:
            self.dominant = best.id
            self.dominant_since = self.t
        elif best is None:
            self.dominant = None

        dominant = self._need(self.dominant) if self.dominant is not None else None
        cut = SUPPRESS.get(dominant.drive, {}) if dominant is not None else {}
        for need in self.needs:
            share = cut.get(need.drive, 0.0) * (min(1.0, dominant.urgency) if dominant else 0.0)
            need.effective = need.urgency * (1.0 - share)

    def _hold(self, step: float) -> None:
        for need in self.needs:
            if need.level < ACTIVATION or self._open(need):
                continue
            if self.t - need.made < MIN_AGE:
                continue
            self.intentions.append(
                Intention(
                    id=self._next_intention,
                    need_id=need.id,
                    drive=need.drive,
                    about=need.label or need.about,
                    action=ACTION[need.drive],
                    made=self.t,
                    last_active=self.t,
                    until=self.t + LIFE[need.drive],
                )
            )
            self._next_intention += 1

        if self.active is not None:
            current = self._intention(self.active)
            if current is not None and current.status == "active":
                current.commitment += (1.0 - current.commitment) * min(1.0, step / COMMIT_TAU)

        for intention in self.intentions:
            if intention.status in ("done", "dropped"):
                continue
            need = self._need(intention.need_id)
            if need is None:
                intention.status = "dropped"
                continue
            if intention.until is not None and self.t > intention.until:
                intention.status = "dropped"
                continue
            if need.level < ABANDON_LOW:
                if intention.low_since == 0.0:
                    intention.low_since = self.t
                elif self.t - intention.low_since > ABANDON_AFTER:
                    intention.status = "dropped"
            else:
                intention.low_since = 0.0

        best: Intention | None = None
        score = -1.0
        for intention in self.intentions:
            if intention.status not in ("held", "active"):
                continue
            value = self._score(intention)
            if value > score:
                score = value
                best = intention

        if self.active is not None:
            current = self._intention(self.active)
            if current is not None and current.status in ("active", "held"):
                value = self._score(current)
                if value * ACTIVE_MARGIN > score:
                    best = current
                    score = value

        for intention in self.intentions:
            if intention.status == "active" and (best is None or intention.id != best.id):
                intention.status = "held"

        if best is not None:
            if best.status != "active":
                best.status = "active"
                best.attempts += 1
                best.last_active = self.t
            self.active = best.id
        else:
            self.active = None

        open_ones = [i for i in self.intentions if i.status in ("held", "active")]
        if len(open_ones) > CAPACITY:
            open_ones.sort(key=lambda i: i.commitment)
            for intention in open_ones[: len(open_ones) - CAPACITY]:
                intention.status = "dropped"

    def _score(self, intention: Intention) -> float:
        need = self._need(intention.need_id)
        if need is None:
            return -1.0
        shock = 2.5 if need.shock_at is not None and self.t - need.shock_at < SHOCK_WINDOW else 1.0
        return need.urgency * (0.55 + 0.45 * intention.commitment) * shock

    def _open(self, need: Need) -> bool:
        for intention in self.intentions:
            if intention.need_id == need.id and intention.status in ("held", "active"):
                return True
        return False

    def _intention(self, intention_id: int | None) -> Intention | None:
        for intention in self.intentions:
            if intention.id == intention_id:
                return intention
        return None

    def _feel(self, step: float) -> None:
        total = sum(n.urgency for n in self.needs)
        rate = (total - self._prev_sum) / step if step > 1e-6 else 0.0
        self._prev_sum = total
        level = _clamp(total / 2.5, 0.0, 1.0)
        target = _clamp((0.45 - level * 0.9) - _clamp(rate * 3.0, -0.5, 0.5), -1.0, 1.0)
        self.valence += (target - self.valence) * min(1.0, step / 1.5)
        hottest = max((n.urgency for n in self.needs), default=0.0)
        self.arousal += (_clamp(hottest * 1.25, 0.0, 1.0) - self.arousal) * min(1.0, step / 1.0)


def _need(row: dict) -> Need:
    known = {k: v for k, v in row.items() if k in _NEED_FIELDS}
    known["drive"] = _text(known.get("drive"))
    known["about"] = _text(known.get("about"))
    known["label"] = _text(known.get("label"))
    known["id"] = known.get("id") if isinstance(known.get("id"), int) else 0
    for name in ("level", "urgency", "effective", "made", "touched"):
        known[name] = _number(known.get(name))
    for name in ("shock_at", "calm_at"):
        known[name] = _number(known.get(name)) if isinstance(known.get(name), (int, float)) else None
    return Need(**known)


def _intention(row: dict) -> Intention:
    known = {k: v for k, v in row.items() if k in _INTENTION_FIELDS}
    known["id"] = known.get("id") if isinstance(known.get("id"), int) else 0
    known["need_id"] = known.get("need_id") if isinstance(known.get("need_id"), int) else 0
    for name in ("drive", "about", "action", "status"):
        known[name] = _text(known.get(name))
    for name in ("commitment", "made", "last_active", "low_since"):
        known[name] = _number(known.get(name))
    known["until"] = _number(known.get("until")) if isinstance(known.get("until"), (int, float)) else None
    known["attempts"] = known.get("attempts") if isinstance(known.get("attempts"), int) else 0
    return Intention(**known)


def _heat(urgency: float) -> str:
    if urgency < 0.25:
        return "barely"
    if urgency < 0.5:
        return "a little"
    if urgency < 0.8:
        return "strongly"
    return "badly"


def _mood(valence: float) -> str:
    if valence < -0.4:
        return "heavy"
    if valence < -0.15:
        return "uneasy"
    if valence < 0.15:
        return "even"
    if valence < 0.4:
        return "calm"
    return "good"


def _tense(arousal: float) -> str:
    if arousal > 0.66:
        return "wound up"
    if arousal > 0.4:
        return "alert"
    return "still"
