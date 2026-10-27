from django.http import HttpResponse, JsonResponse

from catalog import data as catalog
from core import validate as v
from core.http import ApiError, api_view, client_id, parse_json
from documents import services

from .models import Preferences, Source, SourcesSeeded

PREFERENCE_SPEC = {
    "dark": v.boolean(),
    "autoSave": v.boolean(),
    "showRuler": v.boolean(),
    "showStatusBar": v.boolean(),
    "authorName": v.string(60, allow_empty=False),
    "defaultDesign": lambda k, val: val if val == "" or val in catalog.theme_ids() else v._fail(k, "unknown design id."),
    "defaultFont": v.string(64),
    "defaultFontSize": v.number(6, 96),
    "language": v.string(20, pattern=v.LANG),
    "ribbonDisplay": v.choice(["autoHide", "tabs", "commands"]),
    "view": v.choice(["print", "web", "outline", "draft", "reading"]),
    "zoom": v.integer(10, 500),
    "showGrid": v.boolean(),
    "showParagraphMarks": v.boolean(),
    "showWhitespace": v.boolean(),
}
PREFERENCE_DEFAULTS = {
    "dark": False,
    "autoSave": True,
    "showRuler": True,
    "showStatusBar": True,
    "authorName": "GreenDoc User",
    "defaultDesign": "greenleaf",
    "defaultFont": "Calibri",
    "defaultFontSize": 11,
    "language": "en-US",
    "ribbonDisplay": "commands",
    "view": "print",
    "zoom": 100,
    "showGrid": False,
    "showParagraphMarks": False,
    "showWhitespace": True,
}


@api_view(["GET", "PUT", "DELETE"])
def preferences(request):
    owner = client_id(request)
    row = Preferences.objects.filter(client_id=owner).first()
    if request.method == "DELETE":
        if row:
            row.delete()
        return HttpResponse(status=204)
    if request.method == "PUT":
        changes = v.validate_blob(parse_json(request), PREFERENCE_SPEC, "preference")
        row = row or Preferences(client_id=owner)
        row.data = {**row.data, **changes}
        row.save()
    return JsonResponse({**PREFERENCE_DEFAULTS, **(row.data if row else {})})


# ---- Sources ---------------------------------------------------------------

SOURCE_TYPES = ["Book", "Journal Article", "Report", "Website", "Conference Proceedings", "Other"]
SOURCE_SPEC = {
    "tag": v.string(40, allow_empty=False),
    "type": v.string(40, allow_empty=False),
    "author": v.string(200, allow_empty=False),
    "title": v.string(300, allow_empty=False),
    "year": v.string(10),
    "publisher": v.string(200),
}
MAX_SOURCES = 500


def _source(s):
    return {f: getattr(s, f) for f in ("tag", "type", "author", "title", "year", "publisher")}


def _library(owner):
    """The client's sources; the three defaults are copied in on first use."""
    if not SourcesSeeded.objects.filter(client_id=owner).exists():
        Source.objects.bulk_create(
            [Source(client_id=owner, **s) for s in catalog.DEFAULT_SOURCES], ignore_conflicts=True
        )
        SourcesSeeded.objects.get_or_create(client_id=owner)
    return Source.objects.filter(client_id=owner)


def _get_source(owner, tag):
    source = _library(owner).filter(tag=tag).first()
    if not source:
        raise ApiError("Source not found.", status=404, code="not_found")
    return source


@api_view(["GET", "POST"])
def source_collection(request):
    owner = client_id(request)
    library = _library(owner)
    if request.method == "GET":
        return JsonResponse({"sources": [_source(s) for s in library]})
    data = parse_json(request)
    for required in ("tag", "author", "title"):
        if required not in data:
            raise ApiError(f"{required} is required.", code="missing_field")
    data.setdefault("type", "Book")
    clean = v.validate_blob(data, SOURCE_SPEC, "source")
    if library.count() >= MAX_SOURCES:
        raise ApiError("Source limit reached.", status=409, code="limit_reached")
    if library.filter(tag=clean["tag"]).exists():
        raise ApiError("A source with this tag already exists.", status=409, code="duplicate_tag")
    return JsonResponse(_source(Source.objects.create(client_id=owner, **clean)), status=201)


@api_view(["GET", "PUT", "DELETE"])
def source_detail(request, tag):
    owner = client_id(request)
    source = _get_source(owner, tag)
    if request.method == "DELETE":
        source.delete()
        return HttpResponse(status=204)
    if request.method == "PUT":
        clean = v.validate_blob(parse_json(request), SOURCE_SPEC, "source")
        new_tag = clean.get("tag", source.tag)
        if new_tag != source.tag and Source.objects.filter(client_id=owner, tag=new_tag).exists():
            raise ApiError("A source with this tag already exists.", status=409, code="duplicate_tag")
        for key, value in clean.items():
            setattr(source, key, value)
        source.save()
    return JsonResponse(_source(source))


@api_view(["POST"])
def citation_format(request):
    owner = client_id(request)
    data = parse_json(request)
    style = data.get("style", "APA")
    if style not in services.CITATION_STYLES:
        raise ApiError("style must be APA, MLA or Chicago.", code="invalid_style")
    source = _source(_get_source(owner, str(data.get("tag", ""))))
    return JsonResponse(
        {
            "tag": source["tag"],
            "style": style,
            "text": services.format_citation(source, style),
            "html": services.citation_html(source, style),
        }
    )


@api_view(["GET"])
def bibliography(request):
    owner = client_id(request)
    sources = [_source(s) for s in _library(owner)]
    return JsonResponse({"count": len(sources), "html": services.bibliography_html(sources)})
