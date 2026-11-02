"""OpenAPI description of the Windows app's update endpoints. Merged into the spec by core.openapi.build_spec."""

from .openapi_extra import BOOL, INT, STR, op, path_param, query, s_obj

BINARY = {"type": "string", "format": "binary"}


def build_schemas():
    return {
        "DesktopRelease": s_obj(
            {
                "version": {**STR, "description": "1.2.3"},
                "notes": STR,
                "size": {**INT, "description": "Installer size in bytes."},
                "createdAt": {"type": "string", "format": "date-time"},
                "createdBy": STR,
                "published": BOOL,
                "isLatest": BOOL,
            }
        ),
        "DesktopReleaseList": s_obj({"releases": {"type": "array", "items": {"$ref": "#/components/schemas/DesktopRelease"}}}),
        "DesktopBuild": s_obj(
            {
                "available": {**BOOL, "description": "False when this server cannot build (see `reason`)."},
                "reason": STR,
                "status": {**STR, "enum": ["idle", "building", "done", "failed"]},
                "version": {**STR, "description": "The version published by the last successful build."},
                "message": STR,
                "elapsedSeconds": INT,
                "lastLine": {**STR, "description": "The newest line of the build's output."},
            }
        ),
        "DesktopLatest": s_obj(
            {"available": BOOL, "version": STR, "size": INT, "notes": STR},
            description="`available` is false until a version is published.",
        ),
        "DesktopUpdateManifest": s_obj(
            {
                "version": STR,
                "notes": STR,
                "pub_date": {"type": "string", "format": "date-time"},
                "platforms": {
                    "type": "object",
                    "additionalProperties": s_obj({"signature": STR, "url": {**STR, "format": "uri"}}),
                },
            },
            description="Tauri's update file.",
        ),
    }


def build_paths():
    return {
        "/api/desktop/latest.json": {
            "get": op("Desktop app", "Is there a newer Windows app?", "getDesktopUpdate",
                      "What the installed app asks. Public. Answers 204 when nothing newer is published. Pass `current` (the "
                      "version the app is running) so an up-to-date copy is not offered its own version.",
                      secured=False, ok="DesktopUpdateManifest", ok_description="A newer version exists.",
                      params=[query("current", "The version the app is running, such as 0.1.0.")]),
        },
        "/api/desktop/latest/": {
            "get": op("Desktop app", "Is an installer available?", "getDesktopLatest",
                      "For the website's Install button. Public.", secured=False, ok="DesktopLatest"),
        },
        "/api/desktop/download/{version}/": {
            "parameters": [path_param("version", "A version such as 0.2.0, or `latest`.")],
            "get": op("Desktop app", "Download the Windows installer", "downloadDesktopInstaller",
                      "The installer file. Public.", secured=False, ok=None, ok_code="200",
                      ok_description="The installer (.exe).", errors=("404",)),
        },
        "/api/desktop/releases/": {
            "get": op("Desktop app", "List published versions", "listDesktopReleases",
                      "Administrators only.", ok="DesktopReleaseList"),
            "post": op("Desktop app", "Publish a new version", "publishDesktopRelease",
                       "Administrators only. Upload the installer and the `latest.json` the build wrote, from the same "
                       "build. The version must be higher than the newest published one.",
                       ok="DesktopRelease", ok_code="201", ok_description="Published.", errors=("400", "409"),
                       multipart={"multipart/form-data": {"schema": {
                           "type": "object", "required": ["installer", "manifest"],
                           "properties": {"installer": BINARY, "manifest": BINARY, "notes": STR}}}}),
        },
        "/api/desktop/build/": {
            "get": op("Desktop app", "How is the build going?", "getDesktopBuild",
                      "Administrators only. Whether this server can build the installer, and the state of the current or "
                      "last build.", ok="DesktopBuild"),
            "post": op("Desktop app", "Build and publish a new version", "startDesktopBuild",
                       "Administrators only. The server builds the installer from its copy of the app, raises the version "
                       "number, signs it and publishes it. Takes several minutes: poll GET for progress. Nothing is "
                       "uploaded or typed. Does nothing if a build is already running.",
                       ok="DesktopBuild", ok_code="202", ok_description="The build has started.", errors=("409",)),
        },
        "/api/desktop/releases/{version}/": {
            "parameters": [path_param("version", "A published version such as 0.2.0.")],
            "delete": op("Desktop app", "Take a version down", "deleteDesktopRelease",
                         "Administrators only. Apps that already have this version keep working.",
                         ok=None, ok_code="204", ok_description="Removed.", errors=("404",)),
        },
    }


def extend(spec):
    spec["paths"].update(build_paths())
    spec["components"]["schemas"].update(build_schemas())
    spec["tags"].append({"name": "Desktop app", "description": "Publishing and updating the Windows app."})
    return spec
