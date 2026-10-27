"""Document tools: stats, table of contents, find/replace, proofing, export,
version history and mail merge."""

from urllib.parse import quote

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from catalog import data as catalog
from core.http import ApiError, api_view, parse_json
from proofing import services as proofing

from . import dom, fileformats, services
from .common import owned, snapshot
from .models import DocumentVersion, MergeDataset


def _protected(doc):
    if doc.is_protected:
        raise ApiError("The document is protected.", status=403, code="document_protected")


# ---- Stats, TOC, find -------------------------------------------------------


@api_view(["GET"])
def document_stats(request, doc_id):
    doc = owned(request, doc_id)
    pages = request.GET.get("pages")
    if pages is not None and (not pages.isdigit() or not 1 <= int(pages) <= 10000):
        raise ApiError("pages must be a positive integer.", code="invalid_pages")
    return JsonResponse(services.document_stats(doc.html, int(pages) if pages else None))


@api_view(["GET"])
def document_toc(request, doc_id):
    doc = owned(request, doc_id)
    raw = request.GET.get("levels", "1,2,3")
    try:
        levels = sorted({int(x) for x in raw.split(",") if x.strip()})
    except ValueError:
        levels = []
    if not levels or any(not 1 <= lvl <= 6 for lvl in levels):
        raise ApiError("levels must be a comma-separated list of 1-6.", code="invalid_levels")
    return JsonResponse(services.build_toc(doc.html, tuple(levels)))


@api_view(["POST"])
def document_find(request, doc_id):
    """Find, or find-and-replace-all when `replacement` is given (saves the document)."""
    doc = owned(request, doc_id)
    data = parse_json(request)
    query = data.get("query")
    if not isinstance(query, str) or not query or len(query) > 500:
        raise ApiError("query must be a non-empty string up to 500 characters.", code="invalid_query")
    replacement = data.get("replacement")
    if replacement is not None and (not isinstance(replacement, str) or len(replacement) > 5000):
        raise ApiError("replacement must be a string up to 5000 characters.", code="invalid_replacement")
    for flag in ("matchCase", "wholeWord"):
        if flag in data and not isinstance(data[flag], bool):
            raise ApiError(f"{flag} must be true or false.", code="invalid_field")
    if replacement is not None:
        _protected(doc)
    count, matches, new_html = services.find_replace(
        doc.html, query, data.get("matchCase", False), data.get("wholeWord", False), replacement
    )
    result = {"count": count, "matches": matches, "replaced": 0}
    if replacement is not None and count:
        doc.html = new_html
        doc.save(update_fields=["html", "updated_at"])
        result["replaced"] = count
        result["html"] = doc.html
    return JsonResponse(result)


# ---- Proofing on a stored document ------------------------------------------


@api_view(["GET"])
def document_spellcheck(request, doc_id):
    doc = owned(request, doc_id)
    return JsonResponse({"issues": proofing.find_spelling_issues(dom.html_to_text(doc.html))})


@api_view(["GET"])
def document_readability(request, doc_id):
    doc = owned(request, doc_id)
    return JsonResponse(proofing.readability(dom.html_to_text(doc.html)))


# ---- Export -----------------------------------------------------------------


@api_view(["GET"])
def document_export(request, doc_id):
    doc = owned(request, doc_id)
    fmt = request.GET.get("format", "docx").lower()
    if fmt not in fileformats.CONTENT_TYPES:
        raise ApiError(
            f"format must be one of: {', '.join(fileformats.CONTENT_TYPES)}.", code="invalid_format"
        )
    settings = doc.settings
    size = catalog.PAGE_SIZES[settings.get("pageSize", "A4")]
    width, height = size["width"], size["height"]
    if settings.get("orientation") == "landscape":
        width, height = height, width
    author = doc.properties.get("author") or "GreenDoc"
    body = fileformats.export(
        fmt, doc.html, doc.title, **({"author": author, "settings": settings, "page": {"width": width, "height": height}} if fmt == "docx" else {})
    )
    response = HttpResponse(body, content_type=fileformats.CONTENT_TYPES[fmt])
    filename = f"{fileformats.safe_filename(doc.title)}.{fmt}"
    response["Content-Disposition"] = f"attachment; filename=\"document.{fmt}\"; filename*=UTF-8''{quote(filename)}"
    return response


