from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from core.http import ApiError, api_view, client_id, parse_json

from . import fileformats
from .common import (
    MAX_DOCS_PER_CLIENT,
    apply_fields,
    check_html,
    full,
    maybe_autosnapshot,
    owned,
    seed_from_template,
    summary,
)
from .models import Document, new_share_token

RECENT_LIMIT = 8


def _check_quota(owner):
    if Document.objects.filter(client_id=owner).count() >= MAX_DOCS_PER_CLIENT:
        raise ApiError("Document limit reached.", status=409, code="limit_reached")


def _save(doc):
    doc.in_recent = True
    doc.save()
    maybe_autosnapshot(doc)


@api_view(["GET", "POST"])
def document_collection(request):
    owner = client_id(request)
    if request.method == "GET":
        docs = Document.objects.filter(client_id=owner).defer("html")
        query = request.GET.get("q", "").strip()
        if query:
            docs = docs.filter(title__icontains=query)
        return JsonResponse({"documents": [summary(d) for d in docs]})

    data = parse_json(request)
    _check_quota(owner)
    doc = Document(client_id=owner)
    seed_from_template(doc, data)
    apply_fields(doc, data, partial=False)
    _save(doc)
    return JsonResponse(full(doc), status=201)


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
        if created:
            _check_quota(owner)
        doc = existing or Document(pk=doc_id, client_id=owner)
        if created:
            seed_from_template(doc, data)
        apply_fields(doc, data, partial=not created)
        _save(doc)
        return JsonResponse(full(doc), status=201 if created else 200)

    doc = owned(request, doc_id)
    if request.method == "DELETE":
        doc.delete()
        return HttpResponse(status=204)
    return JsonResponse(full(doc))


@api_view(["POST"])
def document_duplicate(request, doc_id):
    doc = owned(request, doc_id)
    owner = doc.client_id
    _check_quota(owner)
    copy = Document(
        client_id=owner,
        title=f"{doc.title} (copy)"[:200],
        template=doc.template,
        html=doc.html,
        settings=doc.settings,
        properties=doc.properties,
    )
    _save(copy)
    return JsonResponse(full(copy), status=201)


PERMISSIONS = ("view", "edit")


def share_permission(data, default):
    permission = data.get("permission", default)
    if permission not in PERMISSIONS:
        raise ApiError("permission must be view or edit.", code="invalid_permission")
    return permission


def ensure_shared(doc, permission):
    """Make sure the document has a share link with the given permission."""
    changed = []
    if not doc.share_token:
        doc.share_token = new_share_token()
        changed.append("share_token")
    if doc.share_permission != permission:
        doc.share_permission = permission
        changed.append("share_permission")
    if changed:
        doc.save(update_fields=changed)
    return {"shareToken": doc.share_token, "permission": doc.share_permission, "path": f"/shared/{doc.share_token}"}


@api_view(["POST", "DELETE"])
def document_share(request, doc_id):
    doc = owned(request, doc_id)
    if request.method == "POST":
        # The body is optional: a plain POST just creates (or returns) the existing link.
        data = parse_json(request) if request.content_type == "application/json" else {}
        return JsonResponse(ensure_shared(doc, share_permission(data, doc.share_permission)))
    doc.share_token = None
    doc.share_permission = "view"
    doc.save(update_fields=["share_token", "share_permission"])
    return HttpResponse(status=204)


@api_view(["GET", "PUT"])
def shared_document(request, token):
    """Public link access. With an edit link, anyone who has the link can change
    the title and text (like Word's 'anyone with the link can edit')."""
    doc = get_object_or_404(Document, share_token=token)
    if request.method == "PUT":
        if doc.share_permission != "edit":
            raise ApiError("This link is view-only.", status=403, code="view_only")
        if doc.is_protected:
            raise ApiError("The document is protected.", status=403, code="document_protected")
        data = parse_json(request)
        unknown = set(data) - {"title", "html"}
        if unknown:
            raise ApiError(f"Unknown field(s): {', '.join(sorted(unknown))}.", code="unknown_field")
        apply_fields(doc, data, partial=True)
        doc.save()
        maybe_autosnapshot(doc)
    return JsonResponse(
        {
            "title": doc.title,
            "template": doc.template,
            "html": doc.html,
            "settings": doc.settings,
            "permission": doc.share_permission,
            "canEdit": doc.share_permission == "edit" and not doc.is_protected,
            "updatedAt": doc.updated_at.isoformat(),
        }
    )


@api_view(["GET", "DELETE"])
def recent(request):
    """File > Open > Recent: the last documents saved or opened by this browser."""
    owner = client_id(request)
    qs = Document.objects.filter(client_id=owner, in_recent=True)
    if request.method == "DELETE":
        qs.update(in_recent=False)  # clears the list; the documents themselves stay
        return HttpResponse(status=204)
    docs = qs.defer("html")[:RECENT_LIMIT]
    return JsonResponse(
        {
            "recent": [
                {
                    "id": str(d.id),
                    "title": d.title,
                    "template": d.template,
                    "modified": d.updated_at.isoformat(),
                    "path": d.source_name,
                }
                for d in docs
            ]
        }
    )


@api_view(["POST"])
def document_import(request):
    """File > Open from disk: upload a .docx, .html or .txt and get a new document."""
    owner = client_id(request)
    upload = request.FILES.get("file")
    if upload is None:
        raise ApiError("Send the file as multipart form data in the 'file' field.", code="missing_file")
    _check_quota(owner)
    try:
        html, title, kind = fileformats.import_file(upload.name, upload.read(fileformats.MAX_IMPORT_BYTES + 1))
    except fileformats.ImportError_ as exc:
        raise ApiError(str(exc), status=422, code="import_failed")
    doc = Document(
        client_id=owner,
        title=title[:200] or "Document1",
        template=kind,
        html=check_html(html),
        source_name=upload.name[:255],
    )
    _save(doc)
    return JsonResponse(full(doc), status=201)
