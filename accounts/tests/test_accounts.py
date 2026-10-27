import json
import re
import uuid
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AuthToken
from documents.models import Document

FAST = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
PASSWORD = "correct-horse-battery"
GUEST = str(uuid.uuid4())
OTHER_GUEST = str(uuid.uuid4())


class Base(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def call(self, method, url, body=None, token=None, guest=None):
        headers = {}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if guest:
            headers["HTTP_X_CLIENT_ID"] = guest
        if body is None:
            return getattr(self.client, method)(url, **headers)
        return getattr(self.client, method)(url, json.dumps(body), content_type="application/json", **headers)

    def register(self, email="amina@example.com", first="Amina", last="Ndlovu", password=PASSWORD, **extra):
        return self.call(
            "post", "/api/auth/register/",
            {"email": email, "firstName": first, "lastName": last, "password": password, **extra},
        )

    def signed_in(self, email="amina@example.com"):
        r = self.register(email=email)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["token"]


@FAST
class RegisterLoginTests(Base):
    def test_register_returns_session_and_profile(self):
        r = self.register(email="  Amina@Example.COM ")
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["user"]["email"], "amina@example.com")  # normalised
        self.assertEqual((data["user"]["displayName"], data["user"]["initials"]), ("Amina Ndlovu", "AN"))
        self.assertEqual(data["user"]["documentCount"], 0)
        self.assertNotIn("password", json.dumps(data))
        me = self.call("get", "/api/auth/me/", token=data["token"])
        self.assertEqual(me.json()["email"], "amina@example.com")

    def test_field_level_validation_messages(self):
        cases = {
            "email": dict(email="not-an-email"),
            "firstName": dict(first="  "),
            "lastName": dict(last="x" * 51),
            "password": dict(password="short"),
        }
        for field, kwargs in cases.items():
            r = self.register(**kwargs)
            self.assertEqual(r.status_code, 400, field)
            self.assertIn(field, r.json()["error"]["fields"], field)

    def test_password_rules(self):
        for bad in ("12345678", "password", "amina@example.com"):
            r = self.register(password=bad)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "weak_password"), bad)
            self.assertIsInstance(r.json()["error"]["fields"]["password"], list)
        self.assertEqual(self.register(password="x" * 129).status_code, 400)

    def test_duplicate_email(self):
        self.signed_in()
        r = self.register(email="AMINA@example.com")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "email_taken"))
        self.assertIn("email", r.json()["error"]["fields"])

    def test_login(self):
        self.signed_in()
        ok = self.call("post", "/api/auth/login/", {"email": "Amina@example.com", "password": PASSWORD})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["token"])
        for body in ({"email": "amina@example.com", "password": "wrong-password"}, {"email": "nobody@example.com", "password": PASSWORD}):
            r = self.call("post", "/api/auth/login/", body)
            self.assertEqual((r.status_code, r.json()["error"]["message"]), (401, "Email or password is incorrect."))  # no hint which was wrong
        self.assertEqual(self.call("post", "/api/auth/login/", {"email": "amina@example.com"}).status_code, 401)

    def test_login_lockout_after_repeated_failures(self):
        self.signed_in()
        bad = {"email": "amina@example.com", "password": "nope-nope-nope"}
        for _ in range(5):
            self.assertEqual(self.call("post", "/api/auth/login/", bad).status_code, 401)
        locked = self.call("post", "/api/auth/login/", bad)
        self.assertEqual((locked.status_code, locked.json()["error"]["code"]), (429, "too_many_attempts"))
        self.assertIn("Retry-After", locked)
        # even the right password is refused while locked
        self.assertEqual(self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD}).status_code, 429)

    def test_success_clears_failure_count(self):
        self.signed_in()
        bad = {"email": "amina@example.com", "password": "nope-nope-nope"}
        for _ in range(4):
            self.call("post", "/api/auth/login/", bad)
        self.assertEqual(self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD}).status_code, 200)
        for _ in range(4):
            self.assertEqual(self.call("post", "/api/auth/login/", bad).status_code, 401)


