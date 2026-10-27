import io
import json
import uuid
import zipfile

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from documents import dom, fileformats, services

A = str(uuid.uuid4())
B = str(uuid.uuid4())


def h(client=A):
    return {"HTTP_X_CLIENT_ID": client}


class ApiCase(TestCase):
    def setUp(self):
        cache.clear()

    def post(self, url, body=None, client=A):
        return self.client.post(url, json.dumps(body or {}), content_type="application/json", **h(client))

    def put(self, url, body, client=A):
        return self.client.put(url, json.dumps(body), content_type="application/json", **h(client))

    def get(self, url, client=A):
        return self.client.get(url, **h(client))

    def delete(self, url, client=A):
        return self.client.delete(url, **h(client))

    def make_doc(self, html="<p>Hello world</p>", **extra):
        r = self.post("/api/documents/", {"title": "Doc", "html": html, **extra})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()["id"]


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------


class DomTests(TestCase):
    def test_roundtrip_and_text(self):
        html = '<h1 class="a">T</h1><p>one<br>two &amp; three</p><ul><li>x</li></ul>'
        self.assertEqual(dom.parse(html).to_html(), html)
        self.assertEqual(dom.html_to_text(html), "T\none\ntwo & three\nx")

    def test_unclosed_tags_do_not_crash(self):
        self.assertIn("a", dom.parse("<p>a<b>b").to_html())


class ServiceTests(TestCase):
    def test_stats(self):
        s = services.document_stats("<p>One two three.</p><p></p><p>Four</p>")
        self.assertEqual((s["words"], s["paragraphs"], s["characters"]), (4, 2, 16))
        self.assertEqual(services.document_stats("<p>x</p>", pages=3)["pages"], 3)

    def test_toc(self):
        toc = services.build_toc("<h1>A</h1><p>x</p><h2>B</h2><h4>C</h4>", (1, 2))
        self.assertEqual([(e["level"], e["text"]) for e in toc["entries"]], [(1, "A"), (2, "B")])
        self.assertIn('class="w-toc"', toc["html"])
        self.assertIn("No table of contents", services.build_toc("<p>x</p>")["html"])

    def test_find_replace_options_and_skips(self):
        html = '<p>Cat cat concatenate</p><div class="w-toc"><p>cat</p></div>'
        self.assertEqual(services.find_replace(html, "cat")[0], 3)
        self.assertEqual(services.find_replace(html, "cat", match_case=True)[0], 2)  # cat + conCATenate
        self.assertEqual(services.find_replace(html, "cat", whole_word=True)[0], 2)
        count, _, new = services.find_replace(html, "cat", whole_word=True, replacement="dog")
        self.assertEqual(count, 2)
        self.assertIn("dog dog concatenate", new)
        self.assertIn("<p>cat</p>", new)  # table of contents untouched

    def test_revisions(self):
        html = 'a<span class="w-rev w-rev-ins" data-rev="r1">NEW</span><span class="w-rev w-rev-del" data-rev="r2">OLD</span>b'
        self.assertEqual(services.resolve_revisions(html, accept=True), "aNEWb")
        self.assertEqual(services.resolve_revisions(html, accept=False), "aOLDb")

    def test_merge(self):
        html = (
            '<p>Dear <span class="w-merge-field" data-merge-field="First">«First»</span>,'
            '<span class="w-merge-rule" data-field="Vip" data-equals="yes" data-if-true="Welcome back" data-if-false="Hello">x</span></p>'
        )
        self.assertEqual(services.merge_fields_in(html), ["First"])
        self.assertIn("Dear Avery,Welcome back", dom.html_to_text(services.merge_record(html, {"First": "Avery", "Vip": "yes"})))
        self.assertIn("Hello", services.merge_record(html, {"First": "Bo", "Vip": "no"}))
        self.assertIn("&lt;script&gt;", services.merge_record(html, {"First": "<script>", "Vip": ""}))
        self.assertIn("«First»".replace("«First»", "<<First>>").replace("<", "&lt;").replace(">", "&gt;"), services.preview_record(html, {}))

    def test_csv(self):
        fields, records = services.parse_csv('A,B\n1,"x, y"\n2,z')
        self.assertEqual(fields, ["A", "B"])
        self.assertEqual(records, [{"A": "1", "B": "x, y"}, {"A": "2", "B": "z"}])
        self.assertEqual(services.parse_csv("only header"), ([], []))

    def test_citations(self):
        src = {"tag": "T", "author": "Doe, J.", "title": "Title", "year": "2024", "publisher": "P"}
        self.assertEqual(services.format_citation(src, "APA"), "(Doe, 2024)")
        self.assertEqual(services.format_citation(src, "MLA"), "(Doe)")
        self.assertEqual(services.format_citation(src, "Chicago"), 'Doe, J. "Title" (2024)')


