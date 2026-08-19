"""Pytest config. Picks up the dev settings module + lets us flip to sqlite
for fast unit tests that don't need pgvector."""
import os
import django
import pytest


def pytest_configure(config):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    os.environ.setdefault("DJANGO_DEBUG", "True")
    # point the cache at fakeredis so throttle tests don't pollute real redis
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
    django.setup()


@pytest.fixture
def tenant_a(db):
    from apps.tenancy.models import Department
    return Department.objects.create(name="Education Ministry", slug="edu")


@pytest.fixture
def tenant_b(db):
    from apps.tenancy.models import Department
    return Department.objects.create(name="Railways", slug="rwy")


@pytest.fixture
def superuser(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create_superuser("admin", "admin@example.com", "admin")
