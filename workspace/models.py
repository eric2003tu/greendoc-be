from django.db import models


class Preferences(models.Model):
    """Per-browser settings (Options dialog, theme, default design)."""

    client_id = models.UUIDField(unique=True)
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class Source(models.Model):
    """A bibliography source in a browser's library (References tab)."""

    client_id = models.UUIDField(db_index=True)
    tag = models.CharField(max_length=40)
    type = models.CharField(max_length=40, default="Book")
    author = models.CharField(max_length=200)
    title = models.CharField(max_length=300)
    year = models.CharField(max_length=10, blank=True, default="")
    publisher = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["client_id", "tag"], name="unique_source_tag_per_client")
        ]


class SourcesSeeded(models.Model):
    """Marks that the default sources were copied into a browser's library, so
    deleting them all does not bring the defaults back."""

    client_id = models.UUIDField(unique=True)
