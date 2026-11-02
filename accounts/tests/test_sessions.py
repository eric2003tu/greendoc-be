import json
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts import tokens
from accounts.models import AuthToken

FAST = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
PASSWORD = "correct-horse-battery"
CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Safari/604.1"


@FAST
class SessionTests(TestCase):
    def setUp(self):
        cache.clear()

    def call(self, method, url, body=None, token=None, agent=None):
        headers = {}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if agent:
            headers["HTTP_USER_AGENT"] = agent
        if body is None:
            return getattr(self.client, method)(url, **headers)
        return getattr(self.client, method)(url, json.dumps(body), content_type="application/json", **headers)

    def register(self, agent=CHROME_WIN):
        r = self.call(
            "post", "/api/auth/register/",
            {"email": "amina@example.com", "firstName": "Amina", "lastName": "Ndlovu", "password": PASSWORD}, agent=agent,
        )
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def login(self, agent):
        return self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD}, agent=agent).json()

    def test_sign_in_reports_when_the_session_ends(self):
        data = self.register()
        ends = timezone.datetime.fromisoformat(data["expiresAt"])
        self.assertAlmostEqual((ends - timezone.now()).days, 30, delta=1)
        self.assertIn("expiresAt", self.login(CHROME_WIN))

    def test_sliding_window_extends_on_use_but_never_past_the_cap(self):
        data = self.register()
        row = AuthToken.objects.get()
        row.last_used_at = timezone.now() - timedelta(hours=2)
        row.expires_at = timezone.now() + timedelta(days=2)
        row.save()
        self.call("get", "/api/auth/me/", token=data["token"])
        row.refresh_from_db()
        self.assertGreater(row.expires_at, timezone.now() + timedelta(days=29))
        # near the 90-day cap the window is clipped to the cap
        row.last_used_at = timezone.now() - timedelta(hours=2)
        row.max_expires_at = timezone.now() + timedelta(days=3)
        row.save()
        self.call("get", "/api/auth/me/", token=data["token"])
        row.refresh_from_db()
        self.assertLessEqual(row.expires_at, row.max_expires_at)

    def test_session_past_the_absolute_cap_is_dead_even_if_recently_used(self):
        data = self.register()
        AuthToken.objects.update(max_expires_at=timezone.now() - timedelta(seconds=1))
        r = self.call("get", "/api/auth/me/", token=data["token"])
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "invalid_token"))

    def test_refresh_rotates_the_token(self):
        data = self.register()
        old = data["token"]
        r = self.call("post", "/api/auth/refresh/", token=old)
        self.assertEqual(r.status_code, 200, r.content)
        new = r.json()
        self.assertNotEqual(new["token"], old)
        self.assertEqual(new["user"]["email"], "amina@example.com")
        self.assertEqual(self.call("get", "/api/auth/me/", token=old).status_code, 401)  # old one is dead
        self.assertEqual(self.call("get", "/api/auth/me/", token=new["token"]).status_code, 200)
        self.assertEqual(AuthToken.objects.count(), 1)  # a swap, not an extra device

    def test_refresh_keeps_the_device_label_and_resets_the_cap(self):
        data = self.register(CHROME_WIN)
        AuthToken.objects.update(max_expires_at=timezone.now() + timedelta(days=1))
        new = self.call("post", "/api/auth/refresh/", token=data["token"]).json()
        row = AuthToken.objects.get()
        self.assertEqual(row.device, "Chrome on Windows")
        self.assertGreater(tokens.absolute_expiry(row), timezone.now() + timedelta(days=80))
        self.assertTrue(new["token"])

    def test_refresh_needs_a_valid_session(self):
        self.assertEqual(self.call("post", "/api/auth/refresh/").json()["error"]["code"], "sign_in_required")
        self.assertEqual(self.call("post", "/api/auth/refresh/", token="garbage").status_code, 401)
        data = self.register()
        AuthToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.call("post", "/api/auth/refresh/", token=data["token"]).status_code, 401)

    def test_sessions_list_marks_the_current_device(self):
        first = self.register(CHROME_WIN)
        second = self.login(SAFARI_IPHONE)
        listing = self.call("get", "/api/auth/sessions/", token=first["token"]).json()["sessions"]
        self.assertEqual(len(listing), 2)
        by_device = {s["device"]: s for s in listing}
        self.assertTrue(by_device["Chrome on Windows"]["current"])
        self.assertFalse(by_device["Safari on iOS"]["current"])
        for key in ("id", "createdAt", "lastUsedAt", "expiresAt"):
            self.assertIn(key, listing[0])
        self.assertNotIn("token", json.dumps(listing))
        listing2 = self.call("get", "/api/auth/sessions/", token=second["token"]).json()["sessions"]
        self.assertTrue({s["device"]: s for s in listing2}["Safari on iOS"]["current"])

    def test_revoke_another_device(self):
        first = self.register(CHROME_WIN)
        second = self.login(SAFARI_IPHONE)
        other = next(s for s in self.call("get", "/api/auth/sessions/", token=first["token"]).json()["sessions"] if not s["current"])
        self.assertEqual(self.call("delete", f"/api/auth/sessions/{other['id']}/", token=first["token"]).status_code, 204)
        self.assertEqual(self.call("get", "/api/auth/me/", token=second["token"]).status_code, 401)
        self.assertEqual(self.call("get", "/api/auth/me/", token=first["token"]).status_code, 200)
        self.assertEqual(self.call("delete", f"/api/auth/sessions/{other['id']}/", token=first["token"]).status_code, 404)

    def test_cannot_revoke_someone_elses_session(self):
        mine = self.register()
        victim = self.call(
            "post", "/api/auth/register/",
            {"email": "bo@example.com", "firstName": "Bo", "lastName": "Lee", "password": PASSWORD},
        ).json()
        victim_row = AuthToken.objects.get(user__email="bo@example.com")
        r = self.call("delete", f"/api/auth/sessions/{victim_row.public_id}/", token=mine["token"])
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.call("get", "/api/auth/me/", token=victim["token"]).status_code, 200)

    def test_revoking_the_current_session_signs_this_device_out(self):
        data = self.register()
        current = self.call("get", "/api/auth/sessions/", token=data["token"]).json()["sessions"][0]
        self.call("delete", f"/api/auth/sessions/{current['id']}/", token=data["token"])
        self.assertEqual(self.call("get", "/api/auth/me/", token=data["token"]).status_code, 401)

    def test_device_labels(self):
        cases = {
            CHROME_WIN: "Chrome on Windows",
            SAFARI_IPHONE: "Safari on iOS",
            "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0": "Firefox on Linux",
            "Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537 Edg/120": "Edge on Windows",
            "": "Browser",
            "curl/8.0": "Browser",
        }
        for agent, label in cases.items():
            self.assertEqual(tokens.describe_device(agent), label, agent)

    def test_unknown_agent_is_listed_safely(self):
        data = self.register(agent="")
        self.assertEqual(self.call("get", "/api/auth/sessions/", token=data["token"]).json()["sessions"][0]["device"], "Browser")
