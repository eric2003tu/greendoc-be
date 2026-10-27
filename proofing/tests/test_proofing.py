import json

from django.core.cache import cache
from django.test import TestCase


def post(client, path, body):
    return client.post(path, json.dumps(body), content_type="application/json")


class ProofingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_spellcheck_finds_issues_with_positions_and_case(self):
        r = post(self.client, "/api/proofing/spellcheck/", {"text": "I will Recieve it teh day."})
        issues = r.json()["issues"]
        self.assertEqual([i["word"] for i in issues], ["Recieve", "teh"])
        self.assertEqual(issues[0]["index"], 7)
        self.assertEqual(issues[0]["suggestions"][0], "Receive")
        self.assertEqual(issues[1]["suggestions"][0], "the")

    def test_spellcheck_clean_text(self):
        r = post(self.client, "/api/proofing/spellcheck/", {"text": "All good here."})
        self.assertEqual(r.json()["issues"], [])

    def test_spellcheck_validation(self):
        self.assertEqual(post(self.client, "/api/proofing/spellcheck/", {}).status_code, 400)
        big = {"text": "a" * 200_001}
        self.assertEqual(post(self.client, "/api/proofing/spellcheck/", big).status_code, 413)

    def test_synonyms(self):
        r = self.client.get("/api/proofing/synonyms/?word=Good")
        self.assertIn("excellent", r.json()["synonyms"])
        self.assertEqual(self.client.get("/api/proofing/synonyms/?word=zzzz").json()["synonyms"], [])
        self.assertEqual(self.client.get("/api/proofing/synonyms/").status_code, 400)

    def test_readability(self):
        r = post(self.client, "/api/proofing/readability/", {"text": "The cat sat. The dog ran."})
        data = r.json()
        self.assertEqual(data["words"], 6)
        self.assertEqual(data["sentences"], 2)
        self.assertGreater(data["fleschReadingEase"], 90)
        empty = post(self.client, "/api/proofing/readability/", {"text": ""}).json()
        self.assertIsNone(empty["fleschReadingEase"])
