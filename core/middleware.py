import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse

ALLOWED_HEADERS = "Content-Type, X-Client-Id"
ALLOWED_METHODS = "GET, POST, PUT, DELETE, OPTIONS"


class CorsMiddleware:
    """CORS for the browser frontend. The API uses a header identity, not cookies,
    so a wildcard origin is safe; restrict via GREENDOC_CORS_ORIGINS in production."""

    def __init__(self, get_response):
        self.get_response = get_response

    def _allowed_origin(self, request):
        origins = settings.CORS_ALLOWED_ORIGINS
        origin = request.headers.get("Origin", "")
        if "*" in origins:
            return "*"
        return origin if origin in origins else None

    def __call__(self, request):
        if request.method == "OPTIONS" and "Access-Control-Request-Method" in request.headers:
            response = HttpResponse(status=204)
            response["Access-Control-Allow-Methods"] = ALLOWED_METHODS
            response["Access-Control-Allow-Headers"] = ALLOWED_HEADERS
            response["Access-Control-Max-Age"] = "86400"
        else:
            response = self.get_response(request)
        allowed = self._allowed_origin(request)
        if allowed:
            response["Access-Control-Allow-Origin"] = allowed
            if allowed != "*":
                response["Vary"] = "Origin"
        return response


class RateLimitMiddleware:
    """Fixed-window per-IP limiter for /api/. The API is public, so this is the
    first line of defence against abuse. Uses the default cache."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        limit = settings.RATE_LIMIT_PER_MINUTE
        if limit and request.path.startswith("/api/") and request.method != "OPTIONS":
            ip = request.META.get("REMOTE_ADDR", "unknown")
            window = int(time.time() // 60)
            key = f"rl:{ip}:{window}"
            cache.add(key, 0, timeout=120)
            count = cache.incr(key)
            if count > limit:
                response = JsonResponse(
                    {"error": {"code": "rate_limited", "message": "Too many requests."}},
                    status=429,
                )
                response["Retry-After"] = str(60 - int(time.time()) % 60)
                return response
        return self.get_response(request)
