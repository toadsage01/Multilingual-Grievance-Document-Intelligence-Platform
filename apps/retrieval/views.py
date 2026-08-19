"""GET /api/v1/search/ — semantic search surface, used for debugging
the retrieval layer without spinning up the chat endpoint."""
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from apps.retrieval.handlers import SearchQuery, aggregate_confidence, search


class SearchRequestSerializer(serializers.Serializer):
    department_id = serializers.UUIDField()
    query = serializers.CharField(allow_blank=False, max_length=2000)
    language_code = serializers.CharField(max_length=10, default="en", required=False)
    top_k = serializers.IntegerField(default=5, min_value=1, max_value=20, required=False)


class SearchHitSerializer(serializers.Serializer):
    chunk_id = serializers.UUIDField()
    document_title = serializers.CharField()
    chunk_text = serializers.CharField()
    score = serializers.FloatField()


class SearchResponseSerializer(serializers.Serializer):
    query = serializers.CharField()
    hits = SearchHitSerializer(many=True)
    confidence = serializers.FloatField()
    language_code = serializers.CharField()


class SearchView(APIView):
    """Anonymous — this is read-only retrieval scoped to a public department."""
    permission_classes = [AllowAny]

    @extend_schema(
        parameters=[SearchRequestSerializer],
        responses={200: SearchResponseSerializer},
        examples=[
            OpenApiExample(
                "Example response",
                value={
                    "query": "how do I apply for a scholarship?",
                    "language_code": "en",
                    "confidence": 0.84,
                    "hits": [
                        {
                            "chunk_id": "00000000-0000-0000-0000-000000000001",
                            "document_title": "Scholarship SOP 2024",
                            "chunk_text": "Apply via the NSP portal...",
                            "score": 0.84,
                        }
                    ],
                },
            )
        ],
    )
    def get(self, request: Request) -> Response:
        ser = SearchRequestSerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        q = SearchQuery(
            department_id=ser.validated_data["department_id"],
            text=ser.validated_data["query"],
            language_code=ser.validated_data.get("language_code", "en"),
            top_k=ser.validated_data.get("top_k", 5),
        )
        hits = search(q)
        return Response(
            {
                "query": q.text,
                "language_code": q.language_code,
                "hits": [
                    {
                        "chunk_id": str(h.chunk_id),
                        "document_title": h.document_title,
                        "chunk_text": h.chunk_text,
                        "score": h.score,
                    }
                    for h in hits
                ],
                "confidence": aggregate_confidence(hits),
            },
            status=status.HTTP_200_OK,
        )
