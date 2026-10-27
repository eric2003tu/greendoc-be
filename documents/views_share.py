"""Share > Collaborate and Share > Email a Copy. Both send email, so they
require a signed-in account and are capped per hour to prevent abuse."""

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage, send_mail
from django.http import JsonResponse

from accounts import services as account_services
from core.http import ApiError, api_view, parse_json, require_user

from . import fileformats
from .common import owned
from .views import ensure_shared, share_permission

MAX_INVITEES = 10
EMAILS_PER_HOUR = 20


def _spend(user, count):
    key = f"mail-out:{user.pk}"
    cache.add(key, 0, timeout=3600)
    if cache.incr(key, count) > EMAILS_PER_HOUR:
        raise ApiError(
            f"You can send up to {EMAILS_PER_HOUR} emails per hour. Please try again later.",
            status=429,
            code="rate_limited",
        )


def _note(data):
    message = data.get("message", "")
    if not isinstance(message, str) or len(message) > 1000:
        raise ApiError("message must be a string up to 1,000 characters.", code="invalid_message")
    return message.strip()


@api_view(["POST"])
def document_invite(request, doc_id):
    """Invite people by email. They receive the document's share link with the chosen permission."""
    user = require_user(request)
    doc = owned(request, doc_id)
    data = parse_json(request)
    raw = data.get("emails")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_INVITEES:
        raise ApiError(f"emails must be a list of 1 to {MAX_INVITEES} addresses.", code="invalid_emails",
                       fields={"emails": f"Add 1 to {MAX_INVITEES} email addresses."})
    emails = list(dict.fromkeys(account_services.clean_email(e) for e in raw))
    permission = share_permission(data, "edit")  # the Share panel invites people to edit
    note = _note(data)
    _spend(user, len(emails))
    link = ensure_shared(doc, permission)
    who = account_services.display_name(user)
    verb = "edit" if permission == "edit" else "view"
    for email in emails:
        send_mail(
            f"{who} shared \"{doc.title}\" with you",
            f"{who} invited you to {verb} \"{doc.title}\" in GreenDoc.\n\n"
            + (f"\"{note}\"\n\n" if note else "")
            + f"Open it here (no account needed): {settings.FRONTEND_URL}{link['path']}\n",
            settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=True,
        )
    return JsonResponse({"sent": len(emails), **link})


@api_view(["POST"])
def document_email_copy(request, doc_id):
    """Email the document as an attachment (defaults to your own address)."""
    user = require_user(request)
    doc = owned(request, doc_id)
    data = parse_json(request)
    to = account_services.clean_email(data.get("to", user.email))
    fmt = data.get("format", "docx")
    if fmt not in fileformats.CONTENT_TYPES:
        raise ApiError(f"format must be one of: {', '.join(fileformats.CONTENT_TYPES)}.", code="invalid_format")
    note = _note(data)
    _spend(user, 1)
    kwargs = {"author": doc.properties.get("author") or account_services.display_name(user)} if fmt == "docx" else {}
    body = fileformats.export(fmt, doc.html, doc.title, **kwargs)
    mail = EmailMessage(
        f"{doc.title}",
        (f"{note}\n\n" if note else "") + f"Sent from GreenDoc by {account_services.display_name(user)}.",
        settings.DEFAULT_FROM_EMAIL,
        [to],
    )
    mail.attach(f"{fileformats.safe_filename(doc.title)}.{fmt}", body, fileformats.CONTENT_TYPES[fmt].split(";")[0])
    mail.send(fail_silently=True)
    return JsonResponse({"sent": True, "to": to, "format": fmt})
