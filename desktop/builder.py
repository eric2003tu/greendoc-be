"""Builds the Windows installer and publishes it: the engine page's one button. Two ways, chosen by settings.

On GitHub (a host with no Rust, such as Render): GREENDOC_GITHUB_TOKEN and GREENDOC_GITHUB_REPO are set. The
button starts the repository's "desktop-release" workflow; GitHub's free Windows machine builds and signs the
installer and uploads it to this server with GREENDOC_PUBLISH_KEY. This module starts it and reports how it goes.

On this computer: the server has the app's source code (GREENDOC_APP_SOURCE, by default the greenleaf-word-studio
folder next to this project), Node, Rust and the signing key. The build raises the version number, signs the
installer, and this module publishes it exactly as an upload would.

Nothing the administrator types reaches a command or a request."""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from collections import deque
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import connection

from core.http import ApiError

from . import services
from .models import DesktopRelease

PLATFORM_DIR = "src-tauri/target/release/bundle/nsis"
BUILD_TIMEOUT = 45 * 60

_lock = threading.Lock()
_state = {"status": "idle", "started": 0.0, "finished": 0.0, "version": "", "message": "", "log": deque(maxlen=200)}


def source_dir():
    configured = getattr(settings, "DESKTOP_APP_SOURCE", "")
    return Path(configured) if configured else None


def github_configured():
    return bool(getattr(settings, "GITHUB_TOKEN", "") and getattr(settings, "GITHUB_REPO", ""))


def unavailable_reason():
    """Why this server cannot build, or "" when it can."""
    if github_configured():
        return ""
    source = source_dir()
    if source is None or not (source / "src-tauri" / "tauri.conf.json").is_file():
        return (
            "This server cannot build the installer: it has neither the app's source code nor a GitHub connection "
            "(GREENDOC_GITHUB_TOKEN and GREENDOC_GITHUB_REPO)."
        )
    if not shutil.which("npm"):
        return "Node.js (npm) is not installed on this server, so it cannot build the installer."
    return ""


def _github_error(code):
    if code in (401, 403):
        return "GitHub refused this server's token. It needs permission to read and run Actions on the repository."
    if code == 404:
        return (
            "GitHub cannot find the repository or its desktop-release workflow. Check GREENDOC_GITHUB_REPO, and that "
            ".github/workflows/desktop-release.yml has been pushed."
        )
    if code == 422:
        return "GitHub rejected the request. Check GREENDOC_GITHUB_REF (the branch name)."
    return f"GitHub answered with an error ({code}). Try again in a moment."


def _github(method, path, body=None):
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "greendoc-server",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raise ApiError(_github_error(error.code), status=502, code="github_error")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ApiError("Could not reach GitHub. Try again in a moment.", status=502, code="github_unreachable")


def _workflow_path():
    return f"/repos/{settings.GITHUB_REPO}/actions/workflows/{settings.GITHUB_WORKFLOW}"


def _seconds_since(iso):
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return 0
    return max(0, int((datetime.now(timezone.utc) - moment).total_seconds()))


def _github_status():
    """The newest run of the release workflow, in the same shape as a local build."""
    base = {"available": True, "reason": "", "version": "", "message": "", "elapsedSeconds": 0, "lastLine": ""}
    try:
        runs = _github("GET", f"{_workflow_path()}/runs?event=workflow_dispatch&per_page=1").get("workflow_runs", [])
    except ApiError as error:
        return {**base, "status": "failed", "message": error.message}
    with _lock:
        asked = _state["started"]
    just_asked = bool(asked) and time.time() - asked < 120
    run = runs[0] if runs else None
    # Asked a moment ago and GitHub has not listed the new run yet: the newest run is still an old one.
    if just_asked and (run is None or _seconds_since(run.get("created_at")) > time.time() - asked + 5):
        return {**base, "status": "building", "elapsedSeconds": int(time.time() - asked),
                "lastLine": "Waiting for GitHub to start the build…"}
    if run is None:
        return {**base, "status": "idle"}
    started = _seconds_since(run.get("run_started_at") or run.get("created_at"))
    if run["status"] != "completed":
        waiting = run["status"] in ("queued", "requested", "waiting", "pending")
        return {**base, "status": "building", "elapsedSeconds": started,
                "lastLine": "Waiting for a Windows machine on GitHub…" if waiting
                else "Building and signing the installer on GitHub…"}
    ran_for = max(0, started - _seconds_since(run.get("updated_at")))
    if run.get("conclusion") == "success":
        latest = services.latest_published()
        version = latest.version if latest else ""
        return {**base, "status": "done", "version": version, "elapsedSeconds": ran_for,
                "message": f"Version {version} is published. Installed apps will offer it when they refresh."}
    return {**base, "status": "failed", "elapsedSeconds": ran_for,
            "message": f"The build did not finish on GitHub. See why: {run.get('html_url', '')}"}


