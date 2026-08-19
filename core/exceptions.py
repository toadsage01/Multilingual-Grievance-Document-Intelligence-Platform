"""Custom exceptions shared across the codebase.

The point is to let core/ raise its own types instead of leaking Django
or DRF exceptions upward. The api/ layer is responsible for translating
these into HTTP responses.
"""


class SetuError(Exception):
    """Base class — never raised directly."""


class StateTransitionError(SetuError):
    """Raised when a grievance state machine move is illegal."""

    def __init__(self, from_state: str, to_state: str, reason: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        super().__init__(
            f"illegal transition {from_state} -> {to_state}"
            + (f": {reason}" if reason else "")
        )


class ProviderUnavailable(SetuError):
    """All LLM providers are down or in cooldown."""


class CircuitOpen(SetuError):
    """Circuit breaker is currently open; the call was short-circuited."""


class TenantNotSet(SetuError):
    """No tenant context was attached to this request."""


class IngestionChecksumMismatch(SetuError):
    """Content changed during re-ingestion — not really an error, used internally."""
