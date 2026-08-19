"""Root URL config. API v1 lives under /api/v1/, schema at /api/schema/."""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.api.urls")),
    # OpenAPI schema + swagger UI
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    # django-rq dashboard is mounted under /django-rq/ in dev only;
    # prod env gates it through staff auth via django-rq's default
    path("django-rq/", include("django_rq.urls")),
]