def status():
    if github_configured():
        return _github_status()
    reason = unavailable_reason()
    with _lock:
        running = _state["status"] == "building"
        end = time.time() if running else (_state["finished"] or _state["started"])
        return {
            "available": not reason,
            "reason": reason,
            "status": _state["status"],
            "version": _state["version"],
            "message": _state["message"],
            "elapsedSeconds": int(end - _state["started"]) if _state["started"] else 0,
            "lastLine": _state["log"][-1] if _state["log"] else "",
        }


def start(email, background=True):
    """Begin a build, unless one is already running (then that one is reported)."""
    reason = unavailable_reason()
    if reason:
        raise ApiError(reason, status=409, code="cannot_build")
    if github_configured():
        if status()["status"] == "building":
            return
        _github("POST", f"{_workflow_path()}/dispatches", {"ref": settings.GITHUB_REF})
        with _lock:
            _state["started"] = time.time()
        return
    with _lock:
        if _state["status"] == "building":
            return
        _state.update(status="building", started=time.time(), finished=0.0, version="", message="", log=deque(maxlen=200))
    if background:
        threading.Thread(target=run, args=(email, True), daemon=True).start()
    else:
        run(email)


def reset():
    """For tests."""
    with _lock:
        _state.update(status="idle", started=0.0, finished=0.0, version="", message="", log=deque(maxlen=200))


def _finish(status_, message="", version=""):
    with _lock:
        _state.update(status=status_, finished=time.time(), message=message, version=version)


def _note(line):
    line = line.strip()
    if line:
        with _lock:
            _state["log"].append(line[:300])


def automatic_notes(source):
    """What changed, from the project's commit messages since the last published version."""
    last = DesktopRelease.objects.order_by("-created_at").first()
    command = ["git", "-C", str(source), "log", "--no-merges", "--pretty=%s", "-n", "8"]
    if last:
        command.append(f"--since={last.created_at.isoformat()}")
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    lines = list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))
    return "\n".join(lines[:6]) or "Improvements and fixes."


def _environment():
    env = dict(os.environ)
    cargo = Path.home() / ".cargo" / "bin"
    if cargo.is_dir():
        env["PATH"] = f"{cargo}{os.pathsep}{env.get('PATH', '')}"
    env.pop("CI", None)  # Tauri reads CI as a flag and only accepts true/false
    return env


def _build(source, notes):
    """Runs the project's packaging script. Raises ApiError with a readable reason when it fails."""
    env = _environment()
    env["RELEASE_NOTES"] = notes
    process = subprocess.Popen(
        [shutil.which("npm"), "run", "package:desktop"],
        cwd=str(source),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    timer = threading.Timer(BUILD_TIMEOUT, process.kill)
    timer.start()
    try:
        for line in process.stdout:
            _note(line)
        code = process.wait()
    finally:
        timer.cancel()
    if code != 0:
        with _lock:
            tail = " | ".join(list(_state["log"])[-3:])
        raise ApiError(f"The build failed. {tail}"[:600], code="build_failed")


def _publish_built(source, email, notes):
    folder = Path(source) / PLATFORM_DIR
    try:
        version = json.loads((folder / "latest.json").read_text("utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        raise ApiError("The build finished but did not leave latest.json.", code="build_failed")
    installer_path = folder / f"GreenDoc_{version}_x64-setup.exe"
    if not installer_path.is_file():
        raise ApiError("The build finished but did not leave the installer.", code="build_failed")
    with open(installer_path, "rb") as installer_file, open(folder / "latest.json", "rb") as manifest_file:
        release = services.publish(
            File(installer_file, name=installer_path.name),
            File(manifest_file, name="latest.json"),
            notes,
            email,
        )
    return release.version


def run(email, close_connection=False):
    try:
        source = source_dir()
        notes = automatic_notes(source)
        _build(source, notes)
        version = _publish_built(source, email, notes)
        _finish("done", f"Version {version} is published. Installed apps will offer it when they refresh.", version)
    except ApiError as error:
        _finish("failed", error.message)
    except Exception:  # a build must never leave the page waiting forever
        _finish("failed", "The build stopped unexpectedly. Check the server's log.")
    finally:
        if close_connection:  # a build thread has its own database connection
            connection.close()