@FAST
class SessionTests(Base):
    def test_logout_revokes_only_this_session(self):
        a = self.signed_in()
        b = self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD}).json()["token"]
        self.assertEqual(self.call("post", "/api/auth/logout/", token=a).status_code, 204)
        self.assertEqual(self.call("get", "/api/auth/me/", token=a).status_code, 401)
        self.assertEqual(self.call("get", "/api/auth/me/", token=b).status_code, 200)
        self.call("post", "/api/auth/logout-all/", token=b)
        self.assertEqual(self.call("get", "/api/auth/me/", token=b).status_code, 401)

    def test_bad_and_expired_tokens_are_401_not_guest(self):
        self.assertEqual(self.call("get", "/api/documents/", token="garbage", guest=GUEST).status_code, 401)
        self.assertEqual(self.client.get("/api/documents/", HTTP_AUTHORIZATION="Basic abc").status_code, 401)
        token = self.signed_in()
        AuthToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        r = self.call("get", "/api/auth/me/", token=token)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "invalid_token"))

    def test_account_endpoints_need_sign_in(self):
        for method, url in (("get", "/api/auth/me/"), ("post", "/api/auth/logout/"), ("post", "/api/auth/change-password/")):
            r = self.call(method, url, guest=GUEST)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "sign_in_required"), url)

    def test_token_is_stored_hashed(self):
        token = self.signed_in()
        self.assertFalse(AuthToken.objects.filter(token_hash=token).exists())

    def test_old_sessions_are_capped(self):
        self.signed_in()
        for _ in range(12):
            self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD})
        self.assertEqual(AuthToken.objects.count(), 10)

    def test_inactive_user_cannot_use_token(self):
        token = self.signed_in()
        from django.contrib.auth import get_user_model

        get_user_model().objects.update(is_active=False)
        self.assertEqual(self.call("get", "/api/auth/me/", token=token).status_code, 401)


@FAST
class ProfileTests(Base):
    def test_update_names(self):
        token = self.signed_in()
        r = self.call("put", "/api/auth/me/", {"firstName": "  Amina   Grace ", "lastName": "Dlamini"}, token=token).json()
        self.assertEqual((r["displayName"], r["initials"]), ("Amina Grace Dlamini", "AD"))
        self.assertEqual(self.call("put", "/api/auth/me/", {"bogus": 1}, token=token).status_code, 400)
        self.assertEqual(self.call("put", "/api/auth/me/", {"firstName": ""}, token=token).status_code, 400)

    def test_email_change_needs_password_and_is_unique(self):
        token = self.signed_in()
        self.signed_in("taken@example.com")
        r = self.call("put", "/api/auth/me/", {"email": "new@example.com"}, token=token)
        self.assertEqual(r.status_code, 403)
        self.assertIn("currentPassword", r.json()["error"]["fields"])
        r = self.call("put", "/api/auth/me/", {"email": "taken@example.com", "currentPassword": PASSWORD}, token=token)
        self.assertEqual(r.status_code, 409)
        r = self.call("put", "/api/auth/me/", {"email": "New@Example.com", "currentPassword": PASSWORD}, token=token)
        self.assertEqual(r.json()["email"], "new@example.com")
        self.assertEqual(self.call("post", "/api/auth/login/", {"email": "new@example.com", "password": PASSWORD}).status_code, 200)

    def test_change_password_keeps_this_device_only(self):
        a = self.signed_in()
        b = self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD}).json()["token"]
        r = self.call("post", "/api/auth/change-password/", {"currentPassword": "wrong", "newPassword": "another-long-pass"}, token=a)
        self.assertEqual(r.status_code, 403)
        r = self.call("post", "/api/auth/change-password/", {"currentPassword": PASSWORD, "newPassword": "123"}, token=a)
        self.assertEqual(r.status_code, 400)
        self.assertIn("newPassword", r.json()["error"]["fields"])
        r = self.call("post", "/api/auth/change-password/", {"currentPassword": PASSWORD, "newPassword": "another-long-pass"}, token=a)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.call("get", "/api/auth/me/", token=a).status_code, 200)
        self.assertEqual(self.call("get", "/api/auth/me/", token=b).status_code, 401)
        self.assertEqual(self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": "another-long-pass"}).status_code, 200)

    def test_delete_account_removes_everything(self):
        token = self.signed_in()
        doc = self.call("post", "/api/documents/", {"title": "Mine", "html": "<p>x</p>"}, token=token).json()["id"]
        self.call("post", f"/api/documents/{doc}/comments/", {"text": "c"}, token=token)
        self.call("put", "/api/preferences/", {"dark": True}, token=token)
        self.assertEqual(self.call("delete", "/api/auth/me/", {"password": "wrong"}, token=token).status_code, 403)
        self.assertEqual(self.call("delete", "/api/auth/me/", {"password": PASSWORD}, token=token).status_code, 204)
        from documents.models import Comment
        from workspace.models import Preferences

        self.assertEqual((Document.objects.count(), Comment.objects.count(), Preferences.objects.count()), (0, 0, 0))
        self.assertEqual(self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD}).status_code, 401)


