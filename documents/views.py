from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from core.http import ApiError, api_view, client_id, parse_json

from .models import Document, new_share_token
from .sanitize import sanitize_html

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_DOCS_PER_CLIENT = 200
MAX_TITLE = 200


def _summary(doc):
    return {
        "id": str(doc.id),
        "title": doc.title,
        "template": doc.template,
        "shared": doc.share_token is not None,
        "createdAt": doc.created_at.isoformat(),
        "updatedAt": doc.updated_at.isoformat(),
    }


def _full(doc):
    data = _summary(doc)
    data["html"] = doc.html
    data["shareToken"] = doc.share_token
    return data


def _owned(request, doc_id):
    return get_object_or_404(Document, pk=doc_id, client_id=client_id(request))


def _apply_fields(doc, data, partial):
    if "title" in data or not partial:
        title = data.get("title", doc.title)
        if not isinstance(title, str):
            raise ApiError("title must be a string.", code="invalid_title")
        doc.title = title.strip()[:MAX_TITLE] or "Document1"
    if "template" in data:
        template = data["template"]
        if not isinstance(template, str) or len(template) > 64:
            raise ApiError("template must be a string up to 64 characters.", code="invalid_template")
        doc.template = template
    if "html" in data:
        html = data["html"]
        if not isinstance(html, str):
            raise ApiError("html must be a string.", code="invalid_html")
        if len(html.encode("utf-8")) > MAX_HTML_BYTES:
            raise ApiError("Document is too large (5 MB maximum).", status=413, code="too_large")
        doc.html = sanitize_html(html)


@api_view(["GET", "POST"])
def document_collection(request):
    owner = client_id(request)
    if request.method == "GET":
        docs = Document.objects.filter(client_id=owner).defer("html")
        return JsonResponse({"documents": [_summary(d) for d in docs]})

    data = parse_json(request)
    if Document.objects.filter(client_id=owner).count() >= MAX_DOCS_PER_CLIENT:
        raise ApiError("Document limit reached.", status=409, code="limit_reached")
    doc = Document(client_id=owner)
    _apply_fields(doc, data, partial=False)
    doc.save()
    return JsonResponse(_full(doc), status=201)


@api_view(["GET", "PUT", "DELETE"])
def document_detail(request, doc_id):
    owner = client_id(request)
    if request.method == "PUT":
        # Upsert under the caller-chosen id so local drafts can be synced as-is.
        data = parse_json(request)
        existing = Document.objects.filter(pk=doc_id).first()
        if existing and existing.client_id != owner:
            raise ApiError("Document not found.", status=404, code="not_found")
        created = existing is None
        if created and Document.objects.filter(client_id=owner).count() >= MAX_DOCS_PER_CLIENT:
            raise ApiError("Document limit reached.", status=409, code="limit_reached")
        doc = existing or Document(pk=doc_id, client_id=owner)
        _apply_fields(doc, data, partial=not created)
        doc.save()
        return JsonResponse(_full(doc), status=201 if created else 200)

    doc = _owned(request, doc_id)
    if request.method == "DELETE":
        doc.delete()
        return HttpResponse(status=204)
    return JsonResponse(_full(doc))


@api_view(["POST", "DELETE"])
def document_share(request, doc_id):
    doc = _owned(request, doc_id)
    if request.method == "POST":
        if not doc.share_token:
            doc.share_token = new_share_token()
            doc.save(update_fields=["share_token"])
        return JsonResponse({"shareToken": doc.share_token, "path": f"/shared/{doc.share_token}"})
    doc.share_token = None
    doc.save(update_fields=["share_token"])
    return HttpResponse(status=204)


@api_view(["GET"])
def shared_document(request, token):
    doc = get_object_or_404(Document, share_token=token)
    return JsonResponse(
        {
            "title": doc.title,
            "template": doc.template,
            "html": doc.html,
            "updatedAt": doc.updated_at.isoformat(),
        }
    )
