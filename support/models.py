from django.db import models


class SupportMessage(models.Model):
    """Help > Contact Support. Kept in the database as well as emailed, so none are lost."""

    name = models.CharField(max_length=100, blank=True, default="")
    email = models.EmailField()
    subject = models.CharField(max_length=150)
    message = models.TextField(max_length=5000)
    user_id = models.IntegerField(null=True, blank=True)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
