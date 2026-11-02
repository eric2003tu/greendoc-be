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


class BuildRequest(models.Model):
    """"Publish update" pressed on the engine page, waiting for the build computer's release agent.

    Kept in the database (not memory) so it survives the server sleeping or restarting."""

    REQUESTED, BUILDING, DONE, FAILED = "requested", "building", "done", "failed"
    STATUSES = [(REQUESTED, "Waiting"), (BUILDING, "Building"), (DONE, "Done"), (FAILED, "Failed")]

    status = models.CharField(max_length=10, choices=STATUSES, default=REQUESTED)
    requested_by = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.CharField(max_length=20, blank=True)
    message = models.TextField(blank=True)
    last_line = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-id"]
