import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

FAST = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])


@FAST
class DemoAccountTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        call_command("seed_demo", verbosity=0)

    def call(self, method, url, body=None, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        data = json.dumps(body) if body is not None else None
        if data is None:
            return getattr(self.client, method)(url, **headers)
        return getattr(self.client, method)(url, data, content_type="application/json", **headers)

    def sign_in(self):
        r = self.call("post", "/api/auth/login/", {"email": settings.DEMO_EMAIL, "password": settings.DEMO_PASSWORD})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["token"]

    def test_demo_credentials_work(self):
        token = self.sign_in()
        me = self.call("get", "/api/auth/me/", token=token).json()
        self.assertEqual((me["email"], me["displayName"], me["initials"]), (settings.DEMO_EMAIL, "Demo User", "DU"))

    def test_seed_is_idempotent_and_restores_the_password(self):
        User = get_user_model()
        User.objects.filter(username=settings.DEMO_EMAIL).update(first_name="Hacked", is_active=False)
        user = User.objects.get(username=settings.DEMO_EMAIL)
        user.set_password("something-else-entirely")
        user.save()
        call_command("seed_demo", verbosity=0)
        self.assertEqual(User.objects.filter(username=settings.DEMO_EMAIL).count(), 1)
        self.sign_in()
        self.assertEqual(User.objects.get(username=settings.DEMO_EMAIL).first_name, "Demo")

    def test_demo_account_cannot_be_locked_down_or_removed(self):
        token = self.sign_in()
        attempts = [
            ("delete", "/api/auth/me/", {"password": settings.DEMO_PASSWORD}),
            ("post", "/api/auth/change-password/", {"currentPassword": settings.DEMO_PASSWORD, "newPassword": "my-own-new-passphrase"}),
            ("put", "/api/auth/me/", {"email": "mine@example.com", "currentPassword": settings.DEMO_PASSWORD}),
        ]
        for method, url, body in attempts:
            r = self.call(method, url, body, token=token)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "demo_account"), url)
        self.sign_in()  # still works with the public password

    def test_demo_name_can_still_be_edited_and_documents_saved(self):
        token = self.sign_in()
        self.assertEqual(self.call("put", "/api/auth/me/", {"firstName": "Guest"}, token=token).status_code, 200)
        self.assertEqual(self.call("post", "/api/documents/", {"title": "Try it"}, token=token).status_code, 201)

    def test_demo_password_cannot_be_reset_by_email(self):
        r = self.call("post", "/api/auth/forgot-password/", {"email": settings.DEMO_EMAIL})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
