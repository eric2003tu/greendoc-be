"""Numbers for the admin dashboard and the app's Engine page: how the shared engine is growing.

Only terms and totals appear here. Nothing in this module reads a person's private profile
except to count how many people opted in. Terms an administrator removed are left out of every
count (they are listed only when asked for).
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from .models import EngineDay, LearningProfile, TermStat

DAYS = 30
KIND_LABELS = dict(TermStat.KINDS)
PAGE_SIZE = 25


def live():
    """Terms that have not been removed by an administrator."""
    return TermStat.objects.filter(removed=False)


def engine_summary():
    k = settings.LEARNING_MIN_CONTRIBUTORS
    today = timezone.localdate()

    kinds = []
    totals = live().values("kind").annotate(
        total=Count("id"),
        published=Count("id", filter=Q(contributors__gte=k)),
        uses=Sum("count"),
    )
    by_kind = {row["kind"]: row for row in totals}
    for kind, label in TermStat.KINDS:
        row = by_kind.get(kind, {})
        total = row.get("total", 0)
        published = row.get("published", 0)
        kinds.append(
            {
                "kind": kind,
                "label": label + ("s" if not label.endswith("s") else ""),
                "total": total,
                "published": published,
                "pending": total - published,
                "uses": row.get("uses") or 0,
            }
        )

    # Growth over the last DAYS days: new terms per day and the running total of all terms.
    start = today - timedelta(days=DAYS - 1)
    activity = {d.day: d for d in EngineDay.objects.filter(day__gte=start)}
    before = live().filter(created_at__date__lt=start).count()
    series, running = [], before
    for offset in range(DAYS):
        day = start + timedelta(days=offset)
        row = activity.get(day)
        new_terms = row.new_terms if row else 0
        running += new_terms
        series.append(
            {"day": day, "new_terms": new_terms, "uploads": row.uploads if row else 0, "total": running}
        )
    peak = max((d["new_terms"] for d in series), default=0) or 1
    for d in series:
        d["bar"] = round(100 * d["new_terms"] / peak)

    published_words = live().filter(kind=TermStat.WORD, contributors__gte=k)
    return {
        "threshold": k,
        "opted_in": LearningProfile.objects.filter(shared=True).count(),
        "profiles": LearningProfile.objects.count(),
        "terms": live().count(),
        "published": live().filter(contributors__gte=k).count(),
        "removed": TermStat.objects.filter(removed=True).count(),
        "kinds": kinds,
        "series": series,
        "new_this_week": sum(d["new_terms"] for d in series[-7:]),
        "uploads_this_week": sum(d["uploads"] for d in series[-7:]),
        "top_words": list(published_words.order_by("-contributors", "-count", "key")[:15]),
        "almost": list(
            live().filter(kind=TermStat.WORD, contributors=k - 1).order_by("-count", "key")[:10]
        )
        if k > 1
        else [],
        "recent": list(live().order_by("-created_at", "-id")[:15]),
        "published_today": published_words.filter(updated_at__date=today).count(),
        "last_activity": live().aggregate(last=Max("updated_at"))["last"],
    }


def _term(t):
    k = settings.LEARNING_MIN_CONTRIBUTORS
    return {
        "id": t.pk,
        "key": t.key,
        "kind": t.kind,
        "contributors": t.contributors,
        "count": t.count,
        "createdAt": t.created_at.isoformat(),
        "published": t.contributors >= k and not t.removed,
        "removed": t.removed,
    }


def engine_summary_json():
    """The same numbers as the admin dashboard, shaped for the app's Engine page."""
    s = engine_summary()
    return {
        "threshold": s["threshold"],
        "optedIn": s["opted_in"],
        "profiles": s["profiles"],
        "terms": s["terms"],
        "published": s["published"],
        "removed": s["removed"],
        "newThisWeek": s["new_this_week"],
        "uploadsThisWeek": s["uploads_this_week"],
        "publishedToday": s["published_today"],
        "lastActivityAt": s["last_activity"].isoformat() if s["last_activity"] else None,
        "kinds": [
            {"kind": k["kind"], "label": k["label"], "total": k["total"], "published": k["published"],
             "pending": k["pending"], "uses": k["uses"]}
            for k in s["kinds"]
        ],
        "series": [
            {"day": d["day"].isoformat(), "newTerms": d["new_terms"], "uploads": d["uploads"], "total": d["total"]}
            for d in s["series"]
        ],
        "topWords": [_term(t) for t in s["top_words"]],
        "almost": [_term(t) for t in s["almost"]],
        "recent": [_term(t) for t in s["recent"]],
        "adminUrl": "/admin/learning/termstat/",
    }


def browse_terms(kind="", status="", query="", page=1, page_size=PAGE_SIZE):
    """A page of terms for the Engine page: filter by kind and status, search, newest-agreed first."""
    k = settings.LEARNING_MIN_CONTRIBUTORS
    rows = TermStat.objects.all()
    if kind in KIND_LABELS:
        rows = rows.filter(kind=kind)
    if status == "removed":
        rows = rows.filter(removed=True)
    else:
        rows = rows.filter(removed=False)
        if status == "published":
            rows = rows.filter(contributors__gte=k)
        elif status == "waiting":
            rows = rows.filter(contributors__lt=k)
    if query:
        rows = rows.filter(key__icontains=query[:60])
    rows = rows.order_by("-contributors", "-count", "key")
    page_size = max(1, min(100, page_size))
    total = rows.count()
    pages = max(1, -(-total // page_size))
    page = max(1, min(page, pages))
    items = rows[(page - 1) * page_size : page * page_size]
    return {
        "total": total,
        "page": page,
        "pages": pages,
        "pageSize": page_size,
        "threshold": k,
        "items": [_term(t) for t in items],
    }
