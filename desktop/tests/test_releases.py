import base64
import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.tokens import issue_token
from desktop.models import DesktopRelease

User = get_user_model()
SIGNATURE = base64.b64encode(b"untrusted comment: signature from tauri secret key\nRUQ...").decode()
MEDIA = tempfile.mkdtemp(prefix="greendoc-test-media-")


def installer(version="0.2.0", size=1_200_000, name=None, magic=None, platform="windows-x86_64"):
    names = {
        "windows-x86_64": f"GreenDoc_{version}_x64-setup.exe",
        "darwin-aarch64": f"GreenDoc_{version}_aarch64.dmg",
        "darwin-x86_64": f"GreenDoc_{version}_x64.dmg",
    }
    if magic is None:
        magic = b"MZ" if platform == "windows-x86_64" else b"\0"
    data = bytearray(magic + b"\0" * (size - len(magic)))
    if platform.startswith("darwin-") and magic != b"PK":
        data[-512:-508] = b"koly"
    return SimpleUploadedFile(name or names[platform], bytes(data))


def manifest(version="0.2.0", signature=SIGNATURE, notes="", platform="windows-x86_64"):
    body = {
        "version": version,
        "notes": notes,
        "platforms": {platform: {"signature": signature, "url": "https://example.com/x.exe"}},
    }
    return SimpleUploadedFile("latest.json", json.dumps(body).encode())


def updater_bundle(name="GreenDoc.app.tar.gz", size=1_200_000):
    return SimpleUploadedFile(name, b"\x1f\x8b" + b"\0" * (size - 2))