@FAST
class PasswordResetTests(Base):
    def test_full_reset_flow(self):
        token = self.signed_in()
        r = self.call("post", "/api/auth/forgot-password/", {"email": "Amina@example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["amina@example.com"])
        m = re.search(r"reset-password\?uid=([^&\s]+)&token=([^\s]+)", mail.outbox[0].body)
        uid, reset = m.groups()
        r = self.call("post", "/api/auth/reset-password/", {"uid": uid, "token": reset, "newPassword": "brand-new-passphrase"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.call("get", "/api/auth/me/", token=token).status_code, 401)  # signed out everywhere
        self.assertEqual(self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": "brand-new-passphrase"}).status_code, 200)
        # the link works once
        again = self.call("post", "/api/auth/reset-password/", {"uid": uid, "token": reset, "newPassword": "yet-another-passphrase"})
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (400, "invalid_reset_link"))

    def test_unknown_email_looks_identical_and_sends_nothing(self):
        self.signed_in()
        known = self.call("post", "/api/auth/forgot-password/", {"email": "amina@example.com"})
        unknown = self.call("post", "/api/auth/forgot-password/", {"email": "nobody@example.com"})
        self.assertEqual((known.status_code, known.json()), (unknown.status_code, unknown.json()))
        self.assertEqual(len(mail.outbox), 1)

    def test_reset_emails_are_capped(self):
        self.signed_in()
        for _ in range(8):
            self.call("post", "/api/auth/forgot-password/", {"email": "amina@example.com"})
        self.assertEqual(len(mail.outbox), 5)

    def test_bad_links_and_weak_new_password(self):
        for body in ({}, {"uid": "x", "token": "y", "newPassword": PASSWORD}, {"uid": "MQ", "token": "bad", "newPassword": PASSWORD}):
            self.assertEqual(self.call("post", "/api/auth/reset-password/", body).status_code, 400, body)
        self.signed_in()
        self.call("post", "/api/auth/forgot-password/", {"email": "amina@example.com"})
        uid, tok = re.search(r"uid=([^&\s]+)&token=([^\s]+)", mail.outbox[0].body).groups()
        r = self.call("post", "/api/auth/reset-password/", {"uid": uid, "token": tok, "newPassword": "123"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("newPassword", r.json()["error"]["fields"])


@FAST
class GuestDataTests(Base):
    def test_guest_work_moves_into_the_account(self):
        guest_doc = self.call("post", "/api/documents/", {"title": "Written as guest", "html": "<p>hi</p>"}, guest=GUEST).json()["id"]
        self.call("put", "/api/preferences/", {"dark": True}, guest=GUEST)
        self.call("post", "/api/sources/", {"tag": "G1", "author": "Guest, A.", "title": "T"}, guest=GUEST)
        r = self.call(
            "post", "/api/auth/register/",
            {"email": "amina@example.com", "firstName": "Amina", "lastName": "Ndlovu", "password": PASSWORD}, guest=GUEST,
        )
        self.assertEqual(r.status_code, 201)
        claimed = r.json()["claimed"]
        self.assertEqual((claimed["documents"], claimed["preferences"]), (1, True))
        self.assertGreaterEqual(claimed["sources"], 4)
        token = r.json()["token"]
        # the account sees it with no guest header, i.e. from any device
        docs = self.call("get", "/api/documents/", token=token).json()["documents"]
        self.assertEqual([d["id"] for d in docs], [guest_doc])
        self.assertEqual(self.call("get", "/api/preferences/", token=token).json()["dark"], True)
        self.assertIn("G1", [s["tag"] for s in self.call("get", "/api/sources/", token=token).json()["sources"]])
        self.assertEqual(self.call("get", "/api/auth/me/", token=token).json()["documentCount"], 1)
        # and the old guest id no longer holds it
        self.assertEqual(self.call("get", "/api/documents/", guest=GUEST).json()["documents"], [])

    def test_login_on_a_second_device_sees_the_same_documents(self):
        token = self.signed_in()
        doc = self.call("post", "/api/documents/", {"title": "Cloud doc"}, token=token).json()["id"]
        second = self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD}, guest=OTHER_GUEST).json()
        self.assertEqual(second["claimed"]["documents"], 0)
        self.assertEqual(self.call("get", f"/api/documents/{doc}/", token=second["token"]).status_code, 200)

    def test_guest_work_merges_with_existing_account_data(self):
        token = self.signed_in()
        self.call("post", "/api/documents/", {"title": "Account doc"}, token=token)
        self.call("put", "/api/preferences/", {"dark": False, "zoom": 120}, token=token)
        self.call("post", "/api/documents/", {"title": "Guest doc"}, guest=GUEST)
        self.call("put", "/api/preferences/", {"dark": True, "showGrid": True}, guest=GUEST)
        self.call("get", "/api/sources/", guest=GUEST)  # seeds the sample sources for the guest too
        r = self.call("post", "/api/auth/login/", {"email": "amina@example.com", "password": PASSWORD}, guest=GUEST).json()
        self.assertEqual(r["claimed"]["documents"], 1)
        titles = sorted(d["title"] for d in self.call("get", "/api/documents/", token=r["token"]).json()["documents"])
        self.assertEqual(titles, ["Account doc", "Guest doc"])
        prefs = self.call("get", "/api/preferences/", token=r["token"]).json()
        self.assertEqual((prefs["dark"], prefs["zoom"], prefs["showGrid"]), (False, 120, True))  # account wins conflicts
        tags = [s["tag"] for s in self.call("get", "/api/sources/", token=r["token"]).json()["sources"]]
        self.assertEqual(len(tags), len(set(tags)))

    def test_opting_out_of_claiming(self):
        self.call("post", "/api/documents/", {"title": "Guest doc"}, guest=GUEST)
        r = self.call(
            "post", "/api/auth/register/",
            {"email": "a@example.com", "firstName": "A", "lastName": "B", "password": PASSWORD, "claimGuestData": False}, guest=GUEST,
        ).json()
        self.assertNotIn("claimed", r)
        self.assertEqual(self.call("get", "/api/documents/", token=r["token"]).json()["documents"], [])
        self.assertEqual(len(self.call("get", "/api/documents/", guest=GUEST).json()["documents"]), 1)

    def test_accounts_cannot_see_each_others_documents(self):
        a, b = self.signed_in(), self.signed_in("bo@example.com")
        doc = self.call("post", "/api/documents/", {"title": "Private"}, token=a).json()["id"]
        self.assertEqual(self.call("get", f"/api/documents/{doc}/", token=b).status_code, 404)

    def test_account_wins_over_a_guest_header(self):
        token = self.signed_in()
        doc = self.call("post", "/api/documents/", {"title": "Acct"}, token=token, guest=GUEST).json()["id"]
        self.assertEqual(self.call("get", f"/api/documents/{doc}/", token=token).status_code, 200)
        self.assertEqual(self.call("get", f"/api/documents/{doc}/", guest=GUEST).status_code, 404)

    def test_comment_author_defaults_to_account_name(self):
        token = self.signed_in()
        doc = self.call("post", "/api/documents/", {}, token=token).json()["id"]
        c = self.call("post", f"/api/documents/{doc}/comments/", {"text": "hi"}, token=token).json()
        self.assertEqual((c["author"], c["initials"]), ("Amina Ndlovu", "AN"))
