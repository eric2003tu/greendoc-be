"""Session tokens.

- A token is random, shown to the client once, and stored only as a SHA-256 hash.
- It lives 30 days from last use (sliding) and at most 90 days in total.
- `rotate` swaps it for a fresh token (the app does this regularly), so a token
  that leaked long ago stops working.
"""

import hashlib
import re
import secrets
from datetime import timedelta

from django.utils import timezone

from .models import AuthToken

SLIDING_LIFETIME = timedelta(days=30)
ABSOLUTE_LIFETIME = timedelta(days=90)
MAX_DEVICES = 10
TOUCH_EVERY = timedelta(hours=1)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def hash_token(token):
    return _hash(token)


def absolute_expiry(row):
    return row.max_expires_at or (row.created_at + ABSOLUTE_LIFETIME)


def _create(user, device):
    raw = secrets.token_urlsafe(32)
    now = timezone.now()
    row = AuthToken.objects.create(
        user=user,
        token_hash=_hash(raw),
        expires_at=now + SLIDING_LIFETIME,
        max_expires_at=now + ABSOLUTE_LIFETIME,
        device=device[:200],
    )
    stale = AuthToken.objects.filter(user=user).order_by("-created_at")[MAX_DEVICES:]
    AuthToken.objects.filter(pk__in=[t.pk for t in stale]).delete()
    return raw, row


def issue_token(user, device=""):
    """Create a session. Returns (raw_token, session_row); the raw token is never stored."""
    return _create(user, device)


def session_for_token(raw):
    """The live session row for a token, or None. Extends the sliding window on use."""
    row = AuthToken.objects.select_related("user__profile").filter(token_hash=_hash(raw)).first()
    if row is None or not row.user.is_active:
        return None
    now = timezone.now()
    if row.expires_at <= now or absolute_expiry(row) <= now:
        row.delete()
        return None
    if now - row.last_used_at > TOUCH_EVERY:
        row.last_used_at = now
        row.expires_at = min(now + SLIDING_LIFETIME, absolute_expiry(row))
        row.save(update_fields=["last_used_at", "expires_at"])
    return row


def user_for_token(raw):
    row = session_for_token(raw)
    return row.user if row else None


def rotate(raw):
    """Replace a valid token with a new one for the same device. Returns (raw, row) or None."""
    row = session_for_token(raw)
    if row is None:
        return None
    user, device = row.user, row.device
    row.delete()
    return _create(user, device)


def revoke_token(raw):
    AuthToken.objects.filter(token_hash=_hash(raw)).delete()


def revoke_all(user):
    AuthToken.objects.filter(user=user).delete()


def describe_device(user_agent):
    """A short, friendly label such as 'Chrome on Windows' for the sessions list."""
    ua = user_agent or ""
    browser = next(
        (name for pattern, name in (
            (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"),
            (r"Chrome/|CriOS/", "Chrome"), (r"Safari/", "Safari"),
        ) if re.search(pattern, ua)),
        "Browser",
    )
    system = next(
        (name for pattern, name in (
            (r"Android", "Android"), (r"iPhone|iPad|iOS", "iOS"), (r"Windows", "Windows"),
            (r"Mac OS X|Macintosh", "macOS"), (r"Linux|X11", "Linux"),
        ) if re.search(pattern, ua)),
        None,
    )
    return f"{browser} on {system}" if system else browser
