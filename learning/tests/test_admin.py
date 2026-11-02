import uuid
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts import services
from learning.models import EngineDay, LearningProfile, TermStat
from learning.services import save_profile
from learning.stats import engine_summary

User = get_user_model()
URL = "/admin/learning/termstat/"


def profile():
    return {"vocab": {"zorbatron": 4}, "names": {"amalitech": 3}, "ngrams": {"thank you for": 2}}


class EngineActivityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_each_upload_is_counted_for_the_day(self):
        save_profile(uuid.uuid4(), {**profile(), "shared": True})
        save_profile(uuid.uuid4(), {**profile(), "shared": True})
        day = EngineDay.objects.get(day=timezone.localdate())
        self.assertEqual(day.uploads, 2)
        self.assertEqual(day.new_terms, 3)  # the second person added no new terms, only uses
        self.assertEqual(day.uses_added, 2 * (4 + 3 + 2))

    def test_private_profiles_do_not_count(self):
        save_profile(uuid.uuid4(), profile())
        self.assertEqual(EngineDay.objects.count(), 0)

    def test_summary_numbers(self):
        for _ in range(3):
            save_profile(uuid.uuid4(), {**profile(), "shared": True})
        save_profile(uuid.uuid4(), {"vocab": {"rareword": 5}, "shared": True})
        s = engine_summary()
        self.assertEqual(s["opted_in"], 4)
        self.assertEqual(s["terms"], 4)  # zorbatron, amalitech, thank you for, rareword
        self.assertEqual(s["published"], 3)  # rareword has only one person
        self.assertEqual(s["new_this_week"], 4)
        words = next(k for k in s["kinds"] if k["label"] == "Words")
        self.assertEqual((words["total"], words["published"], words["pending"]), (2, 1, 1))
        self.assertEqual([t.key for t in s["top_words"]], ["zorbatron"])
        self.assertEqual(len(s["series"]), 30)
        self.assertEqual(s["series"][-1]["total"], 4)
        self.assertEqual(s["series"][-1]["new_terms"], 4)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])  # fast, tests only
class AdminDashboardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("boss@example.com", "boss@example.com", "pw-not-checked-1")
        for _ in range(3):
            save_profile(uuid.uuid4(), {**profile(), "shared": True})

    def test_staff_see_the_dashboard(self):
        self.client.force_login(self.admin)
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Training engine", body)
        self.assertIn("zorbatron", body)
        self.assertIn("terms learned from everyone", body)
        self.assertIn("Most agreed-on words", body)

    def test_filters_and_search_still_work(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(URL, {"status": "published"}).status_code, 200)
        self.assertEqual(self.client.get(URL, {"status": "pending"}).status_code, 200)
        self.assertEqual(self.client.get(URL, {"q": "zorb"}).status_code, 200)

    def test_a_term_can_be_removed_but_not_added_or_edited(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(URL + "add/").status_code, 403)
        term = TermStat.objects.get(key="zorbatron")
        change = self.client.get(f"{URL}{term.pk}/change/")
        self.assertEqual(change.status_code, 200)
        self.assertNotIn(b'name="count"', change.content)  # read-only
        self.client.post(f"{URL}{term.pk}/delete/", {"post": "yes"})
        self.assertFalse(TermStat.objects.filter(key="zorbatron").exists())

    def test_everyone_else_is_sent_to_sign_in(self):
        self.assertEqual(self.client.get(URL).status_code, 302)  # anonymous
        ordinary = User.objects.create_user("user@example.com", "user@example.com", "pw")
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(URL).status_code, 302)  # signed in but not staff

    def test_the_admin_never_exposes_private_profiles(self):
        LearningProfile.objects.create(client_id=uuid.uuid4(), data={"vocab": {"privateword": 9}})
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/admin/learning/learningprofile/").status_code, 404)
        self.assertNotIn(b"privateword", self.client.get(URL).content)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])  # fast, tests only
