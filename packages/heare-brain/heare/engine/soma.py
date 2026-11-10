"""The soul: what presses, and how much; the engine decides when to say it.

A want is bound to a thing and keeps pushing long after the event that made
it is gone. Nothing here speaks, knows the store, or reaches the network.

The old seven drives are gone; the soul wants in verbs — ``finish``,
``find out``, ``fix``, ``reach``, ``keep word``, ``rest`` — all one root:
on the person's side. A verb enters with its event, not before: today only
``finish`` has a producer, and the table below holds exactly the rows the
engine feeds (10 November).

A want carries four numbers:
  care     — how much the thing matters (grows with time)
  pressing — how much she needs to say it now (drops to zero when she
             hands the burden over, then regrows toward care)
  said     — how many times she has already brought it up
  carried  — the person took it over, so time stops pressing

Energy is a state, not a verdict: the engine reads it and raises its own
bar to speak; tiredness never forbids speech here. A finished task is
remembered as a trace; a thing that stopped mattering is let go, and the
trace keeps the last fifty names, so the file has a size.

The model never sees numbers from here — only a sentence about what presses.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

VERB = {
    "finish": {"word": "finish", "rise": 0.00005},
    "find_out": {"word": "find out", "rise": 0.00004},
    "fix": {"word": "fix", "rise": 0.00003},
    "reach": {"word": "reach", "rise": 0.00002},
    "keep_word": {"word": "keep your word", "rise": 0.00001},
    "rest": {"word": "rest", "rise": 0.00004},
}

THING_KINDS = {"task", "person", "project", "promise", "place", "self"}

# event kind -> (verb, care_delta, pressing_delta). One row per thing the
# engine actually feeds; a verb with no producer is a word, not a soul. The
# other twelve rows waited without a producer until 10 November and were
# cut: a row is added with its event and its test, one at a time.
APPRAISE = {
    "task_opened": ("finish", 0.30, 0.10),
    "task_done": ("finish", -0.90, 0.00),
    "task_failed": ("finish", 0.30, 0.60),
    # Said out loud, to be done later: the only row fed by conversation.
    "plan_said": ("finish", 0.35, 0.55),
}

DONE_EVENTS = {"task_done"}
SHOCK_EVENTS = {"task_failed"}

PRESS_REGROW_PER_S = 0.0003
# A want already said, or taken over by the person, stops mattering at this
# half-life. Without it care only grows: after a week of remarks the mood
# line said "heavy" forever while nothing could be raised any more.
FADE_HALF_LIFE_S = 3 * 24 * 3600.0
# A dozen open loops is what a person carries; more is a list, not a soul.
# A week of work left 123 wants pressing at 1.0 and the prompt showed the
# three oldest forever (10 November).
HELD_AT_MOST = 12
# Age is not urgency: pressing saturates at 1.0, so a raw sort raises the
# oldest and a full list never lets a new failure in. What is fresh counts.
RELEVANCE_HALF_LIFE_S = 2 * 24 * 3600.0
HABIT = [1.0, 0.35, 0.12, 0.05, 0.02]
# The trace is what the model reads when it says «you have finished …»; a
# thing is kept while its name is still in it.
TRACE_KEPT = 50
ENGAGE_KEEP = 0.5
ENERGY_START = 1.0
SPEAK_COST = 0.30
REST_PER_S = 0.00002
DROP_BELOW = 0.02
IGNORE_BOOST = 0.20
FINISHABLE = {"task", "project", "promise"}

VERSION = 2


@dataclass
class Thing:
    id: str
    kind: str
    label: str
    state: str = "alive"


@dataclass
class Want:
    verb: str
    thing_id: str
    care: float = 0.0
    pressing: float = 0.0
    said: int = 0
    carried: bool = False
    # When it began, on the soul's own clock: the tie-break between wants
    # that press equally hard, so the newest is raised, not the oldest.
    born: float = 0.0


@dataclass
class Soma:
    scale: float = 1.0
    t: float = 0.0
    things: list[Thing] = field(default_factory=list)
    wants: list[Want] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    energy: float = ENERGY_START
    last_spoken: str | None = None

    def thing(self, thing_id: str) -> Thing | None:
        for thing in self.things:
            if thing.id == thing_id:
                return thing
        return None

    def _thing(self, thing_id: str) -> Thing | None:
        return self.thing(thing_id)

    def _ensure_thing(self, thing_id: str, kind: str, label: str) -> Thing:
        thing = self._thing(thing_id)
        if thing is None:
            thing = Thing(id=thing_id, kind=kind, label=label)
            self.things.append(thing)
        else:
            if label:
                thing.label = label
            thing.state = "alive"
        return thing

    def _want(self, verb: str, thing_id: str) -> Want | None:
        for want in self.wants:
            if want.verb == verb and want.thing_id == thing_id:
                return want
        return None

    def _ensure_want(self, verb: str, thing_id: str) -> Want:
        want = self._want(verb, thing_id)
        if want is None:
            want = Want(verb=verb, thing_id=thing_id, born=self.t)
            self.wants.append(want)
        return want

    def feed(self, event: str, *, thing_id: str, kind: str = "task", label: str = "") -> None:
        if event not in APPRAISE:
            return
        verb, care_delta, pressing_delta = APPRAISE[event]
        if event == "task_opened" and self._want(verb, thing_id) is not None:
            return
        self._ensure_thing(thing_id, kind, label)
        want = self._ensure_want(verb, thing_id)
        want.care = _clamp(want.care + care_delta, 0.0, 1.0)
        want.pressing = _clamp(want.pressing + pressing_delta, 0.0, 1.0)
        if event in SHOCK_EVENTS:
            want.said = 0
            want.carried = False
        if event in DONE_EVENTS:
            self._finish(thing_id)

    def tick(self, seconds: float) -> None:
        if seconds <= 0:
            return
        step = seconds * self.scale
        self.t += step
        for want in self.wants:
            if want.carried or want.said:
                # Heard, or in the person's hands: it fades instead of
                # growing. A want nobody has heard yet keeps rising.
                want.care *= 0.5 ** (step / FADE_HALF_LIFE_S)
            else:
                want.care = _clamp(want.care + VERB[want.verb]["rise"] * step * (1.0 - want.care), 0.0, 1.0)
            if want.carried:
                continue
            target = want.care * _habit(want.said)
            if want.pressing < target:
                want.pressing = min(target, want.pressing + PRESS_REGROW_PER_S * step * (target - want.pressing))
        self.energy = min(1.0, self.energy + REST_PER_S * step)
        self.wants = [w for w in self.wants if w.care >= DROP_BELOW]
        if len(self.wants) > HELD_AT_MOST:
            # Everything else is let go: the thing stays as released, with
            # no trace — it was not done, it stopped mattering.
            self.wants.sort(key=self._rank, reverse=True)
            del self.wants[HELD_AT_MOST:]
        for thing in self.things:
            if thing.state == "alive" and not any(w.thing_id == thing.id for w in self.wants):
                thing.state = "released"
        self.trace = self.trace[-TRACE_KEPT:]
        self.things = [
            thing for thing in self.things
            if thing.state == "alive" or thing.label in self.trace
        ]

    def hottest(self) -> Want | None:
        """What presses hardest and freshest; the engine decides whether it is enough."""
        return max(self.wants, key=self._rank, default=None)

    def _rank(self, want: Want) -> tuple[float, float]:
        """What is raised first: pressing, discounted by age, newest on a tie.
        Pressing saturates at 1.0, so without the discount the oldest always
        wins and a full list is frozen (10 November)."""
        age = max(0.0, self.t - want.born)
        fresh = 0.5 ** (age / RELEVANCE_HALF_LIFE_S)
        return (want.pressing * fresh, want.born)

    def spoke(self, thing_id: str, *, spent: bool = True) -> None:
        """It has been heard: pressing falls to zero. Only a remark she
        actually made costs energy — letting one go by hand is not speaking."""
        if spent:
            self.energy = max(0.0, self.energy - SPEAK_COST)
        for want in self.wants:
            if want.thing_id == thing_id:
                want.pressing = 0.0
                want.said += 1
        self.last_spoken = thing_id

    def engage(self, thing_id: str) -> None:
        for want in self.wants:
            if want.thing_id == thing_id:
                want.care *= ENGAGE_KEEP
                want.pressing = 0.0
                want.carried = True

    def ignored(self) -> None:
        if self.last_spoken is None:
            return
        for want in self.wants:
            if want.thing_id == self.last_spoken:
                want.said = 0
                want.carried = False
                want.pressing = _clamp(want.pressing + IGNORE_BOOST, 0.0, 1.0)

    def _finish(self, thing_id: str) -> None:
        thing = self._thing(thing_id)
        if thing is not None and thing.kind in FINISHABLE:
            thing.state = "finished"
            if thing.label and thing.label not in self.trace:
                self.trace.append(thing.label)
        self.wants = [w for w in self.wants if w.thing_id != thing_id]

    def state(self) -> str:
        lines: list[str] = []
        ranked = sorted(self.wants, key=self._rank, reverse=True)[:3]
        for want in ranked:
            word = VERB[want.verb]["word"]
            thing = self._thing(want.thing_id)
            label = thing.label if thing is not None and thing.label else want.thing_id
            if want.carried:
                lines.append(f"«{label}» is in your hands; you rest about it.")
            else:
                lines.append(f"You want to {word} «{label}» — {_heat(want.pressing)}.")
        if self.trace:
            lines.append("You have finished: " + ", ".join(self.trace[-5:]) + ".")
        if not lines:
            lines.append("Nothing presses. It is quiet inside.")
        lines.append("You feel " + _mood(self.wants, bool(self.trace))
                     + " and " + _tense(self.wants) + ".")
        return " ".join(lines)

    def to_dict(self) -> dict:
        return {
            "version": VERSION,
            "scale": self.scale,
            "t": self.t,
            "energy": self.energy,
            "last_spoken": self.last_spoken,
            "things": [asdict(t) for t in self.things],
            "wants": [asdict(w) for w in self.wants],
            "trace": self.trace,
        }

    @classmethod
    def from_dict(cls, raw: object) -> Soma:
        if not isinstance(raw, dict):
            return cls()
        version = raw.get("version")
        if version == VERSION:
            return _from_v2(raw)
        if version in (None, 1):
            return _from_v1(raw)
        return cls()

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> Soma:
        target = Path(path)
        if not target.exists():
            older = target.with_name("body.json")
            if older.exists():
                target = older
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls.from_dict(raw)


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _habit(said: int) -> float:
    return HABIT[said] if said < len(HABIT) else HABIT[-1]


def _number(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _bool(value: object, default: bool = False) -> bool:
    return bool(value) if isinstance(value, bool) else default


def _from_v2(raw: dict) -> Soma:
    soma = Soma(scale=_number(raw.get("scale"), 1.0) or 1.0)
    soma.t = _number(raw.get("t"))
    soma.energy = _number(raw.get("energy"), ENERGY_START)
    soma.last_spoken = raw.get("last_spoken") if isinstance(raw.get("last_spoken"), str) else None
    things = raw.get("things")
    if isinstance(things, list):
        soma.things = [_thing(row) for row in things if isinstance(row, dict)]
    wants = raw.get("wants")
    if isinstance(wants, list):
        soma.wants = [_want(row, soma.t) for row in wants if isinstance(row, dict)]
    trace = raw.get("trace")
    if isinstance(trace, list):
        soma.trace = [t for t in trace if isinstance(t, str)]
    return soma


def _from_v1(raw: dict) -> Soma:
    soma = Soma(scale=_number(raw.get("scale"), 1.0) or 1.0)
    soma.t = _number(raw.get("t"))
    needs = raw.get("needs")
    if isinstance(needs, list):
        for row in needs:
            if not isinstance(row, dict):
                continue
            drive = _text(row.get("drive"))
            if drive not in _DRIVE_TO_VERB:
                continue
            verb = _DRIVE_TO_VERB[drive]
            about = _text(row.get("about"))
            if about == "self":
                continue
            thing_id = about
            soma._ensure_thing(thing_id, "task", _text(row.get("label"), about))
            want = soma._ensure_want(verb, thing_id)
            want.care = _number(row.get("level"))
    return soma


_DRIVE_TO_VERB = {
    "closure": "finish",
    "understanding": "find_out",
    "competence": "fix",
    "connection": "reach",
    "integrity": "keep_word",
    "rest": "rest",
    "attunement": "reach",
}


def _thing(row: dict) -> Thing:
    known = {k: v for k, v in row.items() if k in {"id", "kind", "label", "state"}}
    known["id"] = _text(known.get("id"))
    known["kind"] = _text(known.get("kind"), "task")
    known["label"] = _text(known.get("label"))
    known["state"] = _text(known.get("state"), "alive")
    return Thing(**known)


def _want(row: dict, now: float) -> Want:
    known = {
        k: v for k, v in row.items()
        if k in {"verb", "thing_id", "care", "pressing", "said", "carried", "born"}
    }
    known["verb"] = _text(known.get("verb"))
    known["thing_id"] = _text(known.get("thing_id"))
    known["care"] = _number(known.get("care"))
    known["pressing"] = _number(known.get("pressing"))
    known["said"] = known.get("said") if isinstance(known.get("said"), int) else 0
    known["carried"] = _bool(known.get("carried"))
    # A file written before ``born`` existed starts every want at "now":
    # beginning-of-time would age them all into irrelevance at once.
    known["born"] = _number(known.get("born"), now)
    return Want(**known)


def _heat(urgency: float) -> str:
    if urgency < 0.25:
        return "barely"
    if urgency < 0.5:
        return "a little"
    if urgency < 0.8:
        return "strongly"
    return "badly"


def _presses(want: Want) -> float:
    """What a want still weighs on the mood: only what it can still raise.

    A remark already made, or work the person has taken over, stays in the
    list but no longer presses; counting its raw care is what made the mood
    "heavy" over things she had stopped raising.
    """
    return 0.0 if want.carried else want.care * _habit(want.said)


def _mood(wants: list[Want], has_trace: bool) -> str:
    level = _clamp(sum(_presses(want) for want in wants) / 3.0, 0.0, 1.0)
    valence = _clamp(0.45 - level * 0.9 + (0.1 if has_trace else 0.0), -1.0, 1.0)
    if valence < -0.4:
        return "heavy"
    if valence < -0.15:
        return "uneasy"
    if valence < 0.15:
        return "even"
    if valence < 0.4:
        return "calm"
    return "good"


def _tense(wants: list[Want]) -> str:
    hottest = max((want.pressing for want in wants), default=0.0)
    arousal = _clamp(hottest * 1.25, 0.0, 1.0)
    if arousal > 0.66:
        return "wound up"
    if arousal > 0.4:
        return "alert"
    return "still"
