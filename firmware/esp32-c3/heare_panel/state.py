"""What the panel shows, reduced from the brain's status events.

The brain emits ``phase`` only when it changes, and ``permission`` /
``question`` beside it; a display therefore keeps what it was told and asks
this module what that adds up to *now*. Time is passed in (``time.ticks_ms``
on the board, a fake clock in tests) so error deadlines expire on their own
between events, and every comparison is wrap-safe because the board's clock
counts in a 30-bit ring.

The link is an explicit input, never inferred from events: ``boot`` and
``wifi`` while the device is coming up, ``brain`` while it is knocking,
``pair`` when the brain refused the token, ``online`` once the stream is
open, and ``offline`` after an online stream dropped. A link state replaces
the phase while there is no live stream, but a permission or an error still
outranks it — those are the things a person must not miss.

Text is deliberately absent. The reducer keeps an id, a kind and a deadline,
never ``text``/``what``/``why``; the panel has no room and no right to them.
"""

BOOT = "BOOT"
WIFI = "WIFI"
PAIR = "PAIR"
BRAIN = "BRAIN"
IDLE = "IDLE"
HEAR = "HEAR"
THINK = "THINK"
SPEAK = "SPEAK"
ALLOW = "ALLOW"
ERROR = "ERROR"
OFFLINE = "OFFLINE"

STATES = (
    BOOT,
    WIFI,
    PAIR,
    BRAIN,
    IDLE,
    HEAR,
    THINK,
    SPEAK,
    ALLOW,
    ERROR,
    OFFLINE,
)

# The four words the brain's ``phase`` event may carry.
_PHASES = {
    "idle": IDLE,
    "hearing": HEAR,
    "thinking": THINK,
    "speaking": SPEAK,
}

# What a link that is not live shows. ``offline`` before the panel was ever
# online is still "looking for the brain", not a dropped stream.
_LINK_STATES = {
    "boot": BOOT,
    "wifi": WIFI,
    "brain": BRAIN,
    "pair": PAIR,
    "offline": BRAIN,
}

LINKS = ("boot", "wifi", "brain", "pair", "online", "offline")

# MicroPython's ``time.ticks_ms()`` counts in a 30-bit ring.
TICKS_PERIOD = 1 << 30
_HALF_PERIOD = TICKS_PERIOD // 2

SAID_ERROR_MS = 5_000
FAILED_ERROR_MS = 10_000
CANNOT_ERROR_MS = 5_000


_FIELDS = (
    "state",
    "phase",
    "allow_id",
    "allow_kind",
    "error_until",
    "link",
    "was_online",
    "phase_at",
)


class Display:
    """Everything needed to decide the one word on the screen.

    The board has no data-class helper, so this is a plain class with slots,
    an explicit copy and value equality — the same shape the tests had before.
    """

    __slots__ = _FIELDS

    def __init__(
        self,
        state: str,
        phase: str = IDLE,
        allow_id: int | None = None,
        allow_kind: str | None = None,
        error_until: int | None = None,
        link: str = "online",
        was_online: bool = False,
        phase_at: float = 0.0,
    ) -> None:
        self.state = state
        self.phase = phase
        self.allow_id = allow_id
        self.allow_kind = allow_kind
        self.error_until = error_until
        self.link = link
        self.was_online = was_online
        self.phase_at = phase_at

    def copy(self, **changes: object) -> "Display":
        values = {name: getattr(self, name) for name in _FIELDS}
        values.update(changes)
        try:
            return Display(**values)
        except TypeError as error:
            raise TypeError(f"unknown field for Display: {error}") from error

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Display):
            return NotImplemented
        for name in _FIELDS:
            if getattr(self, name) != getattr(other, name):
                return False
        return True

    def __hash__(self) -> int:
        return hash(tuple(getattr(self, name) for name in _FIELDS))

    def __repr__(self) -> str:
        parts = [f"{name}={getattr(self, name)!r}" for name in _FIELDS]
        return "Display(" + ", ".join(parts) + ")"


