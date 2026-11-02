"""OpenAPI description of the learning endpoints (the self-training model). Merged into the spec
by core.openapi.build_spec."""

from .openapi_extra import BOOL, INT, STR, op, path_param, query, ref, s_obj

COUNTS = {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1}}


def build_schemas():
    profile_fields = {
        "vocab": {**COUNTS, "description": "Lower-case word -> number of sentences it was used in."},
        "names": {**COUNTS, "description": "Lower-case word -> times it was capitalised mid-sentence (a name)."},
        "fixes": {"type": "object", "description": "Typo -> corrected word -> times the writer made that correction.",
                  "additionalProperties": COUNTS},
        "ignores": {"type": "array", "items": STR, "description": "Suggestions the writer chose to ignore (`rule:text`)."},
        "serial": {"type": "array", "items": INT, "minItems": 2, "maxItems": 2,
                   "description": "Lists written with / without a comma before the final \"and\"."},
        "ngrams": {**COUNTS, "description": "Three-word phrase -> count. Never full sentences."},
    }
    return {
        "LearningProfileInput": s_obj(
            {**profile_fields, "shared": {**BOOL, "description": "Opt in (true) or out (false) of contributing to the shared model. Omit to leave unchanged."}},
            description="Only derived counts are accepted: lower-case words, names, corrections, list style and three-word phrases. "
                        "Unknown fields and anything that is not a plain word are rejected. Counts merge into the stored profile by taking the larger value, "
                        "so several devices can upload without double counting.",
        ),
        "LearningProfile": s_obj(
            {**profile_fields, "shared": BOOL, "updatedAt": {"type": "string", "format": "date-time", "nullable": True}},
        ),
        "EngineStats": s_obj(
            {
                "threshold": {**INT, "description": "People needed before a term is published."},
                "optedIn": INT, "profiles": INT, "terms": INT, "published": INT, "removed": INT,
                "lastActivityAt": {"type": "string", "format": "date-time", "nullable": True},
                "newThisWeek": INT, "uploadsThisWeek": INT, "publishedToday": INT,
                "kinds": {"type": "array", "items": s_obj({"kind": STR, "label": STR, "total": INT, "published": INT, "pending": INT, "uses": INT})},
                "series": {"type": "array", "description": "The last 30 days.",
                           "items": s_obj({"day": {"type": "string", "format": "date"}, "newTerms": INT, "uploads": INT, "total": INT})},
                "topWords": {"type": "array", "items": ref("EngineTerm")},
                "almost": {"type": "array", "items": ref("EngineTerm")},
                "recent": {"type": "array", "items": ref("EngineTerm")},
                "adminUrl": STR,
            },
            description="How the shared engine is growing. Counts and terms only; nothing about any individual.",
        ),
        "EngineTerm": s_obj({"id": INT, "key": STR, "kind": {"type": "string", "enum": ["word", "name", "fix", "ngram"]},
                             "contributors": INT, "count": INT, "published": BOOL, "removed": BOOL,
                             "createdAt": {"type": "string", "format": "date-time"}}),
        "EngineTermPage": s_obj({"total": INT, "page": INT, "pages": INT, "pageSize": INT, "threshold": INT,
                                 "items": {"type": "array", "items": ref("EngineTerm")}}),
        "EngineTermRemoval": s_obj({"removed": BOOL}, required=["removed"]),
        "GlobalModel": s_obj(
            {
                "contributors": {**INT, "description": "People who opted in."},
                "minContributors": {**INT, "description": "An item is published only when at least this many different people contributed it."},
                "words": {"type": "array", "items": STR},
                "names": {"type": "array", "items": STR},
                "fixes": {"type": "object", "additionalProperties": STR, "description": "Typo -> correction."},
                "ngrams": {"type": "array", "items": STR, "description": "Three-word phrases for next-word suggestions."},
            },
        ),
    }


def build_paths():
    return {
        "/api/learning/profile/": {
            "get": op("Learning", "Get my learned model", "getLearningProfile",
                      "What GreenDoc has learned about your writing. Empty until the browser uploads it.",
                      ok="LearningProfile", errors=("400",)),
            "put": op("Learning", "Back up my learned model", "putLearningProfile",
                      "Uploads what the browser learned (derived counts only) and returns the merged profile, so a second device "
                      "gets the same model. Set `shared` to true to also feed the shared engine; it is false until you say so. Shared terms are pooled "
                      "anonymously and permanently, so the dictionary grows for everyone.",
                      body="LearningProfileInput", ok="LearningProfile", errors=("400", "413"),
                      body_example={"vocab": {"amalitech": 4}, "names": {"amalitech": 3}, "fixes": {"recieved": {"received": 2}},
                                    "serial": [3, 0], "ngrams": {"thank you for": 5}, "shared": True}),
            "delete": op("Learning", "Erase my learned model", "deleteLearningProfile",
                         "Deletes your stored private model. Terms you already shared are pooled anonymously (the engine keeps no record of who sent them), "
                         "so they cannot be taken back out.",
                         ok=None, ok_code="204", ok_description="Erased.", errors=("400",)),
        },
        "/api/learning/global/": {
            "get": op("Learning", "Get the shared model", "getGlobalModel",
                      "Words, names, corrections and phrases that several people who opted in agree on. Public; it contains no individual's data.",
                      secured=False, ok="GlobalModel"),
        },
    }


def _extra_paths():
    return {
        "/api/learning/stats/": {
            "get": op("Learning", "How the engine is being trained", "getEngineStats",
                      "Administrators only (a signed-in staff account). Totals, growth over the last 30 days, the most agreed-on "
                      "words and the newest terms. Everyone else gets 403 (or 401 when not signed in).",
                      ok="EngineStats", errors=("400",)),
        },
        "/api/learning/terms/": {
            "get": op("Learning", "Browse what the engine has learned", "listEngineTerms",
                      "Administrators only. Filter by kind and status, search, and page through every term.",
                      ok="EngineTermPage", errors=("400",),
                      params=[query("kind", "word, name, fix or ngram.", {"type": "string", "enum": ["word", "name", "fix", "ngram"]}),
                              query("status", "published, waiting or removed.", {"type": "string", "enum": ["published", "waiting", "removed"]}),
                              query("q", "Text to search for."),
                              query("page", "1-based page number.", {"type": "integer", "minimum": 1}),
                              query("pageSize", "Terms per page (up to 100).", {"type": "integer", "minimum": 1, "maximum": 100})]),
        },
        "/api/learning/terms/{term_id}/": {
            "parameters": [path_param("term_id", "Term id.")],
            "put": op("Learning", "Remove a term from the shared dictionary, or restore it", "setEngineTermRemoved",
                      "Administrators only. A removed term keeps counting but is never published, so it does not come back "
                      "when more people use it. Takes effect at once for everyone.",
                      body="EngineTermRemoval", ok="EngineTerm", errors=("400", "404"),
                      body_example={"removed": True}),
        },
    }


def extend(spec):
    spec["paths"].update(build_paths())
    spec["paths"].update(_extra_paths())
    spec["components"]["schemas"].update(build_schemas())
    spec["tags"].append({"name": "Learning", "description": "The self-training model: private per-writer profile and an opt-in shared model."})
    return spec
