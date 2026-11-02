from django.db import models


def installer_path(release, filename):
    return f"desktop/{release.version}/GreenDoc_{release.version}_x64-setup.exe"


class DesktopRelease(models.Model):
    """One published version of the Windows app: the installer, its signature and what changed.

    The installer is signed on the build computer; the app checks that signature against the public key
    inside it before installing, so the server only stores and hands out what it was given."""

    version = models.CharField(max_length=20, unique=True)
    notes = models.TextField(blank=True)
    signature = models.TextField()
    installer = models.FileField(upload_to=installer_path, max_length=200)
    size = models.BigIntegerField()
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.EmailField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"GreenDoc {self.version}"
