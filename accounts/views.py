import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import HttpResponse, JsonResponse
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from core.http import ApiError, api_view, bearer_token, parse_json, require_user

from . import services, tokens
from .models import AuthToken

User = get_user_model()

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
RESET_EMAILS_PER_HOUR = 5
_DUMMY_HASH = None


def _guard_demo(user):
    """The demo account is shared by everyone, so people cannot lock each other out of it."""
    if user.username == settings.DEMO_EMAIL:
        raise ApiError(
            "This is the shared demo account, so it can't be changed or deleted. Create your own account to do that.",
            status=403,
            code="demo_account",
        )


def _ip(request):
    return request.META.get("REMOTE_ADDR", "unknown")


def _guest_id(request):
    """The browser's guest UUID, if it sent a valid one, and did not opt out of keeping its work."""
    raw = request.headers.get("X-Client-Id", "")
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _session(request, user, data):
    """Issue a token and carry the browser's guest documents into the account."""
    claimed = None
    guest = _guest_id(request)
    if guest and data.get("claimGuestData", True) is not False:
        claimed = services.claim_guest_data(guest, user.profile.owner_id)
    token = tokens.issue_token(user, request.headers.get("User-Agent", ""))
    body = {"token": token, "user": services.user_json(user)}
    if claimed is not None:
        body["claimed"] = claimed
    return body


@api_view(["POST"])
def register(request):
    data = parse_json(request)
    email = services.clean_email(data.get("email"))
    first = services.clean_name(data.get("firstName"), "firstName", "first name")
    last = services.clean_name(data.get("lastName"), "lastName", "last name")
    if User.objects.filter(username=email).exists():
        msg = "An account with this email already exists. Try signing in instead."
        raise ApiError(msg, status=409, code="email_taken", fields={"email": msg})
    probe = User(username=email, email=email, first_name=first, last_name=last)
    password = services.clean_password(data.get("password"), probe)
    user = services.create_user(email, first, last, password)
    return JsonResponse(_session(request, user, data), status=201)


@api_view(["POST"])
def login(request):
    data = parse_json(request)
    email = services.clean_email(data.get("email"))
    password = data.get("password")
    key = f"login-fail:{email}:{_ip(request)}"
    if cache.get(key, 0) >= MAX_FAILED_LOGINS:
        raise ApiError(
            "Too many failed attempts. Try again in 15 minutes or reset your password.",
            status=429,
            code="too_many_attempts",
            headers={"Retry-After": str(LOCKOUT_SECONDS)},
        )
    user = User.objects.filter(username=email).first()
    global _DUMMY_HASH
    if user is None:
        _DUMMY_HASH = _DUMMY_HASH or make_password("greendoc-timing-pad")
        check_password(password if isinstance(password, str) else "", _DUMMY_HASH)  # same cost as a real check
        ok = False
    else:
        ok = isinstance(password, str) and user.check_password(password) and user.is_active
    if not ok:
        cache.add(key, 0, timeout=LOCKOUT_SECONDS)
        cache.incr(key)
        raise ApiError("Email or password is incorrect.", status=401, code="invalid_credentials")
    cache.delete(key)
    return JsonResponse(_session(request, user, data))


@api_view(["POST"])
def logout(request):
    require_user(request)
    tokens.revoke_token(bearer_token(request))
    return HttpResponse(status=204)


@api_view(["POST"])
def logout_all(request):
    tokens.revoke_all(require_user(request))
    return HttpResponse(status=204)


@api_view(["GET", "PUT", "DELETE"])
def me(request):
    user = require_user(request)
    if request.method == "GET":
        return JsonResponse(services.user_json(user))
    data = parse_json(request)
    if request.method == "DELETE":
        _guard_demo(user)
        if not user.check_password(data.get("password") if isinstance(data.get("password"), str) else ""):
            raise ApiError("Password is incorrect.", status=403, code="invalid_credentials", fields={"password": "Password is incorrect."})
        services.delete_account(user)
        return HttpResponse(status=204)
    unknown = set(data) - {"firstName", "lastName", "email", "currentPassword"}
    if unknown:
        raise ApiError(f"Unknown field(s): {', '.join(sorted(unknown))}.", code="unknown_field")
    if "firstName" in data:
        user.first_name = services.clean_name(data["firstName"], "firstName", "first name")
    if "lastName" in data:
        user.last_name = services.clean_name(data["lastName"], "lastName", "last name")
    if "email" in data:
        email = services.clean_email(data["email"])
        if email != user.email:
            _guard_demo(user)
            current = data.get("currentPassword")
            if not isinstance(current, str) or not user.check_password(current):
                raise ApiError(
                    "Enter your current password to change your email.",
                    status=403,
                    code="invalid_credentials",
                    fields={"currentPassword": "Current password is incorrect."},
                )
            if User.objects.filter(username=email).exclude(pk=user.pk).exists():
                msg = "An account with this email already exists."
                raise ApiError(msg, status=409, code="email_taken", fields={"email": msg})
            user.email = user.username = email
    user.save()
    return JsonResponse(services.user_json(user))


@api_view(["POST"])
def change_password(request):
    user = require_user(request)
    _guard_demo(user)
    data = parse_json(request)
    current = data.get("currentPassword")
    if not isinstance(current, str) or not user.check_password(current):
        raise ApiError(
            "Current password is incorrect.",
            status=403,
            code="invalid_credentials",
            fields={"currentPassword": "Current password is incorrect."},
        )
    new = services.clean_password(data.get("newPassword"), user, field="newPassword")
    user.set_password(new)
    user.save()
    keep = tokens._hash(bearer_token(request))
    AuthToken.objects.filter(user=user).exclude(token_hash=keep).delete()  # sign out other devices
    return JsonResponse({"message": "Password changed. Other devices have been signed out."})


@api_view(["POST"])
def forgot_password(request):
    """Always answers the same way so the form cannot be used to discover who has an account."""
    data = parse_json(request)
    email = services.clean_email(data.get("email"))
    reply = {"message": "If an account exists for that email, a reset link is on its way."}
    key = f"reset-mail:{email}"
    cache.add(key, 0, timeout=3600)
    if cache.incr(key) > RESET_EMAILS_PER_HOUR:
        return JsonResponse(reply)
    user = User.objects.filter(username=email, is_active=True).first()
    if user and user.username != settings.DEMO_EMAIL:
        uid = urlsafe_base64_encode(str(user.pk).encode())
        token = default_token_generator.make_token(user)
        link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
        send_mail(
            "Reset your GreenDoc password",
            f"Hi {user.first_name},\n\nUse this link to choose a new password (valid for 3 days):\n{link}\n\n"
            "If you didn't ask for this, you can ignore this email; your password won't change.\n\nGreenDoc",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
    return JsonResponse(reply)


@api_view(["POST"])
def reset_password(request):
    data = parse_json(request)
    invalid = ApiError("This reset link is invalid or has expired. Request a new one.", code="invalid_reset_link")
    try:
        pk = urlsafe_base64_decode(str(data.get("uid", ""))).decode()
        user = User.objects.get(pk=pk, is_active=True)
    except (ValueError, UnicodeDecodeError, User.DoesNotExist):
        raise invalid
    if not default_token_generator.check_token(user, str(data.get("token", ""))):
        raise invalid
    _guard_demo(user)
    user.set_password(services.clean_password(data.get("newPassword"), user, field="newPassword"))
    user.save()
    tokens.revoke_all(user)
    return JsonResponse({"message": "Password updated. You can sign in now."})
