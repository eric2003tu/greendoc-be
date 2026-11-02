from django.core.cache import cache
from django.http import HttpResponse, JsonResponse

from core.http import ApiError, api_view, client_id, parse_json, require_admin

from . import services, stats
from .models import LearningProfile, TermStat

MAX_BODY = 1_000_000


@api_view(["GET", "PUT", "DELETE"])
def profile(request):
    """My learned model: read it, back up what the browser learned, or erase it."""
    owner = client_id(request)
    if request.method == "DELETE":
        services.delete_profile(owner)
        return HttpResponse(status=204)
    if request.method == "PUT":
        if len(request.body) > MAX_BODY:
            raise ApiError("Profile is too large.", status=413, code="too_large")
        row = services.save_profile(owner, parse_json(request))
    else:
        row = LearningProfile.objects.filter(client_id=owner).first()
    return JsonResponse(services.public_profile(row))


@api_view(["GET"])
def global_model(request):
    """The model built from everyone who chose to share, limited to what several people agree on."""
    return JsonResponse(services.global_model())


@api_view(["GET"])
def engine_stats(request):
    """How the engine is being trained so far. Administrators only; no individual's data is included."""
    require_admin(request)
    return JsonResponse(stats.engine_summary_json())


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@api_view(["GET"])
def terms(request):
    """Browse everything the engine has learned (administrators only): filter, search, page."""
    require_admin(request)
    q = request.GET
    return JsonResponse(
        stats.browse_terms(
            kind=q.get("kind", ""),
            status=q.get("status", ""),
            query=q.get("q", "").strip(),
            page=_int(q.get("page"), 1),
            page_size=_int(q.get("pageSize"), stats.PAGE_SIZE),
        )
    )


@api_view(["PUT"])
def term(request, term_id):
    """Take a term out of the shared dictionary, or put it back (administrators only)."""
    require_admin(request)
    row = TermStat.objects.filter(pk=_int(term_id, -1)).first()
    if row is None:
        raise ApiError("That term was not found.", status=404, code="not_found")
    removed = parse_json(request).get("removed")
    if not isinstance(removed, bool):
        raise ApiError("removed must be true or false.", code="invalid_field")
    row.removed = removed
    row.save(update_fields=["removed", "updated_at"])
    cache.delete(services.CACHE_KEY)  # the published dictionary changes at once
    return JsonResponse(stats._term(row))
