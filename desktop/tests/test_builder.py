import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.tokens import issue_token
from core.http import ApiError
from desktop import builder
from desktop.models import DesktopRelease

from .test_releases import SIGNATURE, installer, manifest

User = get_user_model()
MEDIA = tempfile.mkdtemp(prefix="greendoc-test-media-")
SOURCE = Path(tempfile.mkdtemp(prefix="greendoc-test-source-"))


def fake_build(version):
    """Stands in for `npm run package:desktop`: leaves the files the real build leaves."""

    def build(source, notes):
        folder = Path(source) / builder.PLATFORM_DIR
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"GreenDoc_{version}_x64-setup.exe").write_bytes(b"MZ" + b"\0" * 1_200_000)
        manifest = {"version": version, "notes": notes, "platforms": {"windows-x86_64": {"signature": SIGNATURE, "url": ""}}}
        (folder / "latest.json").write_text(json.dumps(manifest))

    return build


@override_settings(MEDIA_ROOT=MEDIA, DESKTOP_APP_SOURCE=str(SOURCE))
class BuilderTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (SOURCE / "src-tauri").mkdir(parents=True, exist_ok=True)
        (SOURCE / "src-tauri" / "tauri.conf.json").write_text("{}")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)
        shutil.rmtree(SOURCE, ignore_errors=True)

    def setUp(self):
        cache.clear()
        builder.reset()
        shutil.rmtree(MEDIA, ignore_errors=True)
        shutil.rmtree(SOURCE / "src-tauri" / "target", ignore_errors=True)
        admin = User.objects.create_user("boss@example.com", "boss@example.com", "pw", is_staff=True)
        self.admin = {"HTTP_AUTHORIZATION": f"Bearer {issue_token(admin)[0]}"}
        user = User.objects.create_user("user@example.com", "user@example.com", "pw")
        self.user = {"HTTP_AUTHORIZATION": f"Bearer {issue_token(user)[0]}"}
        patcher = mock.patch("desktop.builder.shutil.which", return_value="npm")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_administrators_can_build(self):
        self.assertEqual(self.client.post("/api/desktop/build/").status_code, 401)
        self.assertEqual(self.client.post("/api/desktop/build/", **self.user).status_code, 403)
        self.assertEqual(self.client.get("/api/desktop/build/", **self.user).status_code, 403)

    def test_status_before_any_build(self):
        body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertTrue(body["available"])
        self.assertEqual(body["status"], "idle")

    @override_settings(DESKTOP_APP_SOURCE="")
    def test_a_server_without_the_source_says_so_and_refuses(self):
        body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertFalse(body["available"])
        self.assertIn("source code", body["reason"])
        self.assertEqual(self.client.post("/api/desktop/build/", **self.admin).status_code, 409)

    def test_a_build_publishes_the_new_version_by_itself(self):
        with mock.patch("desktop.builder._build", fake_build("0.2.0")):
            builder.start("boss@example.com", background=False)
        body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["version"], "0.2.0")
        release = DesktopRelease.objects.get(version="0.2.0")
        self.assertEqual(release.created_by, "boss@example.com")
        self.assertTrue(release.notes)
        # An installed 0.1.0 app is now offered it, and the website can download it.
        offered = self.client.get("/api/desktop/latest.json?current=0.1.0").json()
        self.assertEqual(offered["version"], "0.2.0")
        self.assertEqual(self.client.get("/api/desktop/download/latest/").status_code, 200)

    def test_a_failed_build_reports_why_and_publishes_nothing(self):
        def broken(source, notes):
            raise ApiError("The build failed. no linker", code="build_failed")

        with mock.patch("desktop.builder._build", broken):
            builder.start("boss@example.com", background=False)
        body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertEqual(body["status"], "failed")
        self.assertIn("no linker", body["message"])
        self.assertEqual(DesktopRelease.objects.count(), 0)

    def test_building_the_same_version_twice_fails_clearly(self):
        with mock.patch("desktop.builder._build", fake_build("0.2.0")):
            builder.start("boss@example.com", background=False)
            builder.start("boss@example.com", background=False)
        body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertEqual(body["status"], "failed")
        self.assertIn("already been published", body["message"])
        self.assertEqual(DesktopRelease.objects.count(), 1)

    def test_a_second_press_while_building_does_not_start_another(self):
        with mock.patch("desktop.builder.threading.Thread") as thread:
            builder.start("boss@example.com")
            builder.start("boss@example.com")
        self.assertEqual(thread.call_count, 1)
        self.assertEqual(builder.status()["status"], "building")


