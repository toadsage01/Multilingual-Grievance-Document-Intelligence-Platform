"""Grievance API: file, fetch status+history, appeal."""
import uuid
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiResponse, extend_schema

from apps.grievances.models import Grievance, GrievanceStatusHistory
from apps.grievances.services import (
    appeal, file_grievance, reopen, resolve, route_after_classification,
)


class FileGrievanceSerializer(serializers.Serializer):
    department_id = serializers.UUIDField()
    conversation_id = serializers.UUIDField(required=False)
    category = serializers.CharField(max_length=255, required=False, allow_blank=True)


class StatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = GrievanceStatusHistory
        fields = ["id", "from_status", "to_status", "confidence_score",
                  "note", "actor", "created_at"]


class GrievanceResponseSerializer(serializers.ModelSerializer):
    history = StatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Grievance
        fields = ["id", "department_id", "conversation_id", "status",
                  "category", "created_at", "updated_at", "history"]


class FileGrievanceView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=FileGrievanceSerializer,
        responses={201: GrievanceResponseSerializer},
    )
    def post(self, request: Request) -> Response:
        ser = FileGrievanceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        g = file_grievance(
            department_id=ser.validated_data["department_id"],
            conversation_id=ser.validated_data.get("conversation_id"),
            category=ser.validated_data.get("category", ""),
        )
        return Response(
            GrievanceResponseSerializer(g).data,
            status=status.HTTP_201_CREATED,
        )


class GrievanceDetailView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: GrievanceResponseSerializer})
    def get(self, request: Request, grievance_id: uuid.UUID) -> Response:
        try:
            g = Grievance.objects.prefetch_related("history").get(id=grievance_id)
        except Grievance.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(GrievanceResponseSerializer(g).data)


class AppealView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        responses={200: GrievanceResponseSerializer},
        description="Move a RESOLVED grievance into APPEALED.",
    )
    def post(self, request: Request, grievance_id: uuid.UUID) -> Response:
        try:
            appeal(grievance_id)
        except Grievance.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        g = Grievance.objects.prefetch_related("history").get(id=grievance_id)
        return Response(GrievanceResponseSerializer(g).data)


class RouteAfterClassificationView(APIView):
    """Internal/operational endpoint used by the classifier job.

    Marks a grievance CLASSIFIED, then routes it to ANSWERED or
    ESCALATED based on the confidence score.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        request={
            "type": "object",
            "properties": {
                "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
                "threshold": {"type": "number", "default": 0.72},
            },
            "required": ["confidence_score"],
        },
        responses={200: GrievanceResponseSerializer},
    )
    def post(self, request: Request, grievance_id: uuid.UUID) -> Response:
        score = request.data.get("confidence_score")
        threshold = float(request.data.get("threshold", 0.72))
        if score is None:
            return Response({"detail": "confidence_score required"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            route_after_classification(grievance_id, float(score), threshold)
        except Grievance.DoesNotExist:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        g = Grievance.objects.prefetch_related("history").get(id=grievance_id)
        return Response(GrievanceResponseSerializer(g).data)
