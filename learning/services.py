"""The learning model: validating what a browser sends, merging devices, and building the shared model.

Privacy rules, enforced here and not in the browser:
  * only derived counts are accepted (lower-case words, names, corrections, list style and
    three-word phrases); anything that looks like free text, digits or an address is rejected;
  * a private profile is never read by anyone but its owner;
  * only profiles that opted in (`shared=True`) feed the shared engine, which stores terms and
    totals with no record of who contributed them; a term is published only when at least
    LEARNING_MIN_CONTRIBUTORS different people established it.
"""

import re
from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from core.http import ApiError

from .models import EngineDay, LearningProfile, TermStat

WORD = re.compile(r"^[a-z][a-z'-]{0,24}$")
IGNORE = re.compile(r"^[a-z-]{1,30}:.{1,60}$")
MAX_COUNT = 1_000_000
LIMITS = {"vocab": 6000, "names": 3000, "fixes": 3000, "ignores": 2000, "ngrams": 4000}
FIELDS = {"vocab", "names", "fixes", "ignores", "serial", "ngrams"}
CACHE_KEY = "learning:global:v1"
CACHE_SECONDS = 300
PUBLISH_LIMITS = {"words": 5000, "names": 2000, "fixes": 1500, "ngrams": 3000}


def _fail(field, why):
    raise ApiError(f"{field}: {why}", code="invalid_field")