class FileFormatTests(TestCase):
    HTML = '<h1>Title</h1><p>Some <b>bold</b> and <i>italic</i> text.</p><ul><li>one</li></ul><table><tr><td>c1</td><td>c2</td></tr></table>'

    def test_docx_roundtrip_through_snapshot(self):
        data = fileformats.to_docx(self.HTML, "My doc")
        html, title, kind = fileformats.import_file("x.docx", data)
        self.assertEqual((html, title, kind), (self.HTML, "My doc", "docx"))

    def test_docx_is_valid_wordprocessingml(self):
        data = fileformats.to_docx(self.HTML, "T")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            body = z.read("word/document.xml").decode()
        for expected in ('w:pStyle w:val="Heading1"', "<w:b/>", "<w:i/>", "<w:tbl>", "Some "):
            self.assertIn(expected, body)

    def test_foreign_docx_without_snapshot_is_converted(self):
        data = fileformats.to_docx(self.HTML, "T")
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
            for item in src.namelist():
                if item != "word/greendoc.json":
                    dst.writestr(item, src.read(item))
        html, title, _ = fileformats.import_file("foreign.docx", out.getvalue())
        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<b>bold</b>", html)
        self.assertIn("<td>", html)
        self.assertEqual(title, "T")

    def test_other_formats(self):
        self.assertEqual(fileformats.to_txt(self.HTML).splitlines()[0], "Title")
        self.assertIn(b"\\par", fileformats.export("rtf", self.HTML, "T"))
        self.assertIn(b"w:wordDocument", fileformats.export("xml", self.HTML, "T"))
        self.assertIn("<title>T &amp; U</title>", fileformats.to_html("<p>x</p>", "T & U"))
        self.assertEqual(fileformats.import_file("n.txt", b"a\nb")[0], "<p>a</p><p>b</p>")
        html, title, _ = fileformats.import_file("p.html", b"<html><head><title>PT</title></head><body><p>x</p></body></html>")
        self.assertEqual((html, title), ("<p>x</p>", "PT"))

    def test_bad_imports(self):
        for name, data in (("a.docx", b"not a zip"), ("a.exe", b"x"), ("big.txt", b"x" * (fileformats.MAX_IMPORT_BYTES + 1))):
            with self.assertRaises(fileformats.ImportError_):
                fileformats.import_file(name, data)


# --------------------------------------------------------------------------
# Documents: settings, protection, templates, recent, duplicate, import
# --------------------------------------------------------------------------


