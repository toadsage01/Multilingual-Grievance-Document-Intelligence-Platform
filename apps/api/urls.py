"""API v1 URL routing — one entry point per resource."""
from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.api.views import DepartmentListView, IngestionStatusView
from apps.chat.views import ConversationCreateView, StreamingSSEView
from apps.grievances.views import (
    AppealView, FileGrievanceView, GrievanceDetailView, RouteAfterClassificationView,
)
from apps.ingestion.views import IngestDocumentView
from apps.retrieval.views import SearchView

app_name = "api-v1"

urlpatterns = [
    # tenants
    path("departments/", DepartmentListView.as_view(), name="departments"),

    # search (debug surface)
    path("search/", SearchView.as_view(), name="search"),

    # conversations
    path("conversations/", ConversationCreateView.as_view(), name="conversation-create"),
    path(
        "conversations/<uuid:conversation_id>/messages/stream/",
        StreamingSSEView.as_view(),
        name="conversation-stream",
    ),

    # ingestion
    path("ingestion/documents/", IngestDocumentView.as_view(), name="ingestion-documents"),
    path("ingestion/status/<str:batch_id>/", IngestionStatusView.as_view(),
         name="ingestion-status"),

    # grievances
    path("grievances/", FileGrievanceView.as_view(), name="grievance-file"),
    path("grievances/<uuid:grievance_id>/", GrievanceDetailView.as_view(),
         name="grievance-detail"),
    path("grievances/<uuid:grievance_id>/appeal/", AppealView.as_view(),
         name="grievance-appeal"),
    path("grievances/<uuid:grievance_id>/route/",
         RouteAfterClassificationView.as_view(),
         name="grievance-route"),
]
