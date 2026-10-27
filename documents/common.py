"""Shared helpers for the document views: validation, serialisation, snapshots."""

from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone

from catalog import data as catalog
from core import validate as v
from core.http import ApiError, client_id, current_user
from workspace.models import Preferences

from .models import Document, DocumentVersion
from .sanitize import sanitize_html

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_DOCS_PER_CLIENT = 200
MAX_TITLE = 200
AUTO_SNAPSHOT_EVERY = timedelta(minutes=10)
MAX_AUTO_VERSIONS = 30
MAX_MANUAL_VERSIONS = 50

SETTINGS_SPEC = {
    "pageSize": v.choice(catalog.PAGE_SIZES),
    "orientation": v.choice(["portrait", "landscape"]),
    "margins": v.margins(),
    "gutter": v.integer(0, 400),
    "columns": v.integer(1, 6),
    "lineBetween": v.boolean(),
    "showLineNumbers": v.boolean(),
    "verticalAlign": v.choice(["top", "center", "justify"]),
    "pageNumbers": v.boolean(),
    "pageBorders": v.boolean(),
    "pageColor": v.string(7, pattern=v.HEX),
    "watermarkText": v.string(100),
    "watermarkSemitransparent": v.boolean(),
    "watermarkWashout": v.boolean(),
    "headerText": v.string(500),
    "footerText": v.string(500),
    "trackChanges": v.boolean(),
    "theme": lambda k, val: val if val == "" or val in catalog.theme_ids() else v._fail(k, "unknown design id."),
    "defaultFont": v.string(64),
    "defaultFontSize": v.number(6, 96),
    "language": v.string(20, pattern=v.LANG),
}
PROPERTIES_SPEC = {k: v.string(200) for k in ("subject", "author", "keywords", "company", "category")}


def summary(doc):
    return {
        "id": str(doc.id),
        "title": doc.title,
        "template": doc.template,
        "shared": doc.share_token is not None,
        "protected": doc.is_protected,
        "markedFinal": doc.marked_final,
        "sourceName": doc.source_name,
        "createdAt": doc.created_at.isoformat(),
        "updatedAt": doc.updated_at.isoformat(),
    }


def full(doc):
    data = summary(doc)
    data.update(
        html=doc.html,
        settings=doc.settings,
        properties=doc.properties,
        shareToken=doc.share_token,
    )
    return data


def owned(request, doc_id):
    return get_object_or_404(Document, pk=doc_id, client_id=client_id(request))


def author_for(request, data=None):
    """Author name and initials for a comment or revision: explicit value,
    else the browser's saved preference, else the default."""
    name = (data or {}).get("author")
    if name is None:
        pref = Preferences.objects.filter(client_id=client_id(request)).first()
        user = current_user(request)
        account_name = f"{user.first_name} {user.last_name}".strip() if user else ""
        name = (pref.data.get("authorName") if pref else None) or account_name or "GreenDoc User"
    if not isinstance(name, str) or not name.strip() or len(name) > 60:
        raise ApiError("author must be a non-empty string up to 60 characters.", code="invalid_author")
    name = name.strip()
    initials = "".join(p[0] for p in name.split())[:2].upper() or "U"
    return name, initials


def check_html(html):
    if not isinstance(html, str):
        raise ApiError("html must be a string.", code="invalid_html")
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ApiError("Document is too large (5 MB maximum).", status=413, code="too_large")
    return sanitize_html(html)


def apply_fields(doc, data, partial):
    """Validate and apply the editable fields of a document body."""
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
    if "settings" in data:
        doc.settings = {**doc.settings, **v.validate_blob(data["settings"], SETTINGS_SPEC, "settings")}
    if "properties" in data:
        doc.properties = {**doc.properties, **v.validate_blob(data["properties"], PROPERTIES_SPEC, "properties")}
    for key, attr in (("protected", "is_protected"), ("markedFinal", "marked_final")):
        if key in data:
            if not isinstance(data[key], bool):
                raise ApiError(f"{key} must be true or false.", code="invalid_field")
            setattr(doc, attr, data[key])
    if "html" in data:
        if doc.is_protected and not doc._state.adding:
            raise ApiError(
                "The document is protected. Turn off protection to edit it.",
                status=403,
                code="document_protected",
            )
        doc.html = check_html(data["html"])


def seed_from_template(doc, data):
    """Create-time only: fill html from a catalogue template."""
    template_id = data.get("templateId")
    if template_id is None:
        return
    template = catalog.template_by_id(template_id)
    if not template:
        raise ApiError("Unknown templateId.", code="unknown_template")
    doc.template = template["id"]
    if "html" not in data:
        doc.html = sanitize_html(template["html"])
    if "title" not in data:
        doc.title = template["name"]


def snapshot(doc, label="", automatic=True):
    version = DocumentVersion.objects.create(
        document=doc, title=doc.title, html=doc.html, settings=doc.settings, label=label, automatic=automatic
    )
    limit = MAX_AUTO_VERSIONS if automatic else MAX_MANUAL_VERSIONS
    stale = DocumentVersion.objects.filter(document=doc, automatic=automatic).order_by("-created_at")[limit:]
    DocumentVersion.objects.filter(pk__in=[s.pk for s in stale]).delete()
    return version


def maybe_autosnapshot(doc):
    """Keep a rolling history: at most one automatic snapshot per interval,
    and only when the content actually changed."""
    if not doc.html:
        return
    last = doc.versions.order_by("-created_at").first()
    if last is None or (last.html != doc.html and timezone.now() - last.created_at >= AUTO_SNAPSHOT_EVERY):
        snapshot(doc)
