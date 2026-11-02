import json
import uuid
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from learning.models import LearningProfile, TermStat
from learning.services import claim_guest_profile

PEOPLE = [str(uuid.uuid4()) for _ in range(5)]


def profile(**extra):
    return {
        "vocab": {"zorbatron": 4, "table": 9},
        "names": {"amalitech": 3},
        "fixes": {"recieved": {"received": 2}},
        "serial": [3, 0],
        "ngrams": {"thank you for": 5},
        **extra,
    }


class Base(TestCase):
    def setUp(self):
        cache.clear()

    def call(self, method, url, body=None, who=PEOPLE[0]):
        kwargs = {"HTTP_X_CLIENT_ID": who}
        if body is not None:
            kwargs["data"] = json.dumps(body)
            kwargs["content_type"] = "application/json"
        return getattr(self.client, method)(url, **kwargs)

    def put(self, body, who=PEOPLE[0]):
        return self.call("put", "/api/learning/profile/", body, who)

    def global_model(self):
        cache.clear()
        return self.client.get("/api/learning/global/").json()


class ProfileTests(Base):
    def test_empty_until_uploaded_then_roundtrips(self):
        r = self.call("get", "/api/learning/profile/").json()
        self.assertEqual((r["vocab"], r["shared"], r["updatedAt"]), ({}, False, None))
        r = self.put(profile()).json()
        self.assertEqual(r["vocab"]["zorbatron"], 4)
        self.assertEqual(r["fixes"], {"recieved": {"received": 2}})
        self.assertEqual(self.call("get", "/api/learning/profile/").json()["names"], {"amalitech": 3})

    def test_each_person_has_their_own_profile(self):
        self.put(profile())
        self.assertEqual(self.call("get", "/api/learning/profile/", who=PEOPLE[1]).json()["vocab"], {})

    def test_devices_merge_by_the_larger_count(self):
        self.put({"vocab": {"alpha": 3, "beta": 2}, "ignores": ["spelling:foo"]})
        r = self.put({"vocab": {"alpha": 2, "gamma": 5}, "ignores": ["spelling:bar"]}).json()
        self.assertEqual(r["vocab"], {"alpha": 3, "beta": 2, "gamma": 5})
        self.assertEqual(sorted(r["ignores"]), ["spelling:bar", "spelling:foo"])

    def test_delete_erases_the_private_profile(self):
        self.put(profile())
        self.assertEqual(self.call("delete", "/api/learning/profile/").status_code, 204)
        self.assertEqual(self.call("get", "/api/learning/profile/").json()["vocab"], {})

    def test_requires_a_client_id(self):
        self.assertEqual(self.client.get("/api/learning/profile/").status_code, 400)

    def test_rejects_anything_that_is_not_derived_counts(self):
        bad = [
            {"vocab": {"hello world": 1}},  # not a single word
            {"vocab": {"call555": 1}},  # digits
            {"vocab": {"a@b.com": 1}},
            {"vocab": {"word": 0}},
            {"vocab": {"word": True}},
            {"vocab": ["word"]},
            {"ngrams": {"two words": 1}},
            {"ngrams": {"bad words 1": 1}},
            {"fixes": {"x": 1}},
            {"ignores": ["no colon here"]},
            {"serial": [1]},
            {"sentences": ["Dear John, my full private letter."]},
            {"shared": "yes"},
            {"vocab": {"x" * 40: 1}},
        ]
        for body in bad:
            self.assertEqual(self.put(body).status_code, 400, body)
        self.assertEqual(self.put({"vocab": {"fine": 1}}).status_code, 200)

    def test_too_large_is_refused(self):
        response = self.client.put(
            "/api/learning/profile/", data="x" * 1_100_000, content_type="application/json", HTTP_X_CLIENT_ID=PEOPLE[0]
        )
        self.assertEqual(response.status_code, 413)


