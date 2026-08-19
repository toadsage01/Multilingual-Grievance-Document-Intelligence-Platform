"""Seed departments so a fresh deploy can stand up a working demo.

Usage:
    python manage.py seed_departments
"""
from django.core.management.base import BaseCommand
from apps.tenancy.models import Department


_DEPARTMENTS = [
    {
        "name": "Education Ministry",
        "slug": "edu",
        "guardrail_prompt": (
            "You answer only for the Ministry of Education. "
            "If a query is about railway tickets or medical schemes, decline "
            "and direct the citizen to the appropriate department."
        ),
    },
    {
        "name": "Railways",
        "slug": "rwy",
        "guardrail_prompt": (
            "You answer only for the Ministry of Railways. "
            "Decline queries outside railway matters — fares, refunds, "
            "reservations, accidents, freight — and suggest the relevant department."
        ),
    },
    {
        "name": "Health and Family Welfare",
        "slug": "health",
        "guardrail_prompt": (
            "You answer only for the Ministry of Health and Family Welfare. "
            "For non-health queries, refer the citizen to the appropriate ministry."
        ),
    },
]


class Command(BaseCommand):
    help = "Create the default departments with their guardrail prompts"

    def handle(self, *args, **opts):
        created = 0
        updated = 0
        for row in _DEPARTMENTS:
            dept, was_created = Department.objects.update_or_create(
                slug=row["slug"],
                defaults={"name": row["name"], "guardrail_prompt": row["guardrail_prompt"]},
            )
            if was_created:
                created += 1
            else:
                updated += 1
            self.stdout.write(f"  {dept.slug}: {'created' if was_created else 'updated'}")
        self.stdout.write(
            self.style.SUCCESS(f"done: {created} created, {updated} updated")
        )