def _count(field, value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_COUNT:
        _fail(field, f"counts must be whole numbers from 1 to {MAX_COUNT}.")
    return value


def _counts(field, value, key_ok, limit):
    if not isinstance(value, dict):
        _fail(field, "must be an object of counts.")
    if len(value) > limit:
        _fail(field, f"too many entries ({limit} maximum).")
    out = {}
    for key, count in value.items():
        if not isinstance(key, str) or not key_ok(key):
            _fail(field, f"unsupported entry {str(key)[:30]!r}.")
        out[key] = _count(field, count)
    return out


def _ngram_ok(key):
    parts = key.split(" ")
    return len(parts) == 3 and all(WORD.match(p) for p in parts)


def clean_profile(data):
    """Validate a profile sent by the browser and return only known, well-formed fields."""
    if not isinstance(data, dict):
        _fail("profile", "must be an object.")
    unknown = set(data) - FIELDS - {"shared"}
    if unknown:
        _fail(sorted(unknown)[0], "is not a known field.")
    out = {}
    if "vocab" in data:
        out["vocab"] = _counts("vocab", data["vocab"], WORD.match, LIMITS["vocab"])
    if "names" in data:
        out["names"] = _counts("names", data["names"], WORD.match, LIMITS["names"])
    if "ngrams" in data:
        out["ngrams"] = _counts("ngrams", data["ngrams"], _ngram_ok, LIMITS["ngrams"])
    if "fixes" in data:
        fixes = data["fixes"]
        if not isinstance(fixes, dict) or len(fixes) > LIMITS["fixes"]:
            _fail("fixes", f"must be an object of at most {LIMITS['fixes']} entries.")
        out["fixes"] = {}
        for wrong, rights in fixes.items():
            if not isinstance(wrong, str) or not WORD.match(wrong):
                _fail("fixes", f"unsupported entry {str(wrong)[:30]!r}.")
            rights = _counts("fixes", rights, lambda r: bool(WORD.match(r.lower())), 6)
            out["fixes"][wrong] = {r.lower(): c for r, c in rights.items()}
    if "ignores" in data:
        ignores = data["ignores"]
        if not isinstance(ignores, list) or len(ignores) > LIMITS["ignores"]:
            _fail("ignores", f"must be a list of at most {LIMITS['ignores']} entries.")
        if not all(isinstance(i, str) and IGNORE.match(i) for i in ignores):
            _fail("ignores", "entries must look like rule:text.")
        out["ignores"] = list(dict.fromkeys(ignores))
    if "serial" in data:
        s = data["serial"]
        ok = isinstance(s, list) and len(s) == 2 and all(
            isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= MAX_COUNT for n in s
        )
        if not ok:
            _fail("serial", "must be two whole numbers.")
        out["serial"] = s
    return out


def _merge_counts(old, new, limit):
    """Two devices count different sentences, so the larger count is the safe merge."""
    merged = dict(old)
    for key, count in new.items():
        merged[key] = max(merged.get(key, 0), count)
    if len(merged) > limit:
        merged = dict(sorted(merged.items(), key=lambda kv: -kv[1])[:limit])
    return merged


def merge_profiles(old, new):
    out = {
        "vocab": _merge_counts(old.get("vocab", {}), new.get("vocab", {}), LIMITS["vocab"]),
        "names": _merge_counts(old.get("names", {}), new.get("names", {}), LIMITS["names"]),
        "ngrams": _merge_counts(old.get("ngrams", {}), new.get("ngrams", {}), LIMITS["ngrams"]),
    }
    fixes = {w: dict(r) for w, r in old.get("fixes", {}).items()}
    for wrong, rights in new.get("fixes", {}).items():
        fixes[wrong] = _merge_counts(fixes.get(wrong, {}), rights, 6)
    out["fixes"] = dict(list(fixes.items())[: LIMITS["fixes"]])
    out["ignores"] = list(dict.fromkeys([*old.get("ignores", []), *new.get("ignores", [])]))[: LIMITS["ignores"]]
    a, b = old.get("serial", [0, 0]), new.get("serial", [0, 0])
    out["serial"] = [max(a[0], b[0]), max(a[1], b[1])]
    return out


def public_profile(row):
    data = row.data if row else {}
    return {
        "shared": bool(row and row.shared),
        "vocab": data.get("vocab", {}),
        "names": data.get("names", {}),
        "fixes": data.get("fixes", {}),
        "ignores": data.get("ignores", []),
        "serial": data.get("serial", [0, 0]),
        "ngrams": data.get("ngrams", {}),
        "updatedAt": row.updated_at.isoformat() if row else None,
    }


ESTABLISHED = {TermStat.WORD: 2, TermStat.NAME: 2, TermStat.FIX: 2, TermStat.NGRAM: 1}


def _terms(data):
    """Flatten a profile into {(kind, key): count} for the terms worth sharing."""
    out = {}
    for w, n in (data.get("vocab") or {}).items():
        out[(TermStat.WORD, w)] = n
    for w, n in (data.get("names") or {}).items():
        out[(TermStat.NAME, w)] = n
    for g, n in (data.get("ngrams") or {}).items():
        out[(TermStat.NGRAM, g)] = n
    for wrong, rights in (data.get("fixes") or {}).items():
        for right, n in rights.items():
            out[(TermStat.FIX, f"{wrong}>{right}")] = n
    return out


def feed_engine(before, after):
    """Add what is new in `after` (compared with `before`) to the shared engine.

    The engine never learns who a term came from. A person counts once towards a term's
    `contributors`: the first time their own usage reaches the "established" level.
    """
    old, new = _terms(before), _terms(after)
    grown = {k: n - old.get(k, 0) for k, n in new.items() if n > old.get(k, 0)}
    if not grown:
        return 0
    rows = {}
    for kind in {k for k, _ in grown}:
        keys = [key for k, key in grown if k == kind]
        for row in TermStat.objects.filter(kind=kind, key__in=keys):
            rows[(row.kind, row.key)] = row
    create, update = [], []
    for term, delta in grown.items():
        kind = term[0]
        newly = old.get(term, 0) < ESTABLISHED[kind] <= new[term]
        row = rows.get(term)
        if row is None:
            create.append(TermStat(kind=kind, key=term[1], count=delta, contributors=1 if newly else 0))
        else:
            row.count += delta
            row.contributors += 1 if newly else 0
            update.append(row)
    TermStat.objects.bulk_create(create, ignore_conflicts=True)
    if update:
        TermStat.objects.bulk_update(update, ["count", "contributors"])
    today, _ = EngineDay.objects.get_or_create(day=timezone.localdate())
    EngineDay.objects.filter(pk=today.pk).update(
        uploads=F("uploads") + 1,
        new_terms=F("new_terms") + len(create),
        uses_added=F("uses_added") + sum(grown.values()),
    )
    return len(grown)


def save_profile(owner, body):
    """Create or update an owner's profile from a browser upload. Returns the stored profile."""
    shared = body.get("shared")
    if shared is not None and not isinstance(shared, bool):
        _fail("shared", "must be true or false.")
    incoming = clean_profile(body)
    row = LearningProfile.objects.filter(client_id=owner).first() or LearningProfile(client_id=owner)
    row.data = merge_profiles(row.data or {}, incoming)
    if shared is not None:
        row.shared = shared
    if row.shared:
        feed_engine(row.contributed or {}, row.data)
        row.contributed = row.data
    row.save()
    cache.delete(CACHE_KEY)
    return row


def delete_profile(owner):
    LearningProfile.objects.filter(client_id=owner).delete()
    cache.delete(CACHE_KEY)


def claim_guest_profile(guest_id, owner_id):
    """Signing in moves the browser's learned profile into the account."""
    guest = LearningProfile.objects.filter(client_id=guest_id).first()
    if not guest:
        return
    mine = LearningProfile.objects.filter(client_id=owner_id).first()
    if mine is None:
        guest.client_id = owner_id
        guest.save(update_fields=["client_id"])
    else:
        mine.data = merge_profiles(mine.data or {}, guest.data or {})
        mine.shared = mine.shared or guest.shared
        mine.save()
        guest.delete()
    cache.delete(CACHE_KEY)


def build_global():
    """The shared dictionary: terms that enough different people have established."""
    k = settings.LEARNING_MIN_CONTRIBUTORS

    def top(kind, limit):
        rows = TermStat.objects.filter(kind=kind, removed=False, contributors__gte=k).order_by("-contributors", "-count", "key")
        return [r.key for r in rows[:limit]]

    fixes = {}
    for key in top(TermStat.FIX, PUBLISH_LIMITS["fixes"]):
        wrong, _, right = key.partition(">")
        fixes.setdefault(wrong, right)  # the most agreed-on correction comes first
    return {
        "contributors": LearningProfile.objects.filter(shared=True).count(),
        "minContributors": k,
        "words": top(TermStat.WORD, PUBLISH_LIMITS["words"]),
        "names": top(TermStat.NAME, PUBLISH_LIMITS["names"]),
        "fixes": fixes,
        "ngrams": top(TermStat.NGRAM, PUBLISH_LIMITS["ngrams"]),
    }


def global_model():
    data = cache.get(CACHE_KEY)
    if data is None:
        data = build_global()
        cache.set(CACHE_KEY, data, CACHE_SECONDS)
    return data
