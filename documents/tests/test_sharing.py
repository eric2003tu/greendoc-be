import io
import json
import uuid
import zipfile

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from documents import fileformats

FAST = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], FRONTEND_URL="https://app.example")
GUEST = str(uuid.uuid4())


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

    def user(self, email="amina@example.com"):
        r = self.call("post", "/api/auth/register/", {"email": email, "firstName": "Amina", "lastName": "Ndlovu", "password": "correct-horse-battery"})
        return r.json()["token"]

    def doc(self, token, html="<p>Hello</p>", **extra):
        return self.call("post", "/api/documents/", {"title": "Plan", "html": html, **extra}, token=token).json()["id"]


@FAST
class SharePermissionTests(Base):
    def test_default_view_link_is_read_only(self):
        t = self.user()
        doc = self.doc(t)
        link = self.call("post", f"/api/documents/{doc}/share/", token=t).json()
        self.assertEqual(link["permission"], "view")
        shared = self.client.get(f"/api/shared/{link['shareToken']}/").json()
        self.assertEqual((shared["permission"], shared["canEdit"]), ("view", False))
        r = self.call("put", f"/api/shared/{link['shareToken']}/", {"html": "<p>hack</p>"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "view_only"))
        self.assertEqual(self.call("get", f"/api/documents/{doc}/", token=t).json()["html"], "<p>Hello</p>")

    def test_edit_link_lets_anyone_with_it_change_text_only(self):
        t = self.user()
        doc = self.doc(t)
        token = self.call("post", f"/api/documents/{doc}/share/", {"permission": "edit"}, token=t).json()["shareToken"]
        self.assertTrue(self.client.get(f"/api/shared/{token}/").json()["canEdit"])
        r = self.call("put", f"/api/shared/{token}/", {"title": "Renamed", "html": "<p>by guest</p><script>x()</script>"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["title"], r.json()["html"]), ("Renamed", "<p>by guest</p>"))  # sanitised
        self.assertEqual(self.call("get", f"/api/documents/{doc}/", token=t).json()["html"], "<p>by guest</p>")
        # cannot touch owner-only things through the link
        for body in ({"protected": False}, {"settings": {"columns": 2}}, {"template": "x"}):
            self.assertEqual(self.call("put", f"/api/shared/{token}/", body).status_code, 400, body)

    def test_edit_link_respects_protection(self):
        t = self.user()
        doc = self.doc(t)
        token = self.call("post", f"/api/documents/{doc}/share/", {"permission": "edit"}, token=t).json()["shareToken"]
        self.call("put", f"/api/documents/{doc}/", {"protected": True}, token=t)
        self.assertFalse(self.client.get(f"/api/shared/{token}/").json()["canEdit"])
        r = self.call("put", f"/api/shared/{token}/", {"html": "<p>x</p>"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "document_protected"))

    def test_changing_and_revoking_permission(self):
        t = self.user()
        doc = self.doc(t)
        first = self.call("post", f"/api/documents/{doc}/share/", {"permission": "edit"}, token=t).json()
        again = self.call("post", f"/api/documents/{doc}/share/", {"permission": "view"}, token=t).json()
        self.assertEqual(first["shareToken"], again["shareToken"])  # same link, new permission
        self.assertEqual(again["permission"], "view")
        self.assertEqual(self.call("post", f"/api/documents/{doc}/share/", {"permission": "admin"}, token=t).status_code, 400)
        self.call("delete", f"/api/documents/{doc}/share/", token=t)
        self.assertEqual(self.client.get(f"/api/shared/{first['shareToken']}/").status_code, 404)
        reshared = self.call("post", f"/api/documents/{doc}/share/", token=t).json()
        self.assertEqual(reshared["permission"], "view")  # revoking resets to the safe default

    def test_shared_edits_are_versioned_and_unknown_tokens_404(self):
        t = self.user()
        doc = self.doc(t)
        token = self.call("post", f"/api/documents/{doc}/share/", {"permission": "edit"}, token=t).json()["shareToken"]
        self.call("put", f"/api/shared/{token}/", {"html": "<p>v2</p>"})
        self.assertGreaterEqual(len(self.call("get", f"/api/documents/{doc}/versions/", token=t).json()["versions"]), 1)
        self.assertEqual(self.call("put", "/api/shared/nope/", {"html": "x"}).status_code, 404)


