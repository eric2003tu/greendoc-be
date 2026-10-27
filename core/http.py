"""Small helpers shared by the JSON views (no DRF dependency)."""
import json
import uuid
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


class ApiError(Exception):
    """`fields` maps a form field to its message so a UI can show errors inline."""

    def __init__(self, message, status=400, code="bad_request", fields=None, headers=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.fields = fields
        self.headers = headers or {}


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


def bearer_token(request):
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError("Authorization must be 'Bearer <token>'.", status=401, code="invalid_token")
    return token.strip()


def current_user(request):
    """The signed-in user, or None for a guest. A bad or expired token is an
    error (not a silent guest) so the app knows to ask the person to sign in again."""
    if hasattr(request, "_greendoc_user"):
        return request._greendoc_user
    user = None
    token = bearer_token(request)
    if token:
        from accounts.tokens import user_for_token  # local import: accounts depends on core

        user = user_for_token(token)
        if user is None:
            raise ApiError("Your session has expired. Please sign in again.", status=401, code="invalid_token")
    request._greendoc_user = user
    return user


def require_user(request):
    user = current_user(request)
    if user is None:
        raise ApiError("Sign in to use this feature.", status=401, code="sign_in_required")
    return user


def client_id(request):
    """Owner identity for stored data. Signed in: the account's owner id, the same on every
    device. Guest: the random UUID the browser generated (X-Client-Id)."""
    user = current_user(request)
    if user is not None:
        return user.profile.owner_id
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
                error = {"code": exc.code, "message": exc.message}
                if exc.fields:
                    error["fields"] = exc.fields
                response = JsonResponse({"error": error}, status=exc.status)
                for name, value in exc.headers.items():
                    response[name] = value
                return response

        return wrapper

    return decorator