# ---- Version history --------------------------------------------------------


def _version(v, with_html=False):
    data = {
        "id": str(v.id),
        "title": v.title,
        "label": v.label,
        "automatic": v.automatic,
        "createdAt": v.created_at.isoformat(),
    }
    if with_html:
        data.update(html=v.html, settings=v.settings)
    return data


@api_view(["GET", "POST"])
def version_collection(request, doc_id):
    doc = owned(request, doc_id)
    if request.method == "GET":
        return JsonResponse({"versions": [_version(v) for v in doc.versions.defer("html")]})
    label = parse_json(request).get("label", "")
    if not isinstance(label, str) or len(label) > 100:
        raise ApiError("label must be a string up to 100 characters.", code="invalid_label")
    return JsonResponse(_version(snapshot(doc, label.strip(), automatic=False)), status=201)


@api_view(["GET", "DELETE"])
def version_detail(request, doc_id, version_id):
    doc = owned(request, doc_id)
    version = get_object_or_404(DocumentVersion, pk=version_id, document=doc)
    if request.method == "DELETE":
        version.delete()
        return HttpResponse(status=204)
    return JsonResponse(_version(version, with_html=True))


@api_view(["POST"])
def version_restore(request, doc_id, version_id):
    doc = owned(request, doc_id)
    _protected(doc)
    version = get_object_or_404(DocumentVersion, pk=version_id, document=doc)
    snapshot(doc, "Before restore", automatic=False)  # restoring is itself undoable
    doc.title, doc.html, doc.settings = version.title, version.html, version.settings
    doc.save()
    from .common import full

    return JsonResponse(full(doc))


# ---- Mail merge -------------------------------------------------------------


def _dataset(doc):
    return MergeDataset.objects.filter(document=doc).first()


def _dataset_json(doc, ds):
    return {
        "fields": ds.fields if ds else [],
        "records": ds.records if ds else [],
        "count": len(ds.records) if ds else 0,
        "fieldsInDocument": services.merge_fields_in(doc.html),
    }


def _need_records(ds):
    if not ds or not ds.records:
        raise ApiError("Select recipients before merging.", status=409, code="no_recipients")
    return ds


@api_view(["GET", "PUT", "DELETE"])
def mailmerge(request, doc_id):
    doc = owned(request, doc_id)
    ds = _dataset(doc)
    if request.method == "DELETE":
        if ds:
            ds.delete()
        return HttpResponse(status=204)
    if request.method == "PUT":
        data = parse_json(request)
        try:
            if "csv" in data:
                if not isinstance(data["csv"], str):
                    raise services.MergeError("csv must be a string.")
                fields, records = services.parse_csv(data["csv"])
                if not records:
                    raise services.MergeError("Add a header row and at least one recipient.")
            else:
                fields, records = data.get("fields"), data.get("records")
            fields, records = services.validate_dataset(fields, records)
        except services.MergeError as exc:
            raise ApiError(str(exc), code="invalid_recipients")
        ds, _ = MergeDataset.objects.update_or_create(document=doc, defaults={"fields": fields, "records": records})
    return JsonResponse(_dataset_json(doc, ds))


@api_view(["GET"])
def mailmerge_preview(request, doc_id):
    doc = owned(request, doc_id)
    ds = _need_records(_dataset(doc))
    raw = request.GET.get("index", "0")
    try:
        index = int(raw)
    except ValueError:
        raise ApiError("index must be an integer.", code="invalid_index")
    index %= len(ds.records)  # wraps like the Next/Previous Record buttons
    return JsonResponse(
        {"index": index, "count": len(ds.records), "html": services.preview_record(doc.html, ds.records[index])}
    )


@api_view(["GET"])
def mailmerge_finish(request, doc_id):
    doc = owned(request, doc_id)
    ds = _need_records(_dataset(doc))
    html = services.merge_all(doc.html, ds.records)
    if request.GET.get("format") == "json":
        return JsonResponse({"count": len(ds.records), "html": html})
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    name = quote(f"{fileformats.safe_filename(doc.title)}-merged.html")
    response["Content-Disposition"] = f"attachment; filename=\"merged.html\"; filename*=UTF-8''{name}"
    return response
