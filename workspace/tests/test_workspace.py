import json
import uuid

from django.core.cache import cache
from django.test import TestCase

A = str(uuid.uuid4())
B = str(uuid.uuid4())


def h(client=A):
    return {"HTTP_X_CLIENT_ID": client}


class Base(TestCase):
    def setUp(self):
        cache.clear()

    def send(self, method, url, body=None, client=A):
        kwargs = {"content_type": "application/json", **h(client)}
        data = json.dumps(body) if body is not None else None
        return getattr(self.client, method)(url, data, **kwargs) if data else getattr(self.client, method)(url, **h(client))


class PreferencesTests(Base):
    def test_defaults_update_and_reset(self):
        r = self.send("get", "/api/preferences/").json()
        self.assertEqual((r["dark"], r["authorName"], r["autoSave"]), (False, "GreenDoc User", True))
        r = self.send("put", "/api/preferences/", {"dark": True, "authorName": "Merci"}).json()
        self.assertEqual((r["dark"], r["authorName"], r["showRuler"]), (True, "Merci", True))
        self.send("put", "/api/preferences/", {"showRuler": False})
        r = self.send("get", "/api/preferences/").json()
        self.assertEqual((r["dark"], r["showRuler"]), (True, False))  # partial update keeps earlier values
        self.assertEqual(self.send("get", "/api/preferences/", client=B).json()["dark"], False)  # per client
        self.assertEqual(self.send("delete", "/api/preferences/").status_code, 204)
        self.assertEqual(self.send("get", "/api/preferences/").json()["dark"], False)

    def test_validation(self):
        for bad in ({"dark": "yes"}, {"authorName": ""}, {"authorName": "x" * 61}, {"bogus": 1}, {"defaultDesign": "nope"}, {"ribbonDisplay": "x"}, {"defaultFontSize": 500}, []):
            self.assertEqual(self.send("put", "/api/preferences/", bad).status_code, 400, bad)
        self.assertEqual(self.send("put", "/api/preferences/", {"defaultDesign": "sage", "ribbonDisplay": "tabs"}).status_code, 200)

    def test_requires_client_id(self):
        self.assertEqual(self.client.get("/api/preferences/").status_code, 400)