@override_settings(MEDIA_ROOT=MEDIA)
class RemoteStorageTests(TestCase):
    """With installers in a bucket, downloads redirect to a signed link instead of streaming through the server."""

    def test_download_redirects_when_the_installer_is_stored_remotely(self):
        from django.core.files.storage import Storage

        release = DesktopRelease.objects.create(
            version="0.3.0", signature=SIGNATURE, size=1_300_000, installer="installers/GreenDoc_0.3.0_x64-setup.exe"
        )

        class FakeBucket(Storage):
            def url(self, name):
                return f"https://bucket.example/{name}?signed=1"

        with mock.patch.object(DesktopRelease._meta.get_field("installer"), "storage", FakeBucket()):
            response = self.client.get("/api/desktop/download/0.3.0/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://bucket.example/"))


def run_info(status="in_progress", conclusion=None, created=None):
    from datetime import datetime, timedelta, timezone

    stamp = (created or datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
    return {"workflow_runs": [{"status": status, "conclusion": conclusion, "created_at": stamp,
                               "run_started_at": stamp, "updated_at": stamp, "html_url": "https://github.com/o/r/actions/runs/1"}]}


@override_settings(
    MEDIA_ROOT=MEDIA,
    DESKTOP_APP_SOURCE="",
    GITHUB_TOKEN="ghp_fake",
    GITHUB_REPO="owner/repo",
    GITHUB_WORKFLOW="desktop-release.yml",
    GITHUB_REF="main",
    DESKTOP_PUBLISH_KEY="k" * 32,
)
class GithubBuildTests(TestCase):
    """The server has no Rust (Render): the button starts a GitHub workflow that builds and uploads the installer."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        builder.reset()
        shutil.rmtree(MEDIA, ignore_errors=True)
        admin = User.objects.create_user("boss@example.com", "boss@example.com", "pw", is_staff=True)
        self.admin = {"HTTP_AUTHORIZATION": f"Bearer {issue_token(admin)[0]}"}

    def test_a_server_connected_to_github_can_build_without_the_source(self):
        with mock.patch("desktop.builder._github", return_value={"workflow_runs": []}):
            body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertTrue(body["available"])
        self.assertEqual(body["status"], "idle")

    def test_pressing_the_button_starts_the_workflow_on_the_configured_branch(self):
        calls = []

        def github(method, path, body=None):
            calls.append((method, path, body))
            return {"workflow_runs": []}

        with mock.patch("desktop.builder._github", github):
            response = self.client.post("/api/desktop/build/", **self.admin)
        self.assertEqual(response.status_code, 202)
        post = [c for c in calls if c[0] == "POST"]
        self.assertEqual(post, [("POST", "/repos/owner/repo/actions/workflows/desktop-release.yml/dispatches", {"ref": "main"})])
        # GitHub has not listed the new run yet, but the page already shows it is starting
        self.assertEqual(response.json()["status"], "building")

    def test_a_running_build_is_reported_and_not_started_twice(self):
        calls = []

        def github(method, path, body=None):
            calls.append(method)
            return run_info("in_progress")

        with mock.patch("desktop.builder._github", github):
            builder.start("boss@example.com")
            body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertEqual(body["status"], "building")
        self.assertGreaterEqual(body["elapsedSeconds"], 80)
        self.assertNotIn("POST", calls)

    def test_a_finished_build_reports_the_published_version(self):
        DesktopRelease.objects.create(version="0.4.0", signature=SIGNATURE, size=1_300_000, installer="x.exe")
        with mock.patch("desktop.builder._github", return_value=run_info("completed", "success")):
            body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["version"], "0.4.0")

    def test_a_failed_build_points_to_the_run(self):
        with mock.patch("desktop.builder._github", return_value=run_info("completed", "failure")):
            body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertEqual(body["status"], "failed")
        self.assertIn("https://github.com/o/r/actions/runs/1", body["message"])

    def test_a_bad_token_is_explained(self):
        def refused(method, path, body=None):
            raise ApiError(builder._github_error(401), status=502, code="github_error")

        with mock.patch("desktop.builder._github", refused):
            body = self.client.get("/api/desktop/build/", **self.admin).json()
        self.assertEqual(body["status"], "failed")
        self.assertIn("token", body["message"])

    # --- the workflow uploads the installer with the shared publish key ---

    def upload(self, key=None, version="0.5.0"):
        headers = {"HTTP_X_PUBLISH_KEY": key} if key is not None else {}
        data = {"installer": installer(version), "manifest": manifest(version), "notes": "From GitHub"}
        return self.client.post("/api/desktop/releases/", data, **headers)

    def test_the_github_build_can_publish_with_the_key(self):
        response = self.upload("k" * 32)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(DesktopRelease.objects.get(version="0.5.0").created_by, "Build computer")

    def test_a_wrong_or_missing_key_cannot_publish(self):
        self.assertEqual(self.upload("x" * 32).status_code, 401)
        self.assertEqual(self.upload().status_code, 401)
        self.assertEqual(DesktopRelease.objects.count(), 0)

    @override_settings(DESKTOP_PUBLISH_KEY="short")
    def test_a_short_key_is_never_accepted(self):
        self.assertEqual(self.upload("short").status_code, 401)

    def test_the_key_does_not_open_the_admin_list(self):
        response = self.client.get("/api/desktop/releases/", HTTP_X_PUBLISH_KEY="k" * 32)
        self.assertEqual(response.status_code, 401)


@override_settings(MEDIA_ROOT=MEDIA, DESKTOP_APP_SOURCE="", GITHUB_TOKEN="", GITHUB_REPO="", DESKTOP_PUBLISH_KEY="a" * 32)
class AgentBuildTests(TestCase):
    """No GitHub, no source on the server: a release agent on the build computer does the building."""

    KEY = {"HTTP_X_PUBLISH_KEY": "a" * 32}

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        builder.reset()
        admin = User.objects.create_user("boss@example.com", "boss@example.com", "pw", is_staff=True)
        self.admin = {"HTTP_AUTHORIZATION": f"Bearer {issue_token(admin)[0]}"}

    def status(self):
        return self.client.get("/api/desktop/build/", **self.admin).json()

    def test_the_button_is_available_and_waits_when_the_build_computer_is_off(self):
        self.assertTrue(self.status()["available"])
        self.assertEqual(self.client.post("/api/desktop/build/", **self.admin).status_code, 202)
        body = self.status()
        self.assertEqual(body["status"], "building")
        self.assertIn("off", body["lastLine"])

    def test_the_agent_picks_up_a_request_builds_and_reports(self):
        self.client.post("/api/desktop/build/", **self.admin)
        job = self.client.get("/api/desktop/agent/next/", **self.KEY).json()["request"]
        self.assertIsNotNone(job)
        self.assertEqual(self.client.get("/api/desktop/agent/next/", **self.KEY).json(), {"request": None})  # taken
        self.client.post(f"/api/desktop/agent/{job['id']}/", {"line": "Compiling greendoc"}, content_type="application/json", **self.KEY)
        self.assertEqual(self.status()["lastLine"], "Compiling greendoc")
        # the agent uploads with the same key, then says it is done
        upload = self.client.post("/api/desktop/releases/", {"installer": installer("0.6.0"), "manifest": manifest("0.6.0"), "notes": "x"}, **self.KEY)
        self.assertEqual(upload.status_code, 201)
        self.client.post(f"/api/desktop/agent/{job['id']}/", {"status": "done"}, content_type="application/json", **self.KEY)
        body = self.status()
        self.assertEqual((body["status"], body["version"]), ("done", "0.6.0"))

    def test_the_agent_can_report_a_failure(self):
        self.client.post("/api/desktop/build/", **self.admin)
        job = self.client.get("/api/desktop/agent/next/", **self.KEY).json()["request"]
        self.client.post(f"/api/desktop/agent/{job['id']}/", {"status": "failed", "message": "linker not found"}, content_type="application/json", **self.KEY)
        body = self.status()
        self.assertEqual(body["status"], "failed")
        self.assertIn("linker", body["message"])

    def test_a_second_press_while_one_is_waiting_does_not_queue_another(self):
        from desktop.models import BuildRequest

        self.client.post("/api/desktop/build/", **self.admin)
        self.client.post("/api/desktop/build/", **self.admin)
        self.assertEqual(BuildRequest.objects.count(), 1)

    def test_only_the_key_opens_the_agent_endpoints(self):
        self.assertEqual(self.client.get("/api/desktop/agent/next/").status_code, 401)
        self.assertEqual(self.client.get("/api/desktop/agent/next/", HTTP_X_PUBLISH_KEY="b" * 32).status_code, 401)
        self.assertEqual(self.client.get("/api/desktop/agent/next/", **self.admin).status_code, 401)
        self.assertEqual(self.client.post("/api/desktop/agent/1/", {}, content_type="application/json").status_code, 401)

    def test_a_build_that_goes_silent_is_marked_failed(self):
        from datetime import timedelta

        from django.utils import timezone

        from desktop.models import BuildRequest

        row = BuildRequest.objects.create(status=BuildRequest.BUILDING, started_at=timezone.now())
        BuildRequest.objects.filter(id=row.id).update(updated_at=timezone.now() - timedelta(hours=1))
        body = self.status()
        self.assertEqual(body["status"], "failed")
        self.assertIn("stopped answering", body["message"])
