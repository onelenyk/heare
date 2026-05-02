"""Adversarial circuit-breaker tests for capability discovery (US-007).

Spec: 2 sig-fails + 1 safety-block + 3 net-fails — verify safety_block events
are NEVER counted toward tripping; the breaker trips on the third net-fail.
"""
from __future__ import annotations

from src.discovery import CircuitBreaker


def test_safety_blocks_never_count_toward_trip():
    breaker = CircuitBreaker(threshold=3)
    for _ in range(10):
        breaker.record_safety_block()
    assert breaker.tripped is False
    assert breaker.safety_blocks == 10
    assert breaker.net_fails == 0


def test_breaker_trips_on_third_net_fail():
    breaker = CircuitBreaker(threshold=3)

    # 2 signature fails (modeled as safety blocks per spec)
    breaker.record_safety_block()
    breaker.record_safety_block()
    assert breaker.tripped is False

    # 1 safety block
    breaker.record_safety_block()
    assert breaker.tripped is False

    # First net fail
    breaker.record_net_fail()
    assert breaker.tripped is False
    assert breaker.net_fails == 1

    # Second net fail
    breaker.record_net_fail()
    assert breaker.tripped is False
    assert breaker.net_fails == 2

    # Third net fail — trips here, NOT earlier despite 3 safety blocks.
    breaker.record_net_fail()
    assert breaker.tripped is True
    assert breaker.net_fails == 3


def test_breaker_does_not_trip_with_fewer_net_fails():
    breaker = CircuitBreaker(threshold=3)
    breaker.record_net_fail()
    breaker.record_net_fail()
    # Inject many safety blocks between net fails to assert independence.
    for _ in range(5):
        breaker.record_safety_block()
    assert breaker.tripped is False


def test_breaker_success_resets_net_fails():
    breaker = CircuitBreaker(threshold=3)
    breaker.record_net_fail()
    breaker.record_net_fail()
    assert breaker.net_fails == 2
    breaker.record_success()
    assert breaker.net_fails == 0
    assert breaker.tripped is False


def test_breaker_reset_clears_state():
    breaker = CircuitBreaker(threshold=3)
    breaker.record_net_fail()
    breaker.record_net_fail()
    breaker.record_net_fail()
    assert breaker.tripped is True
    breaker.reset()
    assert breaker.tripped is False
    assert breaker.net_fails == 0
    assert breaker.safety_blocks == 0


def test_adversarial_full_sequence_2sig_1safety_3net():
    """Exact spec: 2 sig-fails (safety) + 1 safety-block + 3 net-fails."""
    breaker = CircuitBreaker(threshold=3)

    # 2 signature failures — these are safety blocks, not net fails.
    breaker.record_safety_block()
    breaker.record_safety_block()
    # 1 explicit safety block.
    breaker.record_safety_block()

    assert breaker.tripped is False, "safety blocks must not trip"
    assert breaker.safety_blocks == 3
    assert breaker.net_fails == 0

    # Now 3 net fails.
    breaker.record_net_fail()
    assert breaker.tripped is False, "1 net-fail must not trip threshold=3"
    breaker.record_net_fail()
    assert breaker.tripped is False, "2 net-fails must not trip threshold=3"
    breaker.record_net_fail()
    assert breaker.tripped is True, "3rd net-fail must trip"

    # Final state assertions.
    assert breaker.net_fails == 3
    assert breaker.safety_blocks == 3
