from django.db import models


def installer_filename(version, platform):
    suffix = {
        "windows-x86_64": "x64-setup.exe",
        "darwin-aarch64": "aarch64.dmg",
        "darwin-x86_64": "x64.dmg",
    }.get(platform)
    if suffix is None:
        raise ValueError(f"Unsupported desktop platform: {platform}")
    return f"GreenDoc_{version}_{suffix}"


def installer_path(release, filename):
    return f"desktop/{release.version}/{installer_filename(release.version, release.platform)}"


def updater_bundle_path(release, filename):
    return f"desktop/{release.version}/{installer_filename(release.version, release.platform)}.app.tar.gz"


class DesktopRelease(models.Model):
    """One published version of the desktop app for one platform.

    The installer and updater bundle are signed on the build computer; the app checks the signature
    against the public key inside it before installing."""

    WINDOWS_X64 = "windows-x86_64"
    MACOS_ARM64 = "darwin-aarch64"
    MACOS_X64 = "darwin-x86_64"
    PLATFORMS = [
        (WINDOWS_X64, "Windows x64"),
        (MACOS_ARM64, "macOS Apple Silicon"),
        (MACOS_X64, "macOS Intel"),
    ]

    version = models.CharField(max_length=20)
    platform = models.CharField(max_length=32, choices=PLATFORMS, default=WINDOWS_X64)
    notes = models.TextField(blank=True)
    signature = models.TextField()
    installer = models.FileField(upload_to=installer_path, max_length=200)
    updater_bundle = models.FileField(upload_to=updater_bundle_path, max_length=240, blank=True)
    size = models.BigIntegerField()
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.EmailField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["version", "platform"], name="desktop_release_version_platform_uniq"),
        ]

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