class SourceTests(Base):
    def test_defaults_seeded_once(self):
        tags = [s["tag"] for s in self.send("get", "/api/sources/").json()["sources"]]
        self.assertEqual(tags, ["Doe24", "Smith19", "Lee22"])
        for tag in tags:
            self.send("delete", f"/api/sources/{tag}/")
        self.assertEqual(self.send("get", "/api/sources/").json()["sources"], [])  # not re-seeded

    def test_crud(self):
        body = {"tag": "Khan21", "type": "Book", "author": "Khan, R.", "title": "Writing well", "year": "2021", "publisher": "Green Press"}
        self.assertEqual(self.send("post", "/api/sources/", body).status_code, 201)
        self.assertEqual(self.send("post", "/api/sources/", body).status_code, 409)
        self.assertEqual(self.send("get", "/api/sources/Khan21/").json()["title"], "Writing well")
        r = self.send("put", "/api/sources/Khan21/", {"year": "2022"}).json()
        self.assertEqual((r["year"], r["author"]), ("2022", "Khan, R."))
        self.assertEqual(self.send("put", "/api/sources/Khan21/", {"tag": "Doe24"}).status_code, 409)
        self.assertEqual(self.send("put", "/api/sources/Khan21/", {"tag": "Khan22"}).json()["tag"], "Khan22")
        self.assertEqual(self.send("delete", "/api/sources/Khan22/").status_code, 204)
        self.assertEqual(self.send("get", "/api/sources/Khan22/").status_code, 404)

    def test_validation_and_privacy(self):
        self.assertEqual(self.send("post", "/api/sources/", {"tag": "x"}).status_code, 400)
        self.assertEqual(self.send("post", "/api/sources/", {"tag": "", "author": "a", "title": "t"}).status_code, 400)
        self.assertEqual(self.send("post", "/api/sources/", {"tag": "t", "author": "a", "title": "t", "x": 1}).status_code, 400)
        self.send("post", "/api/sources/", {"tag": "Mine", "author": "Me, M.", "title": "T"})
        tags_b = [s["tag"] for s in self.send("get", "/api/sources/", client=B).json()["sources"]]
        self.assertNotIn("Mine", tags_b)

    def test_citations_and_bibliography(self):
        r = self.send("post", "/api/citations/format/", {"tag": "Doe24", "style": "APA"}).json()
        self.assertEqual(r["text"], "(Doe, 2024)")
        self.assertIn('class="w-citation" data-tag="Doe24"', r["html"])
        self.assertEqual(self.send("post", "/api/citations/format/", {"tag": "Smith19", "style": "MLA"}).json()["text"], "(Smith)")
        self.assertEqual(self.send("post", "/api/citations/format/", {"tag": "Lee22", "style": "Chicago"}).json()["text"], 'Lee, K. "Usability of ribbon interfaces" (2022)')
        self.assertEqual(self.send("post", "/api/citations/format/", {"tag": "nope"}).status_code, 404)
        self.assertEqual(self.send("post", "/api/citations/format/", {"tag": "Doe24", "style": "x"}).status_code, 400)
        bib = self.send("get", "/api/bibliography/").json()
        self.assertEqual(bib["count"], 3)
        self.assertIn("w-bibliography", bib["html"])
        self.assertIn('Doe, J. "On document automation." Journal of Productivity, 2024.', bib["html"])

    def test_html_in_sources_is_escaped(self):
        self.send("post", "/api/sources/", {"tag": "X", "author": "<b>A</b>, B.", "title": "<script>x</script>"})
        bib = self.send("get", "/api/bibliography/").json()["html"]
        self.assertNotIn("<script>", bib)
        self.assertNotIn("<b>A</b>", bib)


class CatalogTests(Base):
    def test_templates(self):
        r = self.client.get("/api/catalog/templates/").json()
        self.assertEqual(len(r["templates"]), 17)
        self.assertEqual(r["categories"][0], "General")
        self.assertNotIn("html", r["templates"][0])
        ids = [t["id"] for t in self.client.get("/api/catalog/templates/?category=Business").json()["templates"]]
        self.assertTrue(ids and all(i not in ("blank",) for i in ids))
        self.assertEqual([t["id"] for t in self.client.get("/api/catalog/templates/?q=memo").json()["templates"]], ["memo"])
        one = self.client.get("/api/catalog/templates/resume/").json()
        self.assertIn("<h1>", one["html"])
        self.assertEqual(self.client.get("/api/catalog/templates/nope/").status_code, 404)

    def test_every_template_html_survives_sanitising(self):
        from catalog import data
        from documents.sanitize import sanitize_html

        for t in data.templates():
            self.assertEqual(sanitize_html(t["html"]).replace("<br>", ""), t["html"].replace("<br>", ""), t["id"])

    def test_design_page_setup_reference(self):
        d = self.client.get("/api/catalog/design/").json()
        self.assertEqual((len(d["themes"]), len(d["styles"])), (8, 15))
        self.assertEqual(d["themes"][0]["id"], "greenleaf")
        self.assertEqual(d["styles"][2]["name"], "Heading 1")
        p = self.client.get("/api/catalog/page-setup/").json()
        self.assertEqual({s["id"] for s in p["pageSizes"]}, {"A4", "Letter", "Legal", "A5", "Executive", "Tabloid"})
        self.assertEqual(p["marginPresets"][0]["margins"], {"top": 96, "right": 96, "bottom": 96, "left": 96})
        ref = self.client.get("/api/catalog/reference/").json()
        self.assertIn("Calibri", ref["fonts"])
        self.assertEqual(len(ref["shortcuts"]), 14)
