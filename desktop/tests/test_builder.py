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

from .test_releases import SIGNATURE

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