def initial(link: str = "wifi") -> Display:
    """The panel before a single event: the link decides what it shows."""
    if link not in LINKS:
        link = "wifi"
    was_online = link == "online"
    return Display(
        state=_shown(IDLE, None, None, link, was_online, 0),
        link=link,
        was_online=was_online,
    )


def reduce(
    prev: Display, event: dict[str, object], now_ms: int, link: str | None = None
) -> Display:
    """Fold one event into the display, resolving the winner for ``now_ms``.

    ``link`` replaces the stored link when given; ``None`` keeps it. Unknown
    event kinds change nothing but still let a deadline expire, so the main
    loop can call ``reduce(state, {}, now)`` as a tick.
    """
    current_link = prev.link if link is None or link not in LINKS else link
    was_online = prev.was_online or current_link == "online"

    phase = prev.phase
    phase_at = prev.phase_at
    allow_id = prev.allow_id
    allow_kind = prev.allow_kind
    error_until = prev.error_until

    kind = event.get("kind")
    if kind == "phase":
        label = event.get("phase")
        mapped = _PHASES.get(label) if isinstance(label, str) else None
        if mapped is not None:
            at = _event_at(event)
            if at is None or at >= prev.phase_at:
                phase = mapped
                if at is not None:
                    phase_at = at
    elif kind in ("permission", "question"):
        allow_id = _event_id(event)
        allow_kind = kind
    elif kind == "permission_closed" or kind == "question_closed":
        opened = "permission" if kind == "permission_closed" else "question"
        if allow_kind == opened and _event_id(event) == allow_id:
            allow_id = None
            allow_kind = None
    elif kind == "said":
        if event.get("failed") is not None:
            error_until = now_ms + SAID_ERROR_MS
    elif kind == "failed":
        error_until = now_ms + FAILED_ERROR_MS
    elif kind == "cannot":
        error_until = now_ms + CANNOT_ERROR_MS

    return prev.copy(
        state=_shown(phase, allow_kind, error_until, current_link, was_online, now_ms),
        phase=phase,
        phase_at=phase_at,
        allow_id=allow_id,
        allow_kind=allow_kind,
        error_until=error_until,
        link=current_link,
        was_online=was_online,
    )


def priority(display: Display, now_ms: int) -> str:
    """The state the display should show at ``now_ms``.

    The order is OFFLINE > ALLOW > ERROR > SPEAK > THINK > HEAR > IDLE, with
    the boot/link states taking the place of the phase while no stream is
    live. OFFLINE needs a link that was online once; PAIR and BRAIN are not
    called offline merely because the brain has not answered yet.
    """
    return _shown(
        display.phase,
        display.allow_kind,
        display.error_until,
        display.link,
        display.was_online,
        now_ms,
    )


def _shown(
    phase: str,
    allow_kind: str | None,
    error_until: int | None,
    link: str,
    was_online: bool,
    now_ms: int,
) -> str:
    if link == "offline" and was_online:
        return OFFLINE
    if allow_kind is not None:
        return ALLOW
    if error_until is not None and _ticks_diff(error_until, now_ms) > 0:
        return ERROR
    if link != "online":
        return _LINK_STATES.get(link, BRAIN)
    return phase


def _ticks_diff(a: int, b: int) -> int:
    """Signed ``a - b`` across the 30-bit ring, as ``time.ticks_diff`` does."""
    return (a - b + _HALF_PERIOD) % TICKS_PERIOD - _HALF_PERIOD


def _event_at(event: dict[str, object]) -> float | None:
    at = event.get("at")
    if isinstance(at, (int, float)) and not isinstance(at, bool):
        return float(at)
    return None


def _event_id(event: dict[str, object]) -> int | None:
    ident = event.get("id")
    if isinstance(ident, int) and not isinstance(ident, bool):
        return ident
    return None
