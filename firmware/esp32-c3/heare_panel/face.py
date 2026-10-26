"""The brand's face on the 72x40 window, moving the way the dashboard's does.

The same face as ``docs/assets/heare-face.svg`` and the page's ``.face``: a
rounded head, two pill eyes, five mouth bars. Each live state moves it as
the page does, so the panel and the page read as one character:

* IDLE   - still, a blink every 5.2 s
* HEAR   - eyes open wider, the head breathes
* THINK  - eyes glance up and aside, the bars rise one after another
* SPEAK  - the bars talk, each at its own pace

Only ``fill_rect``/``hline`` are used, which both ``framebuf`` and the test
recorder have, and only integer arithmetic, so a frame is the same bytes on
the board and on a laptop. ``frame_key`` names the frame a moment shows; the
main loop redraws only when it changes.
"""

IDLE = "IDLE"
HEAR = "HEAR"
THINK = "THINK"
SPEAK = "SPEAK"
STATES = (IDLE, HEAR, THINK, SPEAK)

FRAME_MS = 120

# Geometry for the 72x40 window, scaled from the 120x120 SVG.
HEAD_X, HEAD_Y, HEAD_W, HEAD_H, HEAD_R, STROKE = 16, 1, 40, 38, 12, 2
EYE_W, EYE_H = 5, 9
EYE_Y = 11
EYE_LEFT, EYE_RIGHT = 26, 41
BAR_W = 2
BAR_X = (27, 31, 35, 39, 43)
BAR_MID = 28
BAR_MAX = 7

BLINK_PERIOD = 5200
BLINK_CLOSED = 160
# The page's per-bar talk durations, in ms.
TALK = (500, 380, 620, 440, 560)
THINK_PERIOD = 1000
THINK_STEP = 150
BREATH = 1300


def _tri(t, period):
    """0..1000..0 over ``period``: a sine's shape without floats."""
    phase = t % period
    half = period // 2
    if phase < half:
        return phase * 1000 // half
    return (period - phase) * 1000 // half


def _inset(row, height, radius):
    """How far a rounded rect's edge is pulled in at ``row``."""
    if row < radius:
        d = radius - row
    elif row >= height - radius:
        d = row - (height - radius - 1)
    else:
        return 0
    # The largest x with x*x + d*d <= r*r, from the corner's centre.
    x = radius
    while x > 0 and x * x + d * d > radius * radius:
        x -= 1
    return radius - x


def _round_rect(canvas, x, y, w, h, r, color):
    r = min(r, w // 2, h // 2)
    for row in range(h):
        inset = _inset(row, h, r)
        if w - 2 * inset > 0:
            canvas.hline(x + inset, y + row, w - 2 * inset, color)


def _pill(canvas, x, y, w, h):
    if h <= 2:
        canvas.fill_rect(x, y, w, max(h, 1), 1)
        return
    _round_rect(canvas, x, y, w, h, w // 2, 1)


def frame_key(state, now_ms):
    """Which frame of ``state`` shows at ``now_ms``; same key, same pixels."""
    if state == IDLE:
        phase = now_ms % BLINK_PERIOD
        return (state, phase >= BLINK_PERIOD - BLINK_CLOSED)
    return (state, now_ms // FRAME_MS)


def draw(state, canvas, now_ms=0):
    if state not in STATES:
        raise ValueError(f"no face for {state!r}")
    # Draw the moment the frame key names, so one key is one picture.
    t = now_ms if state == IDLE else now_ms // FRAME_MS * FRAME_MS
    canvas.fill(0)

    grow = 0
    if state == HEAR:
        grow = 1 if _tri(t, BREATH) > 500 else 0
    _round_rect(
        canvas,
        HEAD_X - grow,
        HEAD_Y - (grow if HEAD_Y else 0),
        HEAD_W + 2 * grow,
        HEAD_H + (grow if HEAD_Y else 0) * 2,
        HEAD_R + grow,
        1,
    )
    _round_rect(
        canvas,
        HEAD_X - grow + STROKE,
        HEAD_Y + STROKE,
        HEAD_W + 2 * grow - 2 * STROKE,
        HEAD_H - 2 * STROKE,
        HEAD_R - STROKE,
        0,
    )

    eye_w, eye_h, dx, dy = EYE_W, EYE_H, 0, 0
    if state == IDLE and frame_key(state, t)[1]:
        eye_h = 1
    elif state == HEAR:
        eye_h = EYE_H + 2
    elif state == THINK:
        eye_w, eye_h, dx, dy = 4, 7, 2, -2
    top = EYE_Y + (EYE_H - eye_h) // 2 + dy
    for x in (EYE_LEFT, EYE_RIGHT):
        _pill(canvas, x + dx + (EYE_W - eye_w) // 2, top, eye_w, eye_h)

    for index, x in enumerate(BAR_X):
        height = 2
        if state == SPEAK:
            height = 2 + _tri(t + index * 97, TALK[index]) * (BAR_MAX - 2) // 1000
        elif state == THINK:
            lift = _tri(t - index * THINK_STEP, THINK_PERIOD)
            height = 2 + (BAR_MAX - 4) * lift // 1000
        canvas.fill_rect(x, BAR_MID - height // 2, BAR_W, height, 1)
