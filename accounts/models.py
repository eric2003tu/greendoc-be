import uuid

from django.conf import settings
from django.db import models


class Profile(models.Model):
    """Links a Django user to the owner id that all their documents, sources and
    preferences are stored under. Guests use their browser's UUID in the same
    place, which is how guest data can move into an account."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    owner_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)


class AuthToken(models.Model):
    """A signed-in device. Only a hash of the token is stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="auth_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    device = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