class DocumentFeatureTests(ApiCase):
    def test_settings_and_properties_roundtrip_and_merge(self):
        doc = self.make_doc(settings={"pageSize": "Letter", "columns": 2}, properties={"company": "Greenleaf"})
        r = self.put(f"/api/documents/{doc}/", {"settings": {"orientation": "landscape"}, "properties": {"subject": "S"}}).json()
        self.assertEqual(r["settings"], {"pageSize": "Letter", "columns": 2, "orientation": "landscape"})
        self.assertEqual(r["properties"], {"company": "Greenleaf", "subject": "S"})

    def test_settings_validation(self):
        bad = [
            {"pageSize": "A0"}, {"columns": 9}, {"margins": {"top": 1}}, {"pageColor": "red"},
            {"bogus": 1}, {"trackChanges": "yes"}, {"theme": "nope"}, {"language": "not a tag!"},
        ]
        for settings in bad:
            r = self.post("/api/documents/", {"settings": settings})
            self.assertEqual(r.status_code, 400, settings)
        self.assertEqual(self.post("/api/documents/", {"settings": {"theme": "forest", "margins": {"top": 72, "right": 72, "bottom": 72, "left": 72}}}).status_code, 201)

    def test_create_from_template(self):
        r = self.post("/api/documents/", {"templateId": "memo"}).json()
        self.assertEqual((r["template"], r["title"]), ("memo", "Memo"))
        self.assertIn("MEMORANDUM", r["html"])
        self.assertEqual(self.post("/api/documents/", {"templateId": "nope"}).status_code, 400)

    def test_protection_blocks_content_edits_only(self):
        doc = self.make_doc()
        self.put(f"/api/documents/{doc}/", {"protected": True})
        r = self.put(f"/api/documents/{doc}/", {"html": "<p>changed</p>"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "document_protected"))
        self.assertEqual(self.put(f"/api/documents/{doc}/", {"title": "Renamed"}).status_code, 200)
        self.assertEqual(self.post(f"/api/documents/{doc}/find/", {"query": "Hello", "replacement": "x"}).status_code, 403)
        self.assertEqual(self.post(f"/api/documents/{doc}/revisions/accept-all/").status_code, 403)
        self.assertEqual(self.put(f"/api/documents/{doc}/", {"protected": False, "html": "<p>ok</p>"}).status_code, 200)

    def test_duplicate(self):
        doc = self.make_doc(settings={"columns": 3})
        copy = self.post(f"/api/documents/{doc}/duplicate/").json()
        self.assertNotEqual(copy["id"], doc)
        self.assertEqual((copy["title"], copy["settings"]), ("Doc (copy)", {"columns": 3}))
        self.assertEqual(self.post(f"/api/documents/{doc}/duplicate/", client=B).status_code, 404)

    def test_search_by_title(self):
        self.make_doc()
        self.post("/api/documents/", {"title": "Budget 2026"})
        titles = [d["title"] for d in self.get("/api/documents/?q=budget").json()["documents"]]
        self.assertEqual(titles, ["Budget 2026"])

    def test_recent_list_and_clear(self):
        for i in range(10):
            self.post("/api/documents/", {"title": f"D{i}"})
        recent = self.get("/api/recent/").json()["recent"]
        self.assertEqual(len(recent), 8)
        self.assertEqual(recent[0]["title"], "D9")
        self.assertEqual(self.delete("/api/recent/").status_code, 204)
        self.assertEqual(self.get("/api/recent/").json()["recent"], [])
        self.assertEqual(len(self.get("/api/documents/").json()["documents"]), 10)  # docs kept
        self.assertEqual(self.get("/api/recent/", client=B).json()["recent"], [])
        doc = self.get("/api/documents/").json()["documents"][0]["id"]
        self.put(f"/api/documents/{doc}/", {"title": "again"})
        self.assertEqual(len(self.get("/api/recent/").json()["recent"]), 1)

    def test_import_docx_html_txt(self):
        docx = fileformats.to_docx("<p>From docx</p>", "Imported")
        for name, data, expect in (
            ("Imported.docx", docx, "From docx"),
            ("page.html", b"<body><p>From html</p></body>", "From html"),
            ("notes.txt", b"From txt", "From txt"),
        ):
            r = self.client.post("/api/documents/import/", {"file": SimpleUploadedFile(name, data)}, **h())
            self.assertEqual(r.status_code, 201, r.content)
            self.assertIn(expect, r.json()["html"])
            self.assertEqual(r.json()["sourceName"], name)
        recent = self.get("/api/recent/").json()["recent"]
        self.assertEqual(recent[0]["path"], "notes.txt")

    def test_import_errors(self):
        self.assertEqual(self.client.post("/api/documents/import/", {}, **h()).status_code, 400)
        r = self.client.post("/api/documents/import/", {"file": SimpleUploadedFile("a.exe", b"x")}, **h())
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "import_failed"))
        r = self.client.post("/api/documents/import/", {"file": SimpleUploadedFile("a.docx", b"junk")}, **h())
        self.assertEqual(r.status_code, 422)

    def test_imported_html_is_sanitised(self):
        r = self.client.post(
            "/api/documents/import/", {"file": SimpleUploadedFile("x.html", b"<body><p onclick='x()'>a</p><script>b()</script></body>")}, **h()
        )
        self.assertEqual(r.json()["html"], "<p>a</p>")


