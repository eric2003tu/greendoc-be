"""Small helpers shared by the JSON views (no DRF dependency)."""
import json
import uuid
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


class ApiError(Exception):
    def __init__(self, message, status=400, code="bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def parse_json(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ApiError("Request body must be valid JSON.", code="invalid_json")
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object.", code="invalid_json")
    return data


def client_id(request):
    """Anonymous per-browser identity: a random UUID the frontend generates once."""
    raw = request.headers.get("X-Client-Id", "")
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError):
        raise ApiError(
            "Missing or invalid X-Client-Id header (expected a UUID).",
            code="invalid_client_id",
        )


def api_view(methods):
    """JSON endpoint: restricts methods, exempts CSRF (no cookie auth), maps ApiError."""

    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                response = JsonResponse(
                    {"error": {"code": "method_not_allowed", "message": "Method not allowed."}},
                    status=405,
                )
                response["Allow"] = ", ".join(methods)
                return response
            try:
                return view(request, *args, **kwargs)
            except ApiError as exc:
                return JsonResponse(
                    {"error": {"code": exc.code, "message": exc.message}}, status=exc.status
                )

        return wrapper

    return decorator
