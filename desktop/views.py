import hmac

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import FileResponse, HttpResponse, HttpResponseRedirect, JsonResponse

from core.http import ApiError, api_view, require_admin

from . import builder, services
from .models import DesktopRelease


@api_view(["GET"])
def latest_manifest(request):
    """What the installed app asks: is there a newer version? (Tauri's update file.) Public.

    204 means "nothing newer". The app passes ?current=<its version>, so an old copy is offered the new
    version and an up-to-date copy is not."""
    release = services.latest_published()
    current = request.GET.get("current", "")
    if release is None or (
        services.VERSION_RE.match(current)
        and services.version_key(current) >= services.version_key(release.version)
    ):
        return HttpResponse(status=204)
    return JsonResponse(
        {
            "version": release.version,
            "notes": release.notes,
            "pub_date": release.created_at.isoformat(),
            "platforms": {
                services.PLATFORM: {
                    "signature": release.signature,
                    "url": request.build_absolute_uri(f"/api/desktop/download/{release.version}/"),
                }
            },
        }
    )


@api_view(["GET"])
def download(request, version):
    """The installer file. Public, so the website's Install button and the updater can both use it.
    `latest` means the newest published version."""
    if version == "latest":
        release = services.latest_published()
    else:
        release = DesktopRelease.objects.filter(version=version, published=True).first()
    if release is None:
        raise ApiError("There is no installer to download yet.", status=404, code="not_found")
    if not isinstance(release.installer.storage, FileSystemStorage):
        # Installers kept in a bucket (Render has no permanent disk): send the caller there with a short-lived
        # signed link, so a 45 MB file never passes through this small server.
        return HttpResponseRedirect(release.installer.url)
    return FileResponse(
        release.installer.open("rb"),
        as_attachment=True,
        filename=f"GreenDoc_{release.version}_x64-setup.exe",
        content_type="application/octet-stream",
    )


@api_view(["GET"])
def latest_info(request):
    """For the website: is there an installer, and which version? Public."""
    release = services.latest_published()
    if release is None:
        return JsonResponse({"available": False})
    return JsonResponse(
        {"available": True, "version": release.version, "size": release.size, "notes": release.notes}
    )


def _publisher(request):
    """Who is publishing: the GitHub build (it presents the shared publish key) or a signed-in administrator."""
    key = settings.DESKTOP_PUBLISH_KEY
    given = request.META.get("HTTP_X_PUBLISH_KEY", "")
    if len(key) >= 24 and given and hmac.compare_digest(given.encode(), key.encode()):
        return "GitHub build"
    return require_admin(request).email


@api_view(["GET", "POST"])
def releases(request):
    """Administrators: the published versions, and publish a new one (installer + latest.json)."""
    if request.method == "POST":
        publisher = _publisher(request)
        installer = request.FILES.get("installer")
        manifest = request.FILES.get("manifest")
        if installer is None or manifest is None:
            raise ApiError(
                "Choose both files: the installer (.exe) and latest.json from the same build.",
                code="missing_files",
            )
        release = services.publish(installer, manifest, request.POST.get("notes", ""), publisher)
        latest = services.latest_published()
        return JsonResponse(services.dto(release, latest.version if latest else ""), status=201)
    require_admin(request)
    latest = services.latest_published()
    latest_version = latest.version if latest else ""
    rows = sorted(DesktopRelease.objects.all(), key=lambda r: services.version_key(r.version), reverse=True)
    return JsonResponse({"releases": [services.dto(r, latest_version) for r in rows]})


@api_view(["GET", "POST"])
def build(request):
    """Administrators: build a new installer on this server and publish it (POST), or see how that is going (GET)."""
    admin = require_admin(request)
    if request.method == "POST":
        builder.start(admin.email)
        return JsonResponse(builder.status(), status=202)
    return JsonResponse(builder.status())


@api_view(["DELETE"])
def release(request, version):
    """Administrators: take a version down. Apps that already have it keep working."""
    require_admin(request)
    row = DesktopRelease.objects.filter(version=version).first()
    if row is None:
        raise ApiError("No such version.", status=404, code="not_found")
    row.installer.delete(save=False)
    row.delete()
    return HttpResponse(status=204)
