"""Publishing and finding versions of the Windows app."""
import base64
import binascii
import json
import re

from core.http import ApiError

from .models import DesktopRelease

PLATFORM = "windows-x86_64"
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
INSTALLER_NAME_RE = re.compile(r"^GreenDoc_(\d{1,4}\.\d{1,4}\.\d{1,4})_x64-setup\.exe$")
MIN_INSTALLER = 1_000_000
MAX_INSTALLER = 300 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
MAX_NOTES = 2000


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def latest_published():
    rows = DesktopRelease.objects.filter(published=True)
    return max(rows, key=lambda r: version_key(r.version), default=None)


def read_manifest(upload):
    """The version, signature and notes from the latest.json the build wrote."""
    if upload.size > MAX_MANIFEST:
        raise ApiError("That does not look like latest.json (too large).", code="bad_manifest")
    try:
        data = json.loads(upload.read().decode("utf-8"))
        version = data["version"]
        signature = data["platforms"][PLATFORM]["signature"]
    except (ValueError, UnicodeDecodeError, KeyError, TypeError):
        raise ApiError(
            "latest.json is not a GreenDoc update file. Upload the latest.json the build produced.",
            code="bad_manifest",
        )
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise ApiError("The version in latest.json is not valid (expected 1.2.3).", code="bad_manifest")
    if not isinstance(signature, str) or not _looks_like_signature(signature):
        raise ApiError("The signature in latest.json is missing or damaged.", code="bad_manifest")
    notes = data.get("notes", "")
    return version, signature.strip(), notes if isinstance(notes, str) else ""


def _looks_like_signature(text):
    try:
        return base64.b64decode(text.strip(), validate=True).startswith(b"untrusted comment:")
    except (binascii.Error, ValueError):
        return False


def check_installer(upload, version):
    match = INSTALLER_NAME_RE.match(upload.name or "")
    if not match:
        raise ApiError(
            "The installer should be named like GreenDoc_0.1.1_x64-setup.exe, as the build made it.",
            code="bad_installer",
        )
    if match.group(1) != version:
        raise ApiError(
            f"The installer is version {match.group(1)} but latest.json says {version}. "
            "Upload the two files from the same build.",
            code="version_mismatch",
        )
    if upload.size < MIN_INSTALLER or upload.size > MAX_INSTALLER:
        raise ApiError("The installer has an unexpected size.", code="bad_installer")
    if upload.read(2) != b"MZ":
        raise ApiError("That file is not a Windows installer.", code="bad_installer")
    upload.seek(0)


def publish(installer, manifest, notes, email):
    version, signature, manifest_notes = read_manifest(manifest)
    check_installer(installer, version)
    if DesktopRelease.objects.filter(version=version).exists():
        raise ApiError(f"Version {version} has already been published.", status=409, code="version_exists")
    newest = latest_published()
    if newest and version_key(version) <= version_key(newest.version):
        raise ApiError(
            f"Version {version} is not newer than the published {newest.version}. "
            "Build again (the build raises the number for you) and upload that.",
            status=409,
            code="not_newer",
        )
    return DesktopRelease.objects.create(
        version=version,
        notes=(notes or manifest_notes).strip()[:MAX_NOTES],
        signature=signature,
        installer=installer,
        size=installer.size,
        created_by=email,
    )


def dto(release, latest_version):
    return {
        "version": release.version,
        "notes": release.notes,
        "size": release.size,
        "createdAt": release.created_at.isoformat(),
        "createdBy": release.created_by,
        "published": release.published,
        "isLatest": release.version == latest_version,
    }
