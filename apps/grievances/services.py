"""Grievance command handlers — the "C" in CQRS.

Every transition is one DB transaction:
  - update grievances.current_state
  - insert a grievance_status_history row

If either fails, neither happens — the compensating-action guarantee
the spec asks for.
"""
from __future__ import annotations
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.chat.models import Conversation
from apps.grievances.models import Grievance, GrievanceStatusHistory
from apps.tenancy.models import Department
from core.domain.state_machine import (
    assert_can_transition, auto_route_decision,
)
from core.exceptions import StateTransitionError, ProviderUnavailable

log = logging.getLogger(__name__)


@dataclass
class TransitionResult:
    grievance_id: uuid.UUID
    from_status: str
    to_status: str
    note: str = ""


def file_grievance(
    *,
    department_id: uuid.UUID,
    conversation_id: Optional[uuid.UUID] = None,
    category: str = "",
) -> Grievance:
    """Initial SUBMITTED row. Conversation link is optional but recommended."""
    with transaction.atomic():
        g = Grievance.objects.create(
            department_id=department_id,
            conversation_id=conversation_id,
            status="SUBMITTED",
            category=category,
        )
        GrievanceStatusHistory.objects.create(
            grievance_id=g.id,
            department_id=department_id,
            from_status="",
            to_status="SUBMITTED",
            note="filed by citizen",
            actor="citizen",
        )
    log.info("filed grievance %s dept=%s", g.id, department_id)
    return g


def transition(
    grievance_id: uuid.UUID,
    to_status: str,
    *,
    confidence_score: Optional[float] = None,
    note: str = "",
    actor: str = "system",
) -> TransitionResult:
    """Move a grievance from its current status to a new one.

    Raises StateTransitionError if the move is illegal.
    """
    with transaction.atomic():
        g = Grievance.objects.select_for_update().get(id=grievance_id)
        from_status = g.status
        assert_can_transition(from_status, to_status, reason=note)
        g.status = to_status
        g.save(update_fields=["status", "updated_at"])
        GrievanceStatusHistory.objects.create(
            grievance_id=g.id,
            department_id=g.department_id,
            from_status=from_status,
            to_status=to_status,
            confidence_score=confidence_score,
            note=note,
            actor=actor,
        )
    log.info("grievance %s: %s -> %s (actor=%s)", grievance_id, from_status, to_status, actor)
    return TransitionResult(
        grievance_id=grievance_id,
        from_status=from_status,
        to_status=to_status,
        note=note,
    )


def route_after_classification(
    grievance_id: uuid.UUID, confidence: float, threshold: float
) -> TransitionResult:
    """The demo of graceful failure-mode handling.

    A high-confidence classification routes to ANSWERED — the citizen
    gets the auto-generated RAG response. A low-confidence one routes
    to ESCALATED, where a human officer picks it up. The transition
    itself is recorded with the confidence so an analyst can see why
    the system escalated.
    """
    # first step: CLASSIFIED -> ROUTED (records the classification)
    transition(grievance_id, "CLASSIFIED",
               confidence_score=confidence,
               note="auto-classified", actor="system")
    # second step: ROUTED -> {ANSWERED, ESCALATED} based on threshold
    target = auto_route_decision(confidence, threshold)
    transition(grievance_id, target,
               confidence_score=confidence,
               note=f"threshold={threshold}, routed via {'high' if target=='ANSWERED' else 'low'}-confidence path",
               actor="system")
    return TransitionResult(
        grievance_id=grievance_id,
        from_status="ROUTED",
        to_status=target,
    )


def appeal(grievance_id: uuid.UUID) -> TransitionResult:
    """RESOLVED -> APPEALED. Citizen-initiated."""
    return transition(grievance_id, "APPEALED",
                     note="citizen appealed resolution", actor="citizen")


def reopen(grievance_id: uuid.UUID, note: str = "") -> TransitionResult:
    """APPEALED -> REOPENED. Officer action."""
    return transition(grievance_id, "REOPENED",
                     note=note or "appeal accepted, reopened",
                     actor="officer")


def resolve(grievance_id: uuid.UUID, note: str = "") -> TransitionResult:
    """ESCALATED or ANSWERED -> RESOLVED."""
    return transition(grievance_id, "RESOLVED",
                     note=note or "resolved", actor="officer")