class ToolsTests(ApiCase):
    def test_stats_toc_find(self):
        doc = self.make_doc("<h1>Intro</h1><p>Colour colour.</p><h2>More</h2><p>x</p>")
        self.assertEqual(self.get(f"/api/documents/{doc}/stats/").json()["words"], 5)
        self.assertEqual(self.get(f"/api/documents/{doc}/stats/?pages=4").json()["pages"], 4)
        self.assertEqual(self.get(f"/api/documents/{doc}/stats/?pages=x").status_code, 400)
        toc = self.get(f"/api/documents/{doc}/toc/?levels=1").json()
        self.assertEqual([e["text"] for e in toc["entries"]], ["Intro"])
        self.assertEqual(self.get(f"/api/documents/{doc}/toc/?levels=9").status_code, 400)
        found = self.post(f"/api/documents/{doc}/find/", {"query": "colour"}).json()
        self.assertEqual((found["count"], found["replaced"]), (2, 0))
        done = self.post(f"/api/documents/{doc}/find/", {"query": "colour", "replacement": "color"}).json()
        self.assertEqual(done["replaced"], 2)
        self.assertIn("color color.", self.get(f"/api/documents/{doc}/").json()["html"])
        self.assertEqual(self.post(f"/api/documents/{doc}/find/", {"query": ""}).status_code, 400)

    def test_tools_are_private(self):
        doc = self.make_doc()
        for path in ("stats", "toc", "spellcheck", "readability", "export", "versions", "comments", "revisions", "mailmerge"):
            self.assertEqual(self.get(f"/api/documents/{doc}/{path}/", client=B).status_code, 404, path)

    def test_stored_document_proofing(self):
        doc = self.make_doc("<p>I recieve teh mail.</p>")
        words = [i["word"] for i in self.get(f"/api/documents/{doc}/spellcheck/").json()["issues"]]
        self.assertEqual(words, ["recieve", "teh"])
        self.assertEqual(self.get(f"/api/documents/{doc}/readability/").json()["words"], 4)

    def test_proofing_stats_endpoint(self):
        r = self.post("/api/proofing/stats/", {"text": "one two\nthree"}).json()
        self.assertEqual((r["words"], r["paragraphs"]), (3, 2))
        self.assertEqual(self.post("/api/proofing/stats/", {"html": "<p>a b</p>"}).json()["words"], 2)
        self.assertEqual(self.post("/api/proofing/stats/", {}).status_code, 400)

    def test_export_formats(self):
        doc = self.make_doc("<h1>Hi</h1><p>there</p>", settings={"pageSize": "Letter", "orientation": "landscape"})
        for fmt, ctype in fileformats.CONTENT_TYPES.items():
            r = self.get(f"/api/documents/{doc}/export/?format={fmt}")
            self.assertEqual(r.status_code, 200, fmt)
            self.assertEqual(r["Content-Type"], ctype)
            self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(self.get(f"/api/documents/{doc}/export/?format=pdf").status_code, 400)
        docx = self.get(f"/api/documents/{doc}/export/").content
        with zipfile.ZipFile(io.BytesIO(docx)) as z:
            xml = z.read("word/document.xml").decode()
        self.assertIn('w:w="15840" w:h="12240" w:orient="landscape"', xml)  # Letter landscape in twips
        self.assertEqual(fileformats.import_file("a.docx", docx)[0], "<h1>Hi</h1><p>there</p>")

    def test_export_filename_is_safe(self):
        doc = self.make_doc()
        self.put(f"/api/documents/{doc}/", {"title": 'a/b:"c"'})
        r = self.get(f"/api/documents/{doc}/export/?format=txt")
        self.assertNotIn("/", r["Content-Disposition"].split("filename*=")[1].replace("UTF-8''", ""))


class VersionTests(ApiCase):
    def test_autosnapshot_manual_and_restore(self):
        doc = self.make_doc("<p>v1</p>")
        versions = self.get(f"/api/documents/{doc}/versions/").json()["versions"]
        self.assertEqual(len(versions), 1)  # first save is snapshotted
        self.put(f"/api/documents/{doc}/", {"html": "<p>v2</p>"})
        self.assertEqual(len(self.get(f"/api/documents/{doc}/versions/").json()["versions"]), 1)  # throttled
        named = self.post(f"/api/documents/{doc}/versions/", {"label": "Draft 2"})
        self.assertEqual(named.status_code, 201)
        self.assertFalse(named.json()["automatic"])
        detail = self.get(f"/api/documents/{doc}/versions/{named.json()['id']}/").json()
        self.assertEqual(detail["html"], "<p>v2</p>")
        self.put(f"/api/documents/{doc}/", {"html": "<p>v3</p>"})
        restored = self.post(f"/api/documents/{doc}/versions/{named.json()['id']}/restore/").json()
        self.assertEqual(restored["html"], "<p>v2</p>")
        labels = [v["label"] for v in self.get(f"/api/documents/{doc}/versions/").json()["versions"]]
        self.assertIn("Before restore", labels)
        v3 = [v for v in self.get(f"/api/documents/{doc}/versions/").json()["versions"] if v["label"] == "Before restore"][0]
        self.assertEqual(self.get(f"/api/documents/{doc}/versions/{v3['id']}/").json()["html"], "<p>v3</p>")
        self.assertEqual(self.delete(f"/api/documents/{doc}/versions/{named.json()['id']}/").status_code, 204)

    def test_manual_versions_are_capped(self):
        from documents.common import MAX_MANUAL_VERSIONS

        doc = self.make_doc()
        for _ in range(MAX_MANUAL_VERSIONS + 3):
            self.post(f"/api/documents/{doc}/versions/", {})
            cache.clear()
        manual = [v for v in self.get(f"/api/documents/{doc}/versions/").json()["versions"] if not v["automatic"]]
        self.assertEqual(len(manual), MAX_MANUAL_VERSIONS)

    def test_versions_deleted_with_document(self):
        from documents.models import DocumentVersion

        doc = self.make_doc()
        self.delete(f"/api/documents/{doc}/")
        self.assertEqual(DocumentVersion.objects.count(), 0)


