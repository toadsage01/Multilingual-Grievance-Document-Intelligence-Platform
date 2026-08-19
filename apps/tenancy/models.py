"""Department = tenant. One row per ministry / institution.

The guardrail_prompt field is what makes this multi-tenant at the LLM
layer too — each department can ship its own system prompt that scopes
the answer to its jurisdiction, rather than answering as a generic bot.
"""
import uuid
from django.db import models
from django.utils.text import slugify


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    guardrail_prompt = models.TextField(
        blank=True,
        help_text="Department-specific system prompt / restrictions "
        "(e.g. 'do not advise on legal matters outside education policy').",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "departments"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
