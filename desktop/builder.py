"""Builds the Windows installer on this server and publishes it: the engine page's one button.

The server needs the app's source code (GREENDOC_APP_SOURCE, by default the greenleaf-word-studio folder
next to this project), Node, Rust and the update signing key on the same computer. The build raises the
version number, signs the installer, and this module then publishes it exactly as an upload would.
Nothing the administrator types reaches the command that runs."""
import json
import os
import shutil
import subprocess
import threading
import time
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


def unavailable_reason():
    """Why this server cannot build, or "" when it can."""
    source = source_dir()
    if source is None or not (source / "src-tauri" / "tauri.conf.json").is_file():
        return "This server does not have the app's source code, so it cannot build the installer itself."
    if not shutil.which("npm"):
        return "Node.js (npm) is not installed on this server, so it cannot build the installer."
    return ""


def status():
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