class MailMergeTests(ApiCase):
    HTML = '<p>Hi <span class="w-merge-field" data-merge-field="Name">«Name»</span></p>'

    def test_full_flow(self):
        doc = self.make_doc(self.HTML)
        self.assertEqual(self.get(f"/api/documents/{doc}/mailmerge/").json()["count"], 0)
        self.assertEqual(self.get(f"/api/documents/{doc}/mailmerge/preview/").status_code, 409)
        r = self.put(f"/api/documents/{doc}/mailmerge/", {"csv": "Name,Email\nAvery,a@x.com\nMorgan,m@x.com"}).json()
        self.assertEqual((r["fields"], r["count"], r["fieldsInDocument"]), (["Name", "Email"], 2, ["Name"]))
        p = self.get(f"/api/documents/{doc}/mailmerge/preview/?index=1").json()
        self.assertIn("Morgan", p["html"])
        self.assertEqual(self.get(f"/api/documents/{doc}/mailmerge/preview/?index=-1").json()["index"], 1)  # wraps
        self.assertEqual(self.get(f"/api/documents/{doc}/mailmerge/preview/?index=2").json()["index"], 0)
        fin = self.get(f"/api/documents/{doc}/mailmerge/finish/")
        body = fin.content.decode()
        self.assertEqual(body.count('class="merged-copy"'), 2)
        self.assertIn("attachment", fin["Content-Disposition"])
        self.assertEqual(self.get(f"/api/documents/{doc}/mailmerge/finish/?format=json").json()["count"], 2)
        self.assertEqual(self.delete(f"/api/documents/{doc}/mailmerge/").status_code, 204)
        self.assertEqual(self.get(f"/api/documents/{doc}/mailmerge/").json()["count"], 0)

    def test_fields_and_records_form_and_validation(self):
        doc = self.make_doc(self.HTML)
        ok = self.put(f"/api/documents/{doc}/mailmerge/", {"fields": ["Name"], "records": [{"Name": "A", "Extra": "dropped"}]})
        self.assertEqual(ok.json()["records"], [{"Name": "A"}])
        for bad in ({"csv": "just header"}, {"csv": 5}, {"fields": ["A", "A"], "records": []}, {"fields": ["A"], "records": "x"}, {"fields": ["A"], "records": [1]}, {}):
            self.assertEqual(self.put(f"/api/documents/{doc}/mailmerge/", bad).status_code, 400, bad)

    def test_merge_does_not_modify_the_stored_document(self):
        doc = self.make_doc(self.HTML)
        self.put(f"/api/documents/{doc}/mailmerge/", {"csv": "Name\nAvery"})
        self.get(f"/api/documents/{doc}/mailmerge/finish/")
        self.assertIn("«Name»", self.get(f"/api/documents/{doc}/").json()["html"])