class SharedEngineTests(Base):
    def test_nothing_is_shared_without_opt_in(self):
        for who in PEOPLE:
            self.put(profile(), who)
        self.assertEqual(TermStat.objects.count(), 0)
        g = self.global_model()
        self.assertEqual((g["contributors"], g["words"]), (0, []))

    def test_a_term_joins_the_dictionary_only_when_enough_different_people_used_it(self):
        self.put(profile(shared=True), PEOPLE[0])
        self.put(profile(shared=True), PEOPLE[1])
        self.assertNotIn("zorbatron", self.global_model()["words"])  # two people: still not published
        self.put(profile(shared=True), PEOPLE[2])
        g = self.global_model()
        self.assertEqual(g["contributors"], 3)
        self.assertIn("zorbatron", g["words"])
        self.assertIn("amalitech", g["names"])
        self.assertEqual(g["fixes"], {"recieved": "received"})
        self.assertIn("thank you for", g["ngrams"])

    def test_one_person_cannot_publish_a_word_alone(self):
        for n in range(1, 40):
            self.put({"vocab": {"secretproject": n + 1}, "shared": True}, PEOPLE[0])
        row = TermStat.objects.get(kind="word", key="secretproject")
        self.assertEqual(row.contributors, 1)
        self.assertGreater(row.count, 5)
        self.assertNotIn("secretproject", self.global_model()["words"])

    def test_repeat_uploads_do_not_double_count(self):
        for _ in range(4):
            self.put(profile(shared=True))
        self.assertEqual(TermStat.objects.get(kind="word", key="zorbatron").count, 4)
        self.put({"vocab": {"zorbatron": 7}})
        self.assertEqual(TermStat.objects.get(kind="word", key="zorbatron").count, 7)

    def test_the_engine_stores_no_identity(self):
        self.put(profile(shared=True))
        names = {f.name for f in TermStat._meta.get_fields()}
        self.assertNotIn("client_id", names)
        self.assertNotIn("owner", names)

    def test_opting_out_stops_future_contributions_but_keeps_the_pool(self):
        self.put(profile(shared=True))
        self.put({"shared": False})
        self.put({"vocab": {"zorbatron": 50}})
        self.assertEqual(TermStat.objects.get(kind="word", key="zorbatron").count, 4)
        self.call("delete", "/api/learning/profile/")
        self.assertTrue(TermStat.objects.filter(kind="word", key="zorbatron").exists())

    def test_sharing_later_contributes_what_was_already_learned(self):
        self.put(profile())
        self.assertEqual(TermStat.objects.count(), 0)
        self.put({"shared": True})
        self.assertEqual(TermStat.objects.get(kind="word", key="zorbatron").count, 4)

    @override_settings(LEARNING_MIN_CONTRIBUTORS=1)
    def test_the_threshold_is_configurable(self):
        self.put(profile(shared=True))
        self.assertIn("zorbatron", self.global_model()["words"])

    def test_the_global_model_is_public(self):
        self.assertEqual(self.client.get("/api/learning/global/").status_code, 200)


class GuestClaimTests(Base):
    def test_signing_in_moves_the_learned_profile(self):
        guest, owner = uuid.uuid4(), uuid.uuid4()
        LearningProfile.objects.create(client_id=guest, data={"vocab": {"alpha": 3}}, shared=True)
        LearningProfile.objects.create(client_id=owner, data={"vocab": {"alpha": 1, "beta": 2}})
        claim_guest_profile(guest, owner)
        mine = LearningProfile.objects.get(client_id=owner)
        self.assertEqual(mine.data["vocab"], {"alpha": 3, "beta": 2})
        self.assertTrue(mine.shared)
        self.assertFalse(LearningProfile.objects.filter(client_id=guest).exists())


class ExportCommandTests(Base):
    def test_exports_established_words(self):
        TermStat.objects.create(kind="word", key="zorbatron", count=9, contributors=4)
        TermStat.objects.create(kind="word", key="rare", count=9, contributors=1)
        TermStat.objects.create(kind="name", key="amalitech", count=9, contributors=4)
        out = StringIO()
        call_command("export_dictionary", stdout=out)
        self.assertEqual(out.getvalue().split(), ["zorbatron"])
        out = StringIO()
        call_command("export_dictionary", "--names", stdout=out)
        self.assertEqual(sorted(out.getvalue().split()), ["Amalitech", "zorbatron"])
