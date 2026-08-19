"""Conversation + message models.

Conversation keeps a citizen_ref — a pseudonymous session identifier,
not real PII. We log language_code on every message so per-language
answer quality can be measured downstream (this is the difference
between a defensible "94% intent accuracy" claim and a made-up one).
"""
import uuid
from django.db import models
from django.contrib.postgres.fields import ArrayField
from apps.tenancy.models import Department


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="conversations"
    )
    citizen_ref = models.CharField(max_length=255, blank=True)
    language_code = models.CharField(max_length=10, default="en")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conversations"
        ordering = ["-created_at"]


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="messages",
        null=True,  # denormalized for RLS
    )
    role = models.CharField(max_length=20)  # user/assistant/system
    content = models.TextField()
    cited_chunk_ids = ArrayField(
        models.UUIDField(), default=list, blank=True
    )
    confidence_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "messages"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(role__in=["user", "assistant", "system"]),
                name="msg_role_check",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.role}] {self.content[:60]}"
