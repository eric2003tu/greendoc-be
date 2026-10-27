import secrets
import uuid

from django.db import models


def new_share_token():
    return secrets.token_urlsafe(16)


class Document(models.Model):
    """A document owned by an anonymous browser (client_id). The id matches the
    draft id the frontend already generates, so a local draft can be synced by
    PUTting it under the same id."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_id = models.UUIDField(db_index=True)
    title = models.CharField(max_length=200, default="Document1")
    template = models.CharField(max_length=64, default="blank")
    html = models.TextField(blank=True, default="")
    share_token = models.CharField(max_length=40, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.id})"
