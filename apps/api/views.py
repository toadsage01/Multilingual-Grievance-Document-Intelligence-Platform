"""Public + ingestion status endpoints. Thin wrappers — the heavy
lifting lives in the relevant apps."""
import uuid
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiResponse, extend_schema

from apps.tenancy.models import Department
from apps.ingestion.models import Document


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "slug", "guardrail_prompt", "created_at"]


class DepartmentListView(APIView):
    """GET /api/v1/departments/ — public list of tenants."""
    permission_classes = [AllowAny]

    @extend_schema(responses={200: DepartmentSerializer(many=True)})
    def get(self, request: Request) -> Response:
        qs = Department.objects.all().order_by("name")
        return Response(DepartmentSerializer(qs, many=True).data)


class IngestionStatusView(APIView):
    """GET /api/v1/ingestion/status/{batch_id}/

    The batch_id here is just the last 8 chars of the document id; for
    the single-document ingest flow that's the only batch we have.
    Real production would track a batch model — out of scope for v1.
    """
    permission_classes = [AllowAny]

    @extend_schema(responses={200: OpenApiResponse(description="batch status")})
    def get(self, request: Request, batch_id: str) -> Response:
        try:
            doc = Document.objects.get(id__startswith=batch_id)
        except (Document.DoesNotExist, ValueError):
            return Response({"detail": "not found"},
                            status=status.HTTP_404_NOT_FOUND)
        chunk_count = doc.chunks.count()
        return Response({
            "document_id": str(doc.id),
            "title": doc.title,
            "version": doc.version,
            "chunk_count": chunk_count,
            "superseded_by": str(doc.superseded_by_id) if doc.superseded_by_id else None,
            "ingested_at": doc.ingested_at.isoformat(),
        })
