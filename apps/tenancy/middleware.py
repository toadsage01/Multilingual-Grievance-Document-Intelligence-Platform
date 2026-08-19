"""Tenant context middleware.

Sets the Postgres session variable app.current_tenant before any
request-scoped query runs. RLS policies on each tenant-scoped table
filter rows by this setting — so a buggy ORM query that forgets the
WHERE clause still returns zero cross-tenant rows.

The slug comes in either as a path prefix (/<slug>/api/...) or via the
X-Setu-Tenant header. API consumers usually pass it explicitly on the
POST /conversations/ body; this middleware is the backstop.
"""
from apps.tenancy.models import Department
from core.exceptions import TenantNotSet


_TENANT_HEADER = "HTTP_X_SETU_TENANT"


class TenantContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        slug = self._resolve_slug(request)
        if slug:
            try:
                dept = Department.objects.get(slug=slug)
                request.tenant = dept
                self._set_session_tenant(dept)
            except Department.DoesNotExist:
                request.tenant = None
                self._clear_session_tenant()
        else:
            request.tenant = None
            self._clear_session_tenant()
        return self.get_response(request)

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _resolve_slug(request):
        # explicit header wins
        slug = request.META.get(_TENANT_HEADER)
        if slug:
            return slug.strip().lower()
        # else look for /<slug>/api/...
        path = request.path_info.lstrip("/")
        parts = path.split("/")
        if len(parts) >= 2 and parts[1] == "api":
            return parts[0]
        return None

    @staticmethod
    def _set_session_tenant(department):
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("SET app.current_tenant = %s", [str(department.id)])

    @staticmethod
    def _clear_session_tenant():
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("RESET app.current_tenant")


def get_current_tenant_id():
    """Helper for code paths that aren't behind a request (RQ jobs etc)."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("SHOW app.current_tenant")
        row = cur.fetchone()
    if not row or not row[0] or row[0] == "":
        raise TenantNotSet("no tenant context on the current DB session")
    return row[0]
