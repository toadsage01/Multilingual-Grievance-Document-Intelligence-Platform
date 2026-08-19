"""Grievance state machine — single source of truth for legal transitions.

Living in core/ means the rule is enforced everywhere: the Django model
methods in apps/grievances/models.py delegate here, the API view calls
the same code, and the unit tests assert against this map directly.
"""
from core.exceptions import StateTransitionError


# allowed transitions. anything not listed here raises StateTransitionError.
TRANSITIONS: dict[str, set[str]] = {
    "SUBMITTED": {"CLASSIFIED"},
    "CLASSIFIED": {"ROUTED"},
    "ROUTED": {"ANSWERED", "ESCALATED"},
    "ANSWERED": {"RESOLVED"},
    "ESCALATED": {"RESOLVED"},
    "RESOLVED": {"APPEALED"},
    "APPEALED": {"REOPENED"},
    "REOPENED": {"ROUTED"},  # loop back into the routing step
}

# terminal states (no outbound transitions). RESOLVED is only "terminal"
# until a citizen appeals — appeals re-open it.
TERMINAL_STATES: set[str] = set()


def can_transition(from_state: str, to_state: str) -> bool:
    """Predicate form — used by tests and the throttling logic."""
    return to_state in TRANSITIONS.get(from_state, set())


def assert_can_transition(from_state: str, to_state: str, reason: str = "") -> None:
    """Raise if the move is illegal. Use this in command handlers."""
    if not can_transition(from_state, to_state):
        raise StateTransitionError(from_state, to_state, reason)


def auto_route_decision(confidence: float, threshold: float) -> str:
    """Given a confidence score, where should a ROUTED grievance go?

    This is the small piece of business logic that makes the system
    honest about its own uncertainty: low confidence routes to a human
    queue (ESCALATED) rather than silently producing a wrong answer.
    """
    if confidence >= threshold:
        return "ANSWERED"
    return "ESCALATED"
