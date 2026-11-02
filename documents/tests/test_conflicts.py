import json
import uuid

from django.core.cache import cache
from django.test import TestCase

OWNER = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


class EditConflictTests(TestCase):
    """Saving from a device that was offline must never silently overwrite newer work."""

    def setUp(self):
        cache.clear()
        self.doc_id = str(uuid.uuid4())

    def put(self, body, client=OWNER):
        return self.client.put(
            f"/api/documents/{self.doc_id}/",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_X_CLIENT_ID=client,
        )

    def get(self, client=OWNER):
        return self.client.get(f"/api/documents/{self.doc_id}/", HTTP_X_CLIENT_ID=client).json()

    def test_saving_from_the_version_you_started_with_works_and_returns_the_new_version(self):
        first = self.put({"title": "Report", "html": "<p>one</p>"})
        self.assertEqual(first.status_code, 201)
        base = first.json()["updatedAt"]
        second = self.put({"html": "<p>two</p>", "baseUpdatedAt": base})
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(second.json()["updatedAt"], base)
        self.assertEqual(self.get()["html"], "<p>two</p>")

    def test_a_save_based_on_an_old_version_is_refused_and_changes_nothing(self):
        old = self.put({"title": "Report", "html": "<p>one</p>"}).json()["updatedAt"]
        self.put({"html": "<p>edited on the laptop</p>"})  # another device saved meanwhile
        refused = self.put({"html": "<p>edited offline on the phone</p>", "baseUpdatedAt": old})
        self.assertEqual(refused.status_code, 409)
        error = refused.json()["error"]
        self.assertEqual(error["code"], "edit_conflict")
        self.assertEqual(error["current"]["id"], self.doc_id)
        self.assertEqual(error["current"]["title"], "Report")
        self.assertEqual(self.get()["html"], "<p>edited on the laptop</p>")

    def test_the_conflict_response_carries_the_newer_version_stamp(self):
        old = self.put({"html": "<p>a</p>"}).json()["updatedAt"]
        newer = self.put({"html": "<p>b</p>"}).json()["updatedAt"]
        error = self.put({"html": "<p>c</p>", "baseUpdatedAt": old}).json()["error"]
        self.assertEqual(error["current"]["updatedAt"], newer)
        # that stamp is what lets the app choose "keep mine" without losing the other version by accident
        forced = self.put({"html": "<p>c</p>", "baseUpdatedAt": error["current"]["updatedAt"]})
        self.assertEqual(forced.status_code, 200)

    def test_leaving_out_the_base_overwrites_as_before(self):
        self.put({"html": "<p>a</p>"})
        self.put({"html": "<p>b</p>"})
        self.assertEqual(self.put({"html": "<p>c</p>"}).status_code, 200)
        self.assertEqual(self.get()["html"], "<p>c</p>")

    def test_a_base_on_a_new_document_is_ignored(self):
        r = self.put({"html": "<p>new</p>", "baseUpdatedAt": "2020-01-01T00:00:00+00:00"})
        self.assertEqual(r.status_code, 201)

    def test_a_bad_base_is_a_clear_error(self):
        self.put({"html": "<p>a</p>"})
        for bad in ("yesterday", 5, ["x"]):
            r = self.put({"html": "<p>b</p>", "baseUpdatedAt": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(r.json()["error"]["code"], "invalid_field")

    def test_someone_elses_document_is_still_not_found_not_a_conflict(self):
        self.put({"html": "<p>a</p>"})
        r = self.put({"html": "<p>x</p>", "baseUpdatedAt": "2020-01-01T00:00:00+00:00"}, client=OTHER)
        self.assertEqual(r.status_code, 404)
