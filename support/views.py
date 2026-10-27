from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import JsonResponse

from accounts import services as account_services
from core.http import ApiError, api_view, current_user, parse_json

from .models import SupportMessage

PER_HOUR = 5


@api_view(["POST"])
def contact_support(request):
    """Public. Signed-in people don't need to retype their name and email."""
    user = current_user(request)
    data = parse_json(request)
    name = data.get("name") or (account_services.display_name(user) if user else "")
    email = data.get("email") or (user.email if user else None)
    email = account_services.clean_email(email)
    subject, message = data.get("subject"), data.get("message")
    problems = {}
    if not isinstance(name, str) or len(name) > 100:
        problems["name"] = "Name can be at most 100 characters."
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 150:
        problems["subject"] = "Enter a subject (150 characters at most)."
    if not isinstance(message, str) or not message.strip() or len(message) > 5000:
        problems["message"] = "Tell us what you need (5,000 characters at most)."
    if problems:
        raise ApiError("Please check the highlighted fields.", code="invalid_field", fields=problems)

    ip = request.META.get("REMOTE_ADDR", "unknown")
    key = f"support:{ip}"
    cache.add(key, 0, timeout=3600)
    if cache.incr(key) > PER_HOUR:
        raise ApiError("You've sent several messages recently. Please wait a while.", status=429, code="rate_limited")

    saved = SupportMessage.objects.create(
        name=name.strip(), email=email, subject=subject.strip(), message=message.strip(),
        user_id=user.pk if user else None,
    )
    send_mail(
        f"[GreenDoc support] {saved.subject}",
        f"From: {saved.name or 'Anonymous'} <{saved.email}>\n\n{saved.message}",
        settings.DEFAULT_FROM_EMAIL,
        [settings.SUPPORT_EMAIL],
        fail_silently=True,  # the message is already saved
    )
    return JsonResponse({"id": saved.pk, "message": "Thanks, we've received your message and will reply by email."}, status=201)
