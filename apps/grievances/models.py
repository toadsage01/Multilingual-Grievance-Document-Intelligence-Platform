"""Grievance lifecycle.

The grievances table holds current state. grievance_status_history is
append-only — every transition lands here. The BEFORE UPDATE trigger
(fns_grievance_status_touch) just refreshes updated_at; the actual
"reject writes that skip history" enforcement lives in the Python
domain layer (core.domain.state_machine), which the model delegates to.

Why not enforce in SQL? A trigger that introspects whether a sibling
history row was inserted in the same txn is doable but fragile and
hard to test. The Python contract is clearer and unit-testable.
"""
import uuid
from django.db import models
from apps.tenancy.models import Department
from apps.chat.models import Conversation


STATUS_CHOICES = [
    ("SUBMITTED", "SUBMITTED"),
    ("CLASSIFIED", "CLASSIFIED"),
    ("ROUTED", "ROUTED"),
    ("ANSWERED", "ANSWERED"),
    ("ESCALATED", "ESCALATED"),
    ("RESOLVED", "RESOLVED"),
    ("APPEALED", "APPEALED"),
    ("REOPENED", "REOPENED"),
]


class Grievance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="grievances"
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="grievances",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="SUBMITTED")
    category = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grievances"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.id} {self.status}"


class GrievanceStatusHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grievance = models.ForeignKey(
        Grievance, on_delete=models.CASCADE, related_name="history"
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="grievance_history",
        null=True,
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    confidence_score = models.FloatField(null=True, blank=True)
    note = models.TextField(blank=True)
    actor = models.CharField(max_length=50, default="system")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "grievance_status_history"
        ordering = ["created_at"]
