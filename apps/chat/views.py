"""SSE streaming endpoint + conversation management.

POST /api/v1/conversations/                -- start
POST /api/v1/conversations/{id}/messages/stream/  -- SSE turn
"""
import json
import uuid
from django.http import StreamingHttpResponse
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiResponse, extend_schema

from apps.chat.models import Conversation
from apps.chat.services import start_conversation, stream_turn_sync
from apps.tenancy.models import Department


class ConversationCreateSerializer(serializers.Serializer):
    department_id = serializers.UUIDField()
    language_code = serializers.CharField(max_length=10, default="en")
    citizen_ref = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields.keys())
        if unknown:
            raise serializers.ValidationError({k: "unexpected field" for k in unknown})
        return attrs


class ConversationResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    department_id = serializers.UUIDField()
    language_code = serializers.CharField()


class ConversationCreateView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=ConversationCreateSerializer,
        responses={201: ConversationResponseSerializer},
    )
    def post(self, request: Request) -> Response:
        ser = ConversationCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        # ensure the department exists
        try:
            dept = Department.objects.get(id=ser.validated_data["department_id"])
        except Department.DoesNotExist:
            return Response(
                {"detail": "department not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        conv = start_conversation(
            department_id=dept.id,
            language_code=ser.validated_data.get("language_code", "en"),
            citizen_ref=ser.validated_data.get("citizen_ref", ""),
        )
        return Response(
            {"id": conv.id, "department_id": dept.id, "language_code": conv.language_code},
            status=status.HTTP_201_CREATED,
        )


class StreamMessageSerializer(serializers.Serializer):
    text = serializers.CharField(allow_blank=False, max_length=8000)
    department_id = serializers.UUIDField(required=False)  # fallback if no tenant header


class StreamingSSEView(APIView):
    """POST /api/v1/conversations/{id}/messages/stream/

    Returns text/event-stream. Each event is a JSON payload.
    """
    permission_classes = [AllowAny]
    serializer_class = StreamMessageSerializer

    @extend_schema(
        request=StreamMessageSerializer,
        responses={200: OpenApiResponse(description="SSE stream of token/citation/done events")},
    )
    def post(self, request: Request, conversation_id: uuid.UUID) -> Response:
        ser = self.serializer_class(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            conv = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            return Response({"detail": "conversation not found"},
                            status=status.HTTP_404_NOT_FOUND)

        guardrail = conv.department.guardrail_prompt or ""
        text = ser.validated_data["text"]

        def event_stream():
            yield "event: ready\ndata: {}\n\n"
            for event, data in stream_turn_sync(
                conversation=conv, user_text=text,
                department_guardrail=guardrail,
            ):
                payload = json.dumps(data, ensure_ascii=False)
                yield f"event: {event}\ndata: {payload}\n\n"
            yield "event: end\ndata: {}\n\n"

        # use Django's StreamingHttpResponse, not DRF Response — DRF Response
        # buffers the whole body, which defeats the SSE token-by-token flow
        resp = StreamingHttpResponse(event_stream(), status=status.HTTP_200_OK,
                                      content_type="text/event-stream")
        resp["Cache-Control"] = "no-cache"
        resp["X-Accel-Buffering"] = "no"  # tell nginx not to buffer SSE
        return resp
