import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from .models import AuthToken

TOKEN_LIFETIME = timedelta(days=30)
MAX_DEVICES = 10
TOUCH_EVERY = timedelta(hours=1)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(user, device=""):
    """Create a session token. Returns the raw token, which is never stored."""
    raw = secrets.token_urlsafe(32)
    AuthToken.objects.create(
        user=user, token_hash=_hash(raw), expires_at=timezone.now() + TOKEN_LIFETIME, device=device[:200]
    )
    stale = AuthToken.objects.filter(user=user).order_by("-created_at")[MAX_DEVICES:]
    AuthToken.objects.filter(pk__in=[t.pk for t in stale]).delete()
    return raw


def user_for_token(raw):
    row = AuthToken.objects.select_related("user__profile").filter(token_hash=_hash(raw)).first()
    if row is None or not row.user.is_active:
        return None
    now = timezone.now()
    if row.expires_at <= now:
        row.delete()
        return None
    if now - row.last_used_at > TOUCH_EVERY:
        row.last_used_at = now
        row.save(update_fields=["last_used_at"])
    return row.user


def revoke_token(raw):
    AuthToken.objects.filter(token_hash=_hash(raw)).delete()


def revoke_all(user):
    AuthToken.objects.filter(user=user).delete()
