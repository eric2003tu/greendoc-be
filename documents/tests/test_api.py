import uuid

from django.core.cache import cache
from django.test import TestCase, override_settings

from documents.sanitize import sanitize_html

A = str(uuid.uuid4())
B = str(uuid.uuid4())


def hdr(client):
    return {"HTTP_X_CLIENT_ID": client}


class SanitizeTests(TestCase):
    def test_strips_scripts_handlers_and_js_urls(self):
        dirty = (
            '<p onclick="x()">Hi</p><script>alert(1)</script>'
            '<a href="javascript:alert(1)">l</a><img src="data:text/html;base64,AA">'
            '<div style="width:expression(alert(1))">d</div><iframe src="x">t</iframe>'
        )
        clean = sanitize_html(dirty)
        for bad in ("script", "onclick", "javascript:", "iframe", "expression", "text/html"):
            self.assertNotIn(bad, clean)
        self.assertIn("Hi", clean)

    def test_keeps_formatting_and_image_data_urls(self):
        html = '<h1 class="w-s-title">T</h1><p style="color:red"><b>x</b><br></p><img src="data:image/png;base64,AAAA">'
        self.assertEqual(sanitize_html(html), html.replace("<br></p>", "<br></p>"))


class DocumentApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_requires_client_id(self):
        r = self.client.get("/api/documents/")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "invalid_client_id")

    def test_crud_roundtrip(self):
        r = self.client.post(
            "/api/documents/",
            {"title": "Plan", "html": "<p>hello</p>"},
            content_type="application/json",
            **hdr(A),
        )
        self.assertEqual(r.status_code, 201)
        doc_id = r.json()["id"]
        r = self.client.get(f"/api/documents/{doc_id}/", **hdr(A))
        self.assertEqual(r.json()["html"], "<p>hello</p>")
        r = self.client.put(
            f"/api/documents/{doc_id}/",
            {"title": "Plan v2"},
            content_type="application/json",
            **hdr(A),
        )
        self.assertEqual(r.json()["title"], "Plan v2")
        self.assertEqual(r.json()["html"], "<p>hello</p>")  # partial update keeps html
        listing = self.client.get("/api/documents/", **hdr(A)).json()["documents"]
        self.assertEqual([d["title"] for d in listing], ["Plan v2"])
        self.assertNotIn("html", listing[0])
        self.assertEqual(self.client.delete(f"/api/documents/{doc_id}/", **hdr(A)).status_code, 204)
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/", **hdr(A)).status_code, 404)

    def test_put_upserts_under_draft_id(self):
        draft = str(uuid.uuid4())
        r = self.client.put(
            f"/api/documents/{draft}/",
            {"title": "Draft", "html": "<p>x</p>", "template": "resume"},
            content_type="application/json",
            **hdr(A),
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["id"], draft)

    def test_documents_are_private_to_their_client(self):
        draft = str(uuid.uuid4())
        self.client.put(
            f"/api/documents/{draft}/", {"title": "Mine"}, content_type="application/json", **hdr(A)
        )
        self.assertEqual(self.client.get(f"/api/documents/{draft}/", **hdr(B)).status_code, 404)
        r = self.client.put(
            f"/api/documents/{draft}/", {"title": "Hijack"}, content_type="application/json", **hdr(B)
        )
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.delete(f"/api/documents/{draft}/", **hdr(B)).status_code, 404)
        self.assertEqual(self.client.get("/api/documents/", **hdr(B)).json()["documents"], [])

    def test_html_is_sanitised_on_save(self):
        r = self.client.post(
            "/api/documents/",
            {"html": "<p>ok</p><script>bad()</script>"},
            content_type="application/json",
            **hdr(A),
        )
        self.assertEqual(r.json()["html"], "<p>ok</p>")

    def test_validation(self):
        r = self.client.post(
            "/api/documents/", {"title": 5}, content_type="application/json", **hdr(A)
        )
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/documents/", "{nope", content_type="application/json", **hdr(A))
        self.assertEqual(r.json()["error"]["code"], "invalid_json")

    def test_share_link_lifecycle(self):
        doc = self.client.post(
            "/api/documents/",
            {"title": "Public", "html": "<p>hi</p>"},
            content_type="application/json",
            **hdr(A),
        ).json()
        token = self.client.post(f"/api/documents/{doc['id']}/share/", **hdr(A)).json()["shareToken"]
        shared = self.client.get(f"/api/shared/{token}/")  # no client id needed
        self.assertEqual(shared.status_code, 200)
        self.assertEqual(shared.json()["html"], "<p>hi</p>")
        self.assertNotIn("id", shared.json())
        self.assertEqual(self.client.post(f"/api/documents/{doc['id']}/share/", **hdr(B)).status_code, 404)
        self.client.delete(f"/api/documents/{doc['id']}/share/", **hdr(A))
        self.assertEqual(self.client.get(f"/api/shared/{token}/").status_code, 404)


class PlatformTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_health_and_cors(self):
        r = self.client.get("/api/health/", HTTP_ORIGIN="http://localhost:8080")
        self.assertEqual(r.json()["status"], "ok")
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")

    def test_preflight(self):
        r = self.client.options(
            "/api/documents/",
            HTTP_ORIGIN="http://localhost:8080",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="PUT",
        )
        self.assertEqual(r.status_code, 204)
        self.assertIn("X-Client-Id", r["Access-Control-Allow-Headers"])

    @override_settings(CORS_ALLOWED_ORIGINS=["https://app.example"])
    def test_cors_allowlist(self):
        ok = self.client.get("/api/health/", HTTP_ORIGIN="https://app.example")
        self.assertEqual(ok["Access-Control-Allow-Origin"], "https://app.example")
        bad = self.client.get("/api/health/", HTTP_ORIGIN="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", bad)

    @override_settings(RATE_LIMIT_PER_MINUTE=3)
    def test_rate_limit(self):
        codes = [self.client.get("/api/health/").status_code for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])