@override_settings(MEDIA_ROOT=MEDIA)
class ReleaseTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        shutil.rmtree(MEDIA, ignore_errors=True)  # leftovers would make Django rename the new file
        admin = User.objects.create_user("boss@example.com", "boss@example.com", "pw", is_staff=True)
        self.admin = {"HTTP_AUTHORIZATION": f"Bearer {issue_token(admin)[0]}"}
        user = User.objects.create_user("user@example.com", "user@example.com", "pw")
        self.user = {"HTTP_AUTHORIZATION": f"Bearer {issue_token(user)[0]}"}

    def publish(self, auth=None, **files):
        data = {
            "installer": files.get("installer", installer()),
            "manifest": files.get("manifest", manifest()),
            "notes": files.get("notes", "Faster start"),
        }
        if "updater_bundle" in files:
            data["updater_bundle"] = files["updater_bundle"]
        return self.client.post("/api/desktop/releases/", data, **(auth if auth is not None else self.admin))

    # --- who may publish ---

    def test_only_administrators_can_publish_list_or_remove(self):
        self.assertEqual(self.publish(auth={}).status_code, 401)
        self.assertEqual(self.publish(auth=self.user).status_code, 403)
        self.assertEqual(self.client.get("/api/desktop/releases/", **self.user).status_code, 403)
        self.assertEqual(self.client.delete("/api/desktop/releases/0.2.0/", **self.user).status_code, 403)
        self.assertEqual(DesktopRelease.objects.count(), 0)

    # --- publishing ---

    def test_publishing_stores_the_release(self):
        r = self.publish()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["version"], body["notes"], body["isLatest"]), ("0.2.0", "Faster start", True))
        row = DesktopRelease.objects.get()
        self.assertEqual(row.created_by, "boss@example.com")
        self.assertEqual(row.size, 1_200_000)
        self.assertTrue(row.installer.name.endswith("GreenDoc_0.2.0_x64-setup.exe"))
        self.assertEqual(row.platform, "windows-x86_64")

    def test_notes_default_to_the_ones_the_build_wrote(self):
        r = self.publish(manifest=manifest(notes="From the build"), notes="")
        self.assertEqual(r.json()["notes"], "From the build")

    def test_both_files_are_needed(self):
        r = self.client.post("/api/desktop/releases/", {"installer": installer()}, **self.admin)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "missing_files"))

    def test_the_two_files_must_come_from_the_same_build(self):
        r = self.publish(installer=installer("0.2.1"), manifest=manifest("0.2.0"))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "version_mismatch"))

    def test_rejects_things_that_are_not_an_installer_or_update_file(self):
        bad = [
            (installer(name="setup.exe"), manifest(), "bad_installer"),
            (installer(magic=b"PK"), manifest(), "bad_installer"),
            (installer(size=1000), manifest(), "bad_installer"),
            (installer(), manifest(signature="not-a-signature"), "bad_manifest"),
            (installer(), manifest(platform="linux-x86_64"), "bad_manifest"),
            (installer(), manifest(version="two"), "bad_manifest"),
            (installer(), SimpleUploadedFile("latest.json", b"{nope"), "bad_manifest"),
        ]
        for inst, man, code in bad:
            r = self.publish(installer=inst, manifest=man)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, code), code)
        self.assertEqual(DesktopRelease.objects.count(), 0)

    def test_publishes_mac_dmg_and_signed_updater_bundle_per_architecture(self):
        release = self.publish(
            installer=installer(platform="darwin-aarch64"),
            manifest=manifest(platform="darwin-aarch64"),
            updater_bundle=updater_bundle(),
        )
        self.assertEqual(release.status_code, 201, release.content)
        row = DesktopRelease.objects.get(platform="darwin-aarch64")
        self.assertTrue(row.installer.name.endswith("GreenDoc_0.2.0_aarch64.dmg"))
        self.assertTrue(row.updater_bundle.name.endswith(".app.tar.gz"))
        self.assertEqual(
            self.client.get("/api/desktop/latest/?platform=darwin-aarch64").json()["platform"],
            "darwin-aarch64",
        )
        manifest_response = self.client.get("/api/desktop/latest.json?current=0.1.0&platform=darwin-aarch64")
        entry = manifest_response.json()["platforms"]["darwin-aarch64"]
        self.assertTrue(entry["url"].endswith("/api/desktop/update/0.2.0/?platform=darwin-aarch64"))
        for endpoint, filename in (
            ("/api/desktop/download/latest/?platform=darwin-aarch64", "GreenDoc_0.2.0_aarch64.dmg"),
            ("/api/desktop/update/latest/?platform=darwin-aarch64", ".app.tar.gz"),
        ):
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 200)
            self.assertIn(filename, response["Content-Disposition"])

    def test_mac_release_requires_both_mac_artifacts(self):
        response = self.publish(
            installer=installer(platform="darwin-x86_64"),
            manifest=manifest(platform="darwin-x86_64"),
        )
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "missing_files"))
        self.assertEqual(DesktopRelease.objects.count(), 0)

    def test_same_version_can_be_published_for_different_platforms(self):
        self.assertEqual(self.publish().status_code, 201)
        mac = self.publish(
            installer=installer(platform="darwin-x86_64"),
            manifest=manifest(platform="darwin-x86_64"),
            updater_bundle=updater_bundle(),
        )
        self.assertEqual(mac.status_code, 201, mac.content)
        self.assertEqual(DesktopRelease.objects.count(), 2)

    def test_a_version_is_published_once_and_only_when_newer(self):
        self.assertEqual(self.publish().status_code, 201)
        again = self.publish()
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (409, "version_exists"))
        older = self.publish(installer=installer("0.1.9"), manifest=manifest("0.1.9"))
        self.assertEqual((older.status_code, older.json()["error"]["code"]), (409, "not_newer"))
        self.assertEqual(DesktopRelease.objects.count(), 1)

    def test_versions_compare_as_numbers_not_text(self):
        self.assertEqual(self.publish(installer=installer("0.9.0"), manifest=manifest("0.9.0")).status_code, 201)
        self.assertEqual(self.publish(installer=installer("0.10.0"), manifest=manifest("0.10.0")).status_code, 201)
        listed = self.client.get("/api/desktop/releases/", **self.admin).json()["releases"]
        self.assertEqual([r["version"] for r in listed], ["0.10.0", "0.9.0"])
        self.assertEqual([r["isLatest"] for r in listed], [True, False])

    # --- what the installed app asks ---

    def test_nothing_published_means_nothing_to_update(self):
        self.assertEqual(self.client.get("/api/desktop/latest.json").status_code, 204)
        self.assertEqual(self.client.get("/api/desktop/latest/").json(), {"available": False})
        self.assertEqual(self.client.get("/api/desktop/download/latest/").status_code, 404)
        self.assertEqual(
            self.client.get("/api/desktop/latest/?platform=darwin-aarch64").json(),
            {"available": False},
        )

    def test_an_older_app_is_offered_the_new_version(self):
        self.publish()
        r = self.client.get("/api/desktop/latest.json?current=0.1.0")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["version"], "0.2.0")
        entry = body["platforms"]["windows-x86_64"]
        self.assertEqual(entry["signature"], SIGNATURE)
        self.assertTrue(entry["url"].endswith("/api/desktop/download/0.2.0/"))
        self.assertTrue(entry["url"].startswith("http"))

    def test_an_up_to_date_app_is_offered_nothing(self):
        self.publish()
        self.assertEqual(self.client.get("/api/desktop/latest.json?current=0.2.0").status_code, 204)
        self.assertEqual(self.client.get("/api/desktop/latest.json?current=0.3.0").status_code, 204)

    def test_the_update_file_and_download_need_no_sign_in(self):
        self.publish()
        self.assertEqual(self.client.get("/api/desktop/latest.json").status_code, 200)
        info = self.client.get("/api/desktop/latest/").json()
        self.assertEqual((info["available"], info["version"]), (True, "0.2.0"))
        for version in ("0.2.0", "latest"):
            r = self.client.get(f"/api/desktop/download/{version}/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("GreenDoc_0.2.0_x64-setup.exe", r["Content-Disposition"])
            self.assertEqual(len(b"".join(r.streaming_content)), 1_200_000)

    # --- taking one down ---

    def test_an_administrator_can_remove_a_version(self):
        self.publish()
        self.assertEqual(self.client.delete("/api/desktop/releases/0.2.0/", **self.admin).status_code, 204)
        self.assertEqual(DesktopRelease.objects.count(), 0)
        self.assertEqual(self.client.get("/api/desktop/latest.json").status_code, 204)
        self.assertEqual(self.client.delete("/api/desktop/releases/0.2.0/", **self.admin).status_code, 404)

    def test_removing_the_newest_brings_back_the_previous_one(self):
        self.publish(installer=installer("0.2.0"), manifest=manifest("0.2.0"))
        self.publish(installer=installer("0.3.0"), manifest=manifest("0.3.0"))
        self.client.delete("/api/desktop/releases/0.3.0/", **self.admin)
        self.assertEqual(self.client.get("/api/desktop/latest.json?current=0.1.0").json()["version"], "0.2.0")
