import pytest
from heare_panel import face

from tests.firmware.fakes import FakeCanvas

SAMPLES = range(0, 6000, 37)


def _ops(state, now_ms):
    canvas = FakeCanvas()
    face.draw(state, canvas, now_ms)
    return canvas.ops


def _cells(ops):
    """Every (x, y, w, h) a draw op covers; hline is h=1."""
    for op in ops:
        if op[0] == "fill_rect":
            yield op[1], op[2], op[3], op[4]
        elif op[0] == "hline":
            yield op[1], op[2], op[3], 1


@pytest.mark.parametrize("state", face.STATES)
def test_every_frame_stays_inside_the_72x40_window(state):
    for now in SAMPLES:
        for x, y, w, h in _cells(_ops(state, now)):
            assert 0 <= x and x + w <= 72 and 0 <= y and y + h <= 40, (state, now)


@pytest.mark.parametrize("state", face.STATES)
def test_a_frame_key_means_the_same_pixels(state):
    seen = {}
    for now in SAMPLES:
        key = face.frame_key(state, now)
        ops = _ops(state, now)
        assert seen.setdefault(key, ops) == ops, (state, now)


def test_idle_blinks_once_per_period_and_otherwise_holds_still():
    keys = {face.frame_key("IDLE", now) for now in range(0, face.BLINK_PERIOD, 10)}
    assert keys == {("IDLE", False), ("IDLE", True)}


def test_speaking_moves_the_mouth():
    mouths = {
        tuple(op for op in _ops("SPEAK", now) if op[0] == "fill_rect")
        for now in range(0, 1200, face.FRAME_MS)
    }
    assert len(mouths) > 3


def test_an_unknown_state_has_no_face():
    with pytest.raises(ValueError):
        face.draw("WIFI", FakeCanvas())
