"""Publishing and finding versions of the desktop app."""
import base64
import binascii
import json
import re

from core.http import ApiError

from .models import DesktopRelease, installer_filename

WINDOWS_X64 = DesktopRelease.WINDOWS_X64
MACOS_ARM64 = DesktopRelease.MACOS_ARM64
MACOS_X64 = DesktopRelease.MACOS_X64
PLATFORMS = {platform for platform, _label in DesktopRelease.PLATFORMS}
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
MIN_INSTALLER = 1_000_000
MAX_ARTIFACT = 300 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
MAX_NOTES = 2000


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def latest_published(platform=WINDOWS_X64):
    rows = DesktopRelease.objects.filter(published=True)
    if platform:
        rows = rows.filter(platform=platform)
    return max(rows, key=lambda r: version_key(r.version), default=None)


def platform_from_request(request, default=WINDOWS_X64):
    platform = request.GET.get("platform", default)
    if platform not in PLATFORMS and platform != "all":
        raise ApiError("Choose a supported desktop platform.", code="bad_platform")
    return platform


def read_manifest(upload):
    """The version, platform, signature and notes from the latest.json the build wrote."""
    if upload.size > MAX_MANIFEST:
        raise ApiError("That does not look like latest.json (too large).", code="bad_manifest")
    try:
        data = json.loads(upload.read().decode("utf-8"))
        version = data["version"]
        platforms = data["platforms"]
        platform = next(key for key in platforms if key in PLATFORMS)
        signature = platforms[platform]["signature"]
    except (ValueError, UnicodeDecodeError, KeyError, TypeError, StopIteration):
        raise ApiError(
            "latest.json is not a GreenDoc update file for a supported platform.",
            code="bad_manifest",
        )
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ApiError("The version in latest.json is not valid (expected 1.2.3).", code="bad_manifest")
    if not isinstance(signature, str) or not _looks_like_signature(signature):
        raise ApiError("The signature in latest.json is missing or damaged.", code="bad_manifest")
    notes = data.get("notes", "")
    return version, platform, signature.strip(), notes if isinstance(notes, str) else ""


def _looks_like_signature(text):
    try:
        return base64.b64decode(text.strip(), validate=True).startswith(b"untrusted comment:")
    except (binascii.Error, ValueError):
        return False


def _check_file_size(upload, lower=MIN_INSTALLER):
    if upload.size < lower or upload.size > MAX_ARTIFACT:
        raise ApiError("The installer has an unexpected size.", code="bad_installer")


def check_installer(upload, version, platform):
    filename = upload.name or ""
    suffix = {
        WINDOWS_X64: r"x64-setup\.exe",
        MACOS_ARM64: r"aarch64\.dmg",
        MACOS_X64: r"x64\.dmg",
    }[platform]
    match = re.fullmatch(rf"GreenDoc_(\d{{1,4}}\.\d{{1,4}}\.\d{{1,4}})_{suffix}", filename)
    if match and match.group(1) != version:
        raise ApiError(
            f"The installer is version {match.group(1)} but latest.json says {version}. "
            "Upload the two files from the same build.",
            code="version_mismatch",
        )
    if filename != installer_filename(version, platform):
        raise ApiError(
            f"The installer should be named {installer_filename(version, platform)}, as the build made it.",
            code="bad_installer",
        )
    _check_file_size(upload)
    if platform == WINDOWS_X64 and upload.read(2) != b"MZ":
        raise ApiError("That file is not a Windows installer.", code="bad_installer")
    if platform.startswith("darwin-"):
        upload.seek(-512, 2)
        if upload.read(4) != b"koly":
            raise ApiError("That file is not a macOS disk image.", code="bad_installer")
    upload.seek(0)


def check_updater_bundle(upload, platform):
    if platform.startswith("darwin-"):
        if upload is None:
            raise ApiError("Choose the signed .app.tar.gz update bundle too.", code="missing_files")
        _check_file_size(upload)
        if not (upload.name or "").endswith(".app.tar.gz") or upload.read(2) != b"\x1f\x8b":
            raise ApiError("That file is not a macOS updater bundle.", code="bad_installer")
        upload.seek(0)
    elif upload is not None:
        raise ApiError("Windows releases do not use a separate updater bundle.", code="bad_installer")


def publish(installer, manifest, notes, email, updater_bundle=None):
    version, platform, signature, manifest_notes = read_manifest(manifest)
    check_installer(installer, version, platform)
    check_updater_bundle(updater_bundle, platform)
    if DesktopRelease.objects.filter(version=version, platform=platform).exists():
        raise ApiError(
            f"Version {version} for {platform} has already been published.",
            status=409,
            code="version_exists",
        )
    newest = latest_published(platform)
    if newest and version_key(version) <= version_key(newest.version):
        raise ApiError(
            f"Version {version} is not newer than the published {newest.version} for {platform}. "
            "Build again and upload that.",
            status=409,
            code="not_newer",
        )
    return DesktopRelease.objects.create(
        version=version,
        platform=platform,
        notes=(notes or manifest_notes).strip()[:MAX_NOTES],
        signature=signature,
        installer=installer,
        updater_bundle=updater_bundle or "",
        size=installer.size,
        created_by=email,
    )


def dto(release, latest_version):
    return {
        "version": release.version,
        "platform": release.platform,
        "notes": release.notes,
        "size": release.size,
        "createdAt": release.created_at.isoformat(),
        "createdBy": release.created_by,
        "published": release.published,
        "isLatest": release.version == latest_version,
    }