class SeedAdminTests(TestCase):
    def run_seed(self, **overrides):
        args = dict(email="Eric@Example.com", first_name="Eric", last_name="TUYISHIME", password="223000313")
        args.update(overrides)
        out = StringIO()
        call_command("seed_admin", stdout=out, **args)
        return out.getvalue()

    def test_creates_an_administrator_who_can_sign_in_to_admin_and_the_app(self):
        self.assertIn("Created administrator Eric TUYISHIME", self.run_seed())
        user = User.objects.get(username="eric@example.com")
        self.assertTrue(user.is_staff and user.is_superuser and user.is_active)
        self.assertTrue(user.check_password("223000313"))
        self.assertEqual((user.first_name, user.last_name), ("Eric", "TUYISHIME"))
        self.assertTrue(hasattr(user, "profile"))
        login = self.client.post(
            "/api/auth/login/",
            data='{"email": "eric@example.com", "password": "223000313"}',
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)

    def test_running_again_resets_instead_of_duplicating(self):
        self.run_seed()
        self.assertIn("Updated administrator", self.run_seed(password="another-pass-9"))
        self.assertEqual(User.objects.filter(username="eric@example.com").count(), 1)
        self.assertTrue(User.objects.get(username="eric@example.com").check_password("another-pass-9"))

    def test_promotes_an_existing_ordinary_account(self):
        User.objects.create_user("eric@example.com", "eric@example.com", "old-pass-1")
        self.run_seed()
        self.assertTrue(User.objects.get(username="eric@example.com").is_superuser)

    def test_needs_a_password_and_a_valid_email(self):
        with self.assertRaises(CommandError):
            self.run_seed(password=None)
        with self.assertRaises(Exception):
            self.run_seed(email="not-an-email")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])  # fast, tests only
class EngineApiBase(TestCase):
    """Shared set-up for the in-app Engine page tests: administrators only, enforced by the server."""

    def setUp(self):
        cache.clear()
        for _ in range(3):
            save_profile(uuid.uuid4(), {**profile(), "shared": True})
        call_command("seed_admin", email="boss@example.com", first_name="Eric", last_name="TUYISHIME", password="223000313", stdout=StringIO())
        services.create_user("user@example.com", "Una", "User", "Plain-pass-2026!")

    def token(self, email, password):
        r = self.client.post(
            "/api/auth/login/",
            data=f'{{"email": "{email}", "password": "{password}"}}',
            content_type="application/json",
            HTTP_X_CLIENT_ID=str(uuid.uuid4()),
        )
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def get_stats(self, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get("/api/learning/stats/", **headers)


class EngineStatsApiTests(EngineApiBase):
    def test_login_tells_the_app_who_is_an_administrator(self):
        self.assertTrue(self.token("boss@example.com", "223000313")["user"]["isAdmin"])

    def test_administrators_get_the_numbers(self):
        r = self.get_stats(self.token("boss@example.com", "223000313")["token"])
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual((data["terms"], data["published"], data["optedIn"]), (3, 3, 3))
        self.assertEqual(len(data["series"]), 30)
        self.assertEqual(data["series"][-1]["newTerms"], 3)
        self.assertEqual({k["label"] for k in data["kinds"]}, {"Words", "Names", "Corrections", "Phrases"})
        self.assertIn("zorbatron", [t["key"] for t in data["topWords"]])
        self.assertEqual(data["adminUrl"], "/admin/learning/termstat/")

    def test_everyone_else_is_refused(self):
        self.assertEqual(self.get_stats().status_code, 401)  # guest
        user = self.token("user@example.com", "Plain-pass-2026!")
        self.assertFalse(user["user"]["isAdmin"])
        r = self.get_stats(user["token"])
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "admin_only")

    def test_an_administrator_who_is_switched_off_loses_access(self):
        token = self.token("boss@example.com", "223000313")["token"]
        User.objects.filter(username="boss@example.com").update(is_staff=False)
        self.assertEqual(self.get_stats(token).status_code, 403)

    def test_the_numbers_contain_no_private_profile_data(self):
        LearningProfile.objects.create(client_id=uuid.uuid4(), data={"vocab": {"privateword": 9}})
        body = self.get_stats(self.token("boss@example.com", "223000313")["token"]).content
        self.assertNotIn(b"privateword", body)


