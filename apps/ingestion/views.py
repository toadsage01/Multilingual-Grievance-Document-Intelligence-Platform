"""DRF serializers + view for the ingestion endpoint.

Admin-only — keeps public traffic from uploading arbitrary text into
the vector store. Authentication is via session for now; swap in
JWT when this goes behind a real front-end.
"""
from __future__ import annotations
import uuid
from rest_framework import serializers, status
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiResponse, extend_schema

from apps.ingestion.services import ingest_text


class IngestDocumentSerializer(serializers.Serializer):
    department_id = serializers.UUIDField()
    title = serializers.CharField(max_length=500)
    raw_text = serializers.CharField(allow_blank=False)
    source_url = serializers.URLField(required=False, allow_blank=True)
    language_hint = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        # strict mode: reject unknown keys
        unknown = set(self.initial_data) - set(self.fields.keys())
        if unknown:
            raise serializers.ValidationError({k: "unexpected field" for k in unknown})
        return attrs


class IngestDocumentView(APIView):
    """POST /api/v1/ingestion/documents/

    Accepts a single document for ingestion. For batch uploads the
    caller should iterate / use the management command.
    """
    permission_classes = [IsAdminUser]
    serializer_class = IngestDocumentSerializer

    @extend_schema(
        request=IngestDocumentSerializer,
        responses={201: OpenApiResponse(description="document ingested")},
    )
    def post(self, request: Request) -> Response:
        ser = self.serializer_class(data=request.data)
        ser.is_valid(raise_exception=True)
        result = ingest_text(
            department_id=ser.validated_data["department_id"],
            title=ser.validated_data["title"],
            raw_text=ser.validated_data["raw_text"],
            source_url=ser.validated_data.get("source_url", ""),
            language_hint=ser.validated_data.get("language_hint"),
        )
        return Response(
            {
                "document_id": result.document_id,
                "new": result.new,
                "chunk_count": result.chunk_count,
                "superseded_id": result.superseded_id,
            },
            status=status.HTTP_201_CREATED,
        )
