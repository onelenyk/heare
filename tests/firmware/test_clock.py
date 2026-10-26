import pytest
from heare_panel import clock

from tests.firmware.fakes import FakeClock

PERIOD = 1 << 30
HALF = PERIOD // 2


def test_now_ms_reads_the_injected_ticks_source():
    fake = FakeClock(1234)

    assert clock.now_ms(fake) == 1234


def test_now_ms_follows_the_fake_as_it_advances():
    fake = FakeClock()

    fake.advance(250)

    assert clock.now_ms(fake) == 250


def test_elapsed_is_a_plain_difference_away_from_the_wrap():
    assert clock.elapsed(1000, 1750) == 750
    assert clock.elapsed(1750, 1000) == -750
    assert clock.elapsed(5000, 5000) == 0


def test_elapsed_crosses_the_30_bit_wrap():
    assert clock.elapsed(PERIOD - 10, PERIOD + 5) == 15
    assert clock.elapsed(PERIOD - 10, 5) == 15


def test_elapsed_at_the_half_period_boundary():
    assert clock.elapsed(0, HALF) == -HALF
    assert clock.elapsed(0, HALF - 1) == HALF - 1


def test_elapsed_advances_wrap_safely_around_the_ring():
    start = PERIOD - 5
    now = start

    for step in (1, 10, 100, 1000):
        now = (now + step) % PERIOD
        assert clock.elapsed(start, now) == (now - start) % PERIOD


def test_elapsed_uses_the_ticks_period():
    assert clock.TICKS_PERIOD == PERIOD


@pytest.mark.parametrize("start", [0, 1, PERIOD - 1, PERIOD])
def test_elapsed_is_zero_for_any_start(start):
    assert clock.elapsed(start, start) == 0
