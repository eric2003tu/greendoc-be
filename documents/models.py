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
    # Layout and view state from the editor store: pageSize, orientation, margins,
    # columns, header/footer, watermark, trackChanges, theme, language ...
    settings = models.JSONField(default=dict, blank=True)
    # File > Info > Properties: subject, author, keywords, company, category.
    properties = models.JSONField(default=dict, blank=True)
    is_protected = models.BooleanField(default=False)  # Review > Restrict Editing
    marked_final = models.BooleanField(default=False)  # File > Info > Mark as Final
    source_name = models.CharField(max_length=255, blank=True, default="")  # file it was opened from
    in_recent = models.BooleanField(default=True)  # File > Open > Recent
    share_token = models.CharField(max_length=40, unique=True, null=True, blank=True)
    # "view": anyone with the link can read. "edit": anyone with the link can also change title and text.
    share_permission = models.CharField(max_length=4, default="view")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.id})"


class Comment(models.Model):
    """Review pane comment. `cid` is the id embedded in the HTML (data-cid)."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="comments")
    cid = models.CharField(max_length=64)
    author = models.CharField(max_length=100)
    initials = models.CharField(max_length=4)
    text = models.TextField(max_length=5000)
    quote = models.TextField(blank=True, default="", max_length=2000)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["document", "cid"], name="unique_comment_cid")]


class CommentReply(models.Model):
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="replies")
    rid = models.CharField(max_length=64)
    author = models.CharField(max_length=100)
    initials = models.CharField(max_length=4)
    text = models.TextField(max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class Revision(models.Model):
    """A tracked change. `rid` is the id embedded in the HTML (data-rev)."""

    KINDS = [("insert", "insert"), ("delete", "delete"), ("format", "format")]

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="revisions")
    rid = models.CharField(max_length=64)
    kind = models.CharField(max_length=10, choices=KINDS)
    author = models.CharField(max_length=100)
    initials = models.CharField(max_length=4)
    text = models.TextField(blank=True, default="", max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["document", "rid"], name="unique_revision_rid")]


class MergeDataset(models.Model):
    """Mailings > Select Recipients."""

    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="merge_dataset")
    fields = models.JSONField(default=list)
    records = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)


class DocumentVersion(models.Model):
    """Version history: automatic snapshots on save plus manual ones."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    title = models.CharField(max_length=200)
    html = models.TextField(blank=True, default="")
    settings = models.JSONField(default=dict, blank=True)
    label = models.CharField(max_length=100, blank=True, default="")
    automatic = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
