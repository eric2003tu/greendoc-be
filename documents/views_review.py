"""Review tab: comments (with replies) and tracked changes."""

import secrets

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from core.http import ApiError, api_view, parse_json

from . import services
from .common import author_for, owned
from .models import Comment, CommentReply, Revision

MAX_PER_DOCUMENT = 1000
KINDS = ("insert", "delete", "format")


def _text(data, key="text", required=True):
    value = data.get(key, "" if not required else None)
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > 5000:
        raise ApiError(f"{key} must be a non-empty string up to 5000 characters.", code=f"invalid_{key}")
    return value.strip() if required else value


def _client_id_value(data, prefix):
    """The editor embeds ids in the HTML (data-cid / data-rev), so accept its own id."""
    value = data.get("id")
    if value is None:
        return f"{prefix}-{secrets.token_hex(5)}"
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ApiError("id must be a string up to 64 characters.", code="invalid_id")
    return value


def reply_json(r):
    return {"id": r.rid, "author": r.author, "initials": r.initials, "date": r.created_at.isoformat(), "text": r.text}


def comment_json(c):
    return {
        "id": c.cid,
        "author": c.author,
        "initials": c.initials,
        "date": c.created_at.isoformat(),
        "text": c.text,
        "replies": [reply_json(r) for r in c.replies.all()],
        "resolved": c.resolved,
        "quote": c.quote,
    }


def revision_json(r):
    return {
        "id": r.rid,
        "kind": r.kind,
        "author": r.author,
        "initials": r.initials,
        "date": r.created_at.isoformat(),
        "text": r.text,
    }


def _comment(doc, cid):
    return get_object_or_404(Comment.objects.prefetch_related("replies"), document=doc, cid=cid)


@api_view(["GET", "POST"])
def comment_collection(request, doc_id):
    doc = owned(request, doc_id)
    if request.method == "GET":
        items = doc.comments.prefetch_related("replies")
        if request.GET.get("resolved") in ("true", "false"):
            items = items.filter(resolved=request.GET["resolved"] == "true")
        return JsonResponse({"comments": [comment_json(c) for c in items]})
    data = parse_json(request)
    text = _text(data)
    quote = data.get("quote", "")
    if not isinstance(quote, str) or len(quote) > 2000:
        raise ApiError("quote must be a string up to 2000 characters.", code="invalid_quote")
    if doc.comments.count() >= MAX_PER_DOCUMENT:
        raise ApiError("Comment limit reached.", status=409, code="limit_reached")
    cid = _client_id_value(data, "cmt")
    if doc.comments.filter(cid=cid).exists():
        raise ApiError("A comment with this id already exists.", status=409, code="duplicate_id")
    author, initials = author_for(request, data)
    comment = Comment.objects.create(document=doc, cid=cid, author=author, initials=initials, text=text, quote=quote)
    return JsonResponse(comment_json(comment), status=201)


@api_view(["GET", "PUT", "DELETE"])
def comment_detail(request, doc_id, cid):
    doc = owned(request, doc_id)
    comment = _comment(doc, cid)
    if request.method == "DELETE":
        comment.delete()
        cleaned = services.strip_comment_refs(doc.html, cid)
        if cleaned != doc.html:
            doc.html = cleaned
            doc.save(update_fields=["html", "updated_at"])
        return HttpResponse(status=204)
    if request.method == "PUT":
        data = parse_json(request)
        unknown = set(data) - {"text", "resolved"}
        if unknown:
            raise ApiError(f"Unknown field(s): {', '.join(sorted(unknown))}.", code="unknown_field")
        if "text" in data:
            comment.text = _text(data)
        if "resolved" in data:
            if not isinstance(data["resolved"], bool):
                raise ApiError("resolved must be true or false.", code="invalid_field")
            comment.resolved = data["resolved"]
        comment.save()
    return JsonResponse(comment_json(comment))


@api_view(["POST"])
def comment_reply(request, doc_id, cid):
    doc = owned(request, doc_id)
    comment = _comment(doc, cid)
    data = parse_json(request)
    text = _text(data)
    rid = _client_id_value(data, "rpl")
    if comment.replies.filter(rid=rid).exists():
        raise ApiError("A reply with this id already exists.", status=409, code="duplicate_id")
    if comment.replies.count() >= MAX_PER_DOCUMENT:
        raise ApiError("Reply limit reached.", status=409, code="limit_reached")
    author, initials = author_for(request, data)
    CommentReply.objects.create(comment=comment, rid=rid, author=author, initials=initials, text=text)
    comment = _comment(doc, cid)
    return JsonResponse(comment_json(comment), status=201)


@api_view(["DELETE"])
def comment_reply_detail(request, doc_id, cid, rid):
    doc = owned(request, doc_id)
    comment = _comment(doc, cid)
    get_object_or_404(CommentReply, comment=comment, rid=rid).delete()
    return HttpResponse(status=204)


# ---- Track changes ----------------------------------------------------------


@api_view(["GET", "POST"])
def revision_collection(request, doc_id):
    doc = owned(request, doc_id)
    if request.method == "GET":
        return JsonResponse(
            {"trackChanges": bool(doc.settings.get("trackChanges")), "revisions": [revision_json(r) for r in doc.revisions.all()]}
        )
    data = parse_json(request)
    kind = data.get("kind")
    if kind not in KINDS:
        raise ApiError("kind must be insert, delete or format.", code="invalid_kind")
    text = _text(data, required=False)
    if doc.revisions.count() >= MAX_PER_DOCUMENT:
        raise ApiError("Revision limit reached.", status=409, code="limit_reached")
    rid = _client_id_value(data, "rev")
    if doc.revisions.filter(rid=rid).exists():
        raise ApiError("A revision with this id already exists.", status=409, code="duplicate_id")
    author, initials = author_for(request, data)
    revision = Revision.objects.create(document=doc, rid=rid, kind=kind, author=author, initials=initials, text=text)
    return JsonResponse(revision_json(revision), status=201)


@api_view(["DELETE"])
def revision_detail(request, doc_id, rid):
    doc = owned(request, doc_id)
    get_object_or_404(Revision, document=doc, rid=rid).delete()
    return HttpResponse(status=204)


def _resolve(request, doc_id, accept):
    doc = owned(request, doc_id)
    if doc.is_protected:
        raise ApiError("The document is protected.", status=403, code="document_protected")
    count = doc.revisions.count()
    doc.html = services.resolve_revisions(doc.html, accept)
    doc.save(update_fields=["html", "updated_at"])
    doc.revisions.all().delete()
    return JsonResponse({"resolved": count, "action": "accepted" if accept else "rejected", "html": doc.html})


@api_view(["POST"])
def revisions_accept_all(request, doc_id):
    return _resolve(request, doc_id, True)


@api_view(["POST"])
def revisions_reject_all(request, doc_id):
    return _resolve(request, doc_id, False)