class EngineTermsApiTests(EngineApiBase):
    """Browsing and moderating terms from the app (same administrator-only rules)."""

    def admin_token(self):
        return self.token("boss@example.com", "223000313")["token"]

    def terms(self, token, **params):
        from urllib.parse import urlencode

        return self.client.get("/api/learning/terms/?" + urlencode(params), HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_only_administrators_can_browse(self):
        self.assertEqual(self.client.get("/api/learning/terms/").status_code, 401)
        user = self.token("user@example.com", "Plain-pass-2026!")["token"]
        self.assertEqual(self.terms(user).status_code, 403)

    def test_browse_filter_search_and_page(self):
        token = self.admin_token()
        everything = self.terms(token).json()
        self.assertEqual(everything["total"], 3)
        self.assertEqual({t["kind"] for t in everything["items"]}, {"word", "name", "ngram"})
        self.assertTrue(all(t["published"] for t in everything["items"]))
        self.assertEqual(self.terms(token, kind="word").json()["total"], 1)
        self.assertEqual(self.terms(token, q="zorb").json()["items"][0]["key"], "zorbatron")
        self.assertEqual(self.terms(token, status="waiting").json()["total"], 0)
        self.assertEqual(self.terms(token, status="published").json()["total"], 3)
        page = self.terms(token, pageSize=2, page=2).json()
        self.assertEqual((page["pages"], page["page"], len(page["items"])), (2, 2, 1))
        self.assertEqual(self.terms(token, page="nonsense", pageSize="x").status_code, 200)

    def test_removing_a_term_unpublishes_it_at_once_and_it_stays_out(self):
        token = self.admin_token()
        self.assertIn("zorbatron", self.client.get("/api/learning/global/").json()["words"])
        term = TermStat.objects.get(key="zorbatron")
        r = self.client.put(f"/api/learning/terms/{term.pk}/", data='{"removed": true}', content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["removed"])
        self.assertNotIn("zorbatron", self.client.get("/api/learning/global/").json()["words"])
        # more people using it does not bring it back
        for _ in range(3):
            save_profile(uuid.uuid4(), {**profile(), "shared": True})
        cache.clear()
        self.assertNotIn("zorbatron", self.client.get("/api/learning/global/").json()["words"])
        # the counts and the lists leave it out, and it can be found under "removed"
        self.assertEqual(engine_summary()["published"], 2)
        self.assertEqual(self.terms(token, status="removed").json()["items"][0]["key"], "zorbatron")
        self.assertEqual(self.terms(token).json()["total"], 2)
        # restoring puts it back
        self.client.put(f"/api/learning/terms/{term.pk}/", data='{"removed": false}', content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        cache.clear()
        self.assertIn("zorbatron", self.client.get("/api/learning/global/").json()["words"])

    def test_bad_requests(self):
        token = self.admin_token()
        auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", "content_type": "application/json"}
        term = TermStat.objects.first()
        self.assertEqual(self.client.put(f"/api/learning/terms/{term.pk}/", data='{"removed": "yes"}', **auth).status_code, 400)
        self.assertEqual(self.client.put("/api/learning/terms/999999/", data='{"removed": true}', **auth).status_code, 404)
        self.assertEqual(self.client.put("/api/learning/terms/abc/", data='{"removed": true}', **auth).status_code, 404)
        user = self.token("user@example.com", "Plain-pass-2026!")["token"]
        self.assertEqual(
            self.client.put(f"/api/learning/terms/{term.pk}/", data='{"removed": true}', content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {user}").status_code,
            403,
        )
        self.assertFalse(TermStat.objects.get(pk=term.pk).removed)

    def test_stats_report_when_the_engine_last_learned_and_how_many_are_removed(self):
        token = self.admin_token()
        data = self.client.get("/api/learning/stats/", HTTP_AUTHORIZATION=f"Bearer {token}").json()
        self.assertIsNotNone(data["lastActivityAt"])
        self.assertEqual(data["removed"], 0)
        self.assertEqual({k["kind"] for k in data["kinds"]}, {"word", "name", "fix", "ngram"})
