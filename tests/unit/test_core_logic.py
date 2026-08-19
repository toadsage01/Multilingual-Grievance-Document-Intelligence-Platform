"""Unit tests for the pure-logic core layer — no DB, no network, no Django.

These run in microseconds and are the regression net for the state
machine, circuit breaker, and checksum delta logic.
"""
import pytest

from core.domain.state_machine import (
    TRANSITIONS,
    assert_can_transition,
    auto_route_decision,
    can_transition,
)
from core.exceptions import CircuitOpen, ProviderUnavailable, StateTransitionError
from apps.llm.breaker import CircuitBreaker, FallbackChain
from apps.ingestion.checksum import ChecksumDelta, checksum


# -- state machine --------------------------------------------------------
class TestStateMachine:
    def test_legal_transitions(self):
        assert can_transition("SUBMITTED", "CLASSIFIED")
        assert can_transition("CLASSIFIED", "ROUTED")
        assert can_transition("ROUTED", "ANSWERED")
        assert can_transition("ROUTED", "ESCALATED")
        assert can_transition("RESOLVED", "APPEALED")
        assert can_transition("APPEALED", "REOPENED")
        assert can_transition("REOPENED", "ROUTED")

    def test_illegal_transitions(self):
        assert not can_transition("SUBMITTED", "RESOLVED")
        assert not can_transition("ANSWERED", "ROUTED")
        assert not can_transition("ROUTED", "RESOLVED")
        assert not can_transition("ESCALATED", "ANSWERED")  # must resolve first

    def test_assert_can_transition_raises(self):
        with pytest.raises(StateTransitionError) as exc:
            assert_can_transition("SUBMITTED", "RESOLVED")
        assert "illegal" in str(exc.value)
        assert exc.value.from_state == "SUBMITTED"
        assert exc.value.to_state == "RESOLVED"

    def test_assert_can_transition_passes(self):
        assert_can_transition("ROUTED", "ESCALATED")  # no exception

    def test_loop_back_into_routed(self):
        # the full lifecycle cycle works
        path = ["SUBMITTED", "CLASSIFIED", "ROUTED", "ESCALATED",
                "RESOLVED", "APPEALED", "REOPENED", "ROUTED"]
        for a, b in zip(path, path[1:]):
            assert can_transition(a, b), f"{a}->{b}"

    def test_auto_route_decision_high_confidence(self):
        assert auto_route_decision(0.95, 0.72) == "ANSWERED"

    def test_auto_route_decision_low_confidence(self):
        assert auto_route_decision(0.40, 0.72) == "ESCALATED"

    def test_auto_route_decision_boundary(self):
        assert auto_route_decision(0.72, 0.72) == "ANSWERED"  # >=


# -- checksum delta ------------------------------------------------------
class TestChecksumDelta:
    def test_checksum_stable(self):
        assert checksum("hello") == checksum("hello")

    def test_checksum_changes_on_text_change(self):
        assert checksum("hello") != checksum("hello ")

    def test_skip_on_match(self):
        cs = checksum("foo")
        delta = ChecksumDelta(existing=[("d1", "url1", cs)])
        assert delta.decide("url1", cs) == "SKIP"

    def test_insert_on_checksum_change(self):
        cs = checksum("foo")
        delta = ChecksumDelta(existing=[("d1", "url1", cs)])
        assert delta.decide("url1", "different") == "INSERT"

    def test_insert_on_new_url(self):
        cs = checksum("foo")
        delta = ChecksumDelta(existing=[("d1", "url1", cs)])
        assert delta.decide("url2", cs) == "INSERT"


# -- circuit breaker -----------------------------------------------------
class _FakeProvider:
    """Test double for LLMProvider."""
    def __init__(self, name: str, *, fail: bool = False, tokens: list = None):
        self._name = name
        self._fail = fail
        self._tokens = tokens or ["a", "b", "c"]

    @property
    def name(self):
        return self._name

    async def stream_completion(self, system_prompt, user_prompt, context_chunks):
        if self._fail:
            raise RuntimeError(f"{self._name} boom")
        for t in self._tokens:
            yield t

    def health(self):
        return not self._fail


class TestCircuitBreaker:
    def test_starts_closed(self):
        br = CircuitBreaker(_FakeProvider("p"), failure_threshold=3, cooldown_seconds=10)
        assert br.state == "CLOSED"
        assert br.allow_call() is True

    def test_opens_after_threshold(self):
        br = CircuitBreaker(_FakeProvider("p", fail=True),
                            failure_threshold=3, cooldown_seconds=10)
        for _ in range(3):
            br.record_failure()
        assert br.state == "OPEN"
        assert br.allow_call() is False

    def test_half_open_after_cooldown(self):
        br = CircuitBreaker(_FakeProvider("p", fail=True),
                            failure_threshold=2, cooldown_seconds=0)
        for _ in range(2):
            br.record_failure()
        # cooldown=0 -> immediately HALF_OPEN
        assert br.state == "HALF_OPEN"

    def test_success_resets(self):
        br = CircuitBreaker(_FakeProvider("p", fail=True),
                            failure_threshold=3, cooldown_seconds=10)
        for _ in range(2):
            br.record_failure()
        assert br.state == "CLOSED"
        br.record_success()
        assert br.state == "CLOSED"
        assert br._state.failure_count == 0


class TestFallbackChain:
    def test_primary_succeeds(self):
        import asyncio
        chain = FallbackChain(
            CircuitBreaker(_FakeProvider("p", tokens=["ok"]), failure_threshold=3),
            CircuitBreaker(_FakeProvider("f", tokens=["fallback"]), failure_threshold=3),
        )
        result = asyncio.run(chain.stream_completion("s", "u", []))
        assert result == ["ok"]

    def test_falls_back_on_primary_failure(self):
        import asyncio
        chain = FallbackChain(
            CircuitBreaker(_FakeProvider("p", fail=True), failure_threshold=3),
            CircuitBreaker(_FakeProvider("f", tokens=["fallback"]), failure_threshold=3),
        )
        result = asyncio.run(chain.stream_completion("s", "u", []))
        assert result == ["fallback"]

    def test_both_fail_raises_provider_unavailable(self):
        import asyncio
        chain = FallbackChain(
            CircuitBreaker(_FakeProvider("p", fail=True), failure_threshold=3),
            CircuitBreaker(_FakeProvider("f", fail=True), failure_threshold=3),
        )
        with pytest.raises(ProviderUnavailable):
            asyncio.run(chain.stream_completion("s", "u", []))

    def test_open_circuit_skips_provider(self):
        import asyncio
        # primary opens after N failures — chain should skip and try fallback
        primary = CircuitBreaker(_FakeProvider("p", fail=True),
                                  failure_threshold=1, cooldown_seconds=60)
        primary.record_failure()  # circuit OPEN
        chain = FallbackChain(
            primary,
            CircuitBreaker(_FakeProvider("f", tokens=["fallback"]), failure_threshold=3),
        )
        result = asyncio.run(chain.stream_completion("s", "u", []))
        assert result == ["fallback"]
