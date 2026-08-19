"""Re-export the domain types at the package boundary for easy imports."""
from core.domain.entities import (
    Chunk,
    GenerationResult,
    Grievance,
    RetrievedChunk,
    StatusTransition,
)
from core.domain.state_machine import (
    TRANSITIONS,
    auto_route_decision,
    assert_can_transition,
    can_transition,
)

__all__ = [
    "Chunk",
    "GenerationResult",
    "Grievance",
    "RetrievedChunk",
    "StatusTransition",
    "TRANSITIONS",
    "auto_route_decision",
    "assert_can_transition",
    "can_transition",
]