class ReviewTests(ApiCase):
    def test_comments_lifecycle(self):
        doc = self.make_doc('<p>See <span class="w-comment-ref" data-cid="c1">this</span></p>')
        r = self.post(f"/api/documents/{doc}/comments/", {"id": "c1", "text": "Check", "quote": "this", "author": "Amina Ndlovu"})
        self.assertEqual(r.status_code, 201)
        c = r.json()
        self.assertEqual((c["id"], c["author"], c["initials"], c["resolved"], c["replies"]), ("c1", "Amina Ndlovu", "AN", False, []))
        self.assertEqual(self.post(f"/api/documents/{doc}/comments/", {"id": "c1", "text": "dup"}).status_code, 409)
        rep = self.post(f"/api/documents/{doc}/comments/c1/replies/", {"id": "r1", "text": "Done"}).json()
        self.assertEqual(rep["replies"][0]["text"], "Done")
        self.assertEqual(self.put(f"/api/documents/{doc}/comments/c1/", {"resolved": True}).json()["resolved"], True)
        self.assertEqual(len(self.get(f"/api/documents/{doc}/comments/?resolved=false").json()["comments"]), 0)
        self.assertEqual(len(self.get(f"/api/documents/{doc}/comments/?resolved=true").json()["comments"]), 1)
        self.assertEqual(self.delete(f"/api/documents/{doc}/comments/c1/replies/r1/").status_code, 204)
        self.assertEqual(self.get(f"/api/documents/{doc}/comments/c1/").json()["replies"], [])
        self.assertEqual(self.delete(f"/api/documents/{doc}/comments/c1/").status_code, 204)
        self.assertEqual(self.get(f"/api/documents/{doc}/").json()["html"], "<p>See this</p>")  # marker removed, text kept

    def test_comment_validation_and_default_author(self):
        doc = self.make_doc()
        for bad in ({}, {"text": "  "}, {"text": "x" * 5001}, {"text": "x", "author": ""}, {"text": "x", "quote": 5}, {"text": "x", "id": "i" * 65}):
            self.assertEqual(self.post(f"/api/documents/{doc}/comments/", bad).status_code, 400, bad)
        self.put("/api/preferences/", {"authorName": "Lerato Mokoena"})
        c = self.post(f"/api/documents/{doc}/comments/", {"text": "hi"}).json()
        self.assertEqual((c["author"], c["initials"]), ("Lerato Mokoena", "LM"))
        self.assertTrue(c["id"].startswith("cmt-"))
        self.assertEqual(self.put(f"/api/documents/{doc}/comments/{c['id']}/", {"bogus": 1}).status_code, 400)

    def test_comments_allowed_on_protected_documents(self):
        doc = self.make_doc()
        self.put(f"/api/documents/{doc}/", {"protected": True})
        self.assertEqual(self.post(f"/api/documents/{doc}/comments/", {"text": "still fine"}).status_code, 201)

    def test_revisions_and_accept_reject(self):
        html = 'a<span class="w-rev w-rev-ins" data-rev="r1">NEW</span><span class="w-rev w-rev-del" data-rev="r2">OLD</span>b'
        doc = self.make_doc(html, settings={"trackChanges": True})
        self.post(f"/api/documents/{doc}/revisions/", {"id": "r1", "kind": "insert", "text": "NEW"})
        self.post(f"/api/documents/{doc}/revisions/", {"id": "r2", "kind": "delete", "text": "OLD"})
        listing = self.get(f"/api/documents/{doc}/revisions/").json()
        self.assertEqual((listing["trackChanges"], [r["kind"] for r in listing["revisions"]]), (True, ["insert", "delete"]))
        self.assertEqual(self.post(f"/api/documents/{doc}/revisions/", {"kind": "nope"}).status_code, 400)
        self.assertEqual(self.post(f"/api/documents/{doc}/revisions/", {"id": "r1", "kind": "insert"}).status_code, 409)
        copy = self.post(f"/api/documents/{doc}/duplicate/").json()["id"]
        rejected = self.post(f"/api/documents/{doc}/revisions/reject-all/").json()
        self.assertEqual((rejected["resolved"], rejected["html"]), (2, "aOLDb"))
        self.assertEqual(self.get(f"/api/documents/{doc}/revisions/").json()["revisions"], [])
        self.assertEqual(self.post(f"/api/documents/{copy}/revisions/accept-all/").json()["html"], "aNEWb")

    def test_delete_single_revision_entry(self):
        doc = self.make_doc()
        self.post(f"/api/documents/{doc}/revisions/", {"id": "r9", "kind": "format"})
        self.assertEqual(self.delete(f"/api/documents/{doc}/revisions/r9/").status_code, 204)
        self.assertEqual(self.delete(f"/api/documents/{doc}/revisions/r9/").status_code, 404)

    def test_comments_deleted_with_document_and_private(self):
        from documents.models import Comment

        doc = self.make_doc()
        self.post(f"/api/documents/{doc}/comments/", {"text": "x"})
        self.assertEqual(self.post(f"/api/documents/{doc}/comments/", {"text": "y"}, client=B).status_code, 404)
        self.delete(f"/api/documents/{doc}/")
        self.assertEqual(Comment.objects.count(), 0)