@FAST
class InviteTests(Base):
    def test_invite_sends_link_with_permission(self):
        t = self.user()
        doc = self.doc(t)
        r = self.call("post", f"/api/documents/{doc}/invite/", {"emails": ["Morgan@example.com", "morgan@example.com", "lee@example.com"], "message": "Please review"}, token=t)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["sent"], body["permission"]), (2, "edit"))  # duplicates collapsed, edit by default
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["lee@example.com", "morgan@example.com"])
        text = mail.outbox[0].body
        self.assertIn(f"https://app.example{body['path']}", text)
        self.assertIn("Amina Ndlovu", mail.outbox[0].subject)
        self.assertIn("Please review", text)
        self.assertTrue(self.client.get(f"/api/shared/{body['shareToken']}/").json()["canEdit"])

    def test_view_invite(self):
        t = self.user()
        doc = self.doc(t)
        body = self.call("post", f"/api/documents/{doc}/invite/", {"emails": ["a@example.com"], "permission": "view"}, token=t).json()
        self.assertEqual(body["permission"], "view")
        self.assertIn("view", mail.outbox[0].body)

    def test_requires_sign_in_and_ownership(self):
        t = self.user()
        doc = self.doc(t)
        r = self.call("post", f"/api/documents/{doc}/invite/", {"emails": ["a@example.com"]}, guest=GUEST)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "sign_in_required"))
        other = self.user("bo@example.com")
        self.assertEqual(self.call("post", f"/api/documents/{doc}/invite/", {"emails": ["a@example.com"]}, token=other).status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_validation(self):
        t = self.user()
        doc = self.doc(t)
        for body in ({}, {"emails": []}, {"emails": "a@example.com"}, {"emails": ["nope"]}, {"emails": [f"u{i}@example.com" for i in range(11)]}, {"emails": ["a@example.com"], "message": "x" * 1001}):
            self.assertEqual(self.call("post", f"/api/documents/{doc}/invite/", body, token=t).status_code, 400, body)

    def test_hourly_email_cap(self):
        t = self.user()
        doc = self.doc(t)
        for batch in range(2):
            ok = self.call("post", f"/api/documents/{doc}/invite/", {"emails": [f"b{batch}u{i}@example.com" for i in range(10)]}, token=t)
            self.assertEqual(ok.status_code, 200)
        r = self.call("post", f"/api/documents/{doc}/invite/", {"emails": ["one-more@example.com"]}, token=t)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (429, "rate_limited"))
        self.assertEqual(len(mail.outbox), 20)


@FAST
class EmailCopyTests(Base):
    def test_sends_docx_attachment_to_self_by_default(self):
        t = self.user()
        doc = self.doc(t, "<h1>Report</h1><p>Body</p>")
        r = self.call("post", f"/api/documents/{doc}/email-copy/", {"message": "FYI"}, token=t)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["to"], "amina@example.com")
        message = mail.outbox[0]
        name, content, mimetype = message.attachments[0]
        self.assertEqual(name, "Plan.docx")
        self.assertEqual(mimetype, fileformats.CONTENT_TYPES["docx"])
        self.assertEqual(fileformats.import_file("x.docx", content)[0], "<h1>Report</h1><p>Body</p>")
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            self.assertIn("Amina Ndlovu", z.read("docProps/core.xml").decode())
        self.assertIn("FYI", message.body)

    def test_other_recipient_and_format(self):
        t = self.user()
        doc = self.doc(t)
        self.call("post", f"/api/documents/{doc}/email-copy/", {"to": "Lee@example.com", "format": "txt"}, token=t)
        self.assertEqual(mail.outbox[0].to, ["lee@example.com"])
        self.assertEqual(mail.outbox[0].attachments[0][0], "Plan.txt")

    def test_requires_sign_in_valid_input_and_ownership(self):
        t = self.user()
        doc = self.doc(t)
        self.assertEqual(self.call("post", f"/api/documents/{doc}/email-copy/", {}, guest=GUEST).status_code, 401)
        self.assertEqual(self.call("post", f"/api/documents/{doc}/email-copy/", {"format": "pdf"}, token=t).status_code, 400)
        self.assertEqual(self.call("post", f"/api/documents/{doc}/email-copy/", {"to": "bad"}, token=t).status_code, 400)
        other = self.user("bo@example.com")
        self.assertEqual(self.call("post", f"/api/documents/{doc}/email-copy/", {}, token=other).status_code, 404)
        self.assertEqual(len(mail.outbox), 0)


@FAST
class SupportTests(Base):
    def test_guest_message_is_saved_and_emailed(self):
        r = self.call("post", "/api/support/", {"name": "Lee", "email": "Lee@example.com", "subject": "Export", "message": "Button does nothing"})
        self.assertEqual(r.status_code, 201)
        from support.models import SupportMessage

        saved = SupportMessage.objects.get()
        self.assertEqual((saved.email, saved.subject, saved.user_id), ("lee@example.com", "Export", None))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Button does nothing", mail.outbox[0].body)

    def test_signed_in_users_skip_name_and_email(self):
        t = self.user()
        r = self.call("post", "/api/support/", {"subject": "Help", "message": "Question"}, token=t)
        self.assertEqual(r.status_code, 201, r.content)
        from support.models import SupportMessage

        saved = SupportMessage.objects.get()
        self.assertEqual((saved.email, saved.name), ("amina@example.com", "Amina Ndlovu"))
        self.assertIsNotNone(saved.user_id)

    def test_field_errors_and_rate_limit(self):
        r = self.call("post", "/api/support/", {"email": "x", "subject": " ", "message": ""})
        self.assertEqual(r.status_code, 400)
        r = self.call("post", "/api/support/", {"email": "a@example.com", "subject": " ", "message": ""})
        self.assertEqual(set(r.json()["error"]["fields"]), {"subject", "message"})
        for _ in range(5):
            self.assertEqual(self.call("post", "/api/support/", {"email": "a@example.com", "subject": "s", "message": "m"}).status_code, 201)
        self.assertEqual(self.call("post", "/api/support/", {"email": "a@example.com", "subject": "s", "message": "m"}).status_code, 429)
