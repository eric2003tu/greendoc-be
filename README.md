# GreenDoc API

Django backend for the GreenDoc word processor frontend. Public and login-free:
each browser identifies itself with a random UUID in the `X-Client-Id` header, and
can only see its own documents. Shared documents are read through unguessable tokens.

## Run

    pip install -r requirements.txt
    python manage.py migrate
    python manage.py runserver 8000
    python manage.py test

## Endpoints (all under `/api/`)

| Method | Path | Purpose |
|---|---|---|
| GET | `health/` | Liveness check |
| GET / POST | `documents/` | List own documents (no html) / create |
| GET / PUT / DELETE | `documents/<uuid>/` | Read / upsert (use the frontend draft id) / delete |
| POST / DELETE | `documents/<uuid>/share/` | Create / revoke the public share link |
| GET | `shared/<token>/` | Public read-only document (no header needed) |
| POST | `proofing/spellcheck/` | `{text}` -> `{issues:[{word,index,suggestions}]}` |
| POST | `proofing/readability/` | `{text}` -> Flesch scores, reading time |
| GET | `proofing/synonyms/?word=` | Thesaurus lookup |

Errors are `{"error": {"code", "message"}}`. Document HTML is sanitised on save (5 MB max).

## Configuration (environment variables)

`GREENDOC_SECRET_KEY`, `GREENDOC_DEBUG` (`0` in production), `GREENDOC_ALLOWED_HOSTS`,
`GREENDOC_CORS_ORIGINS` (comma-separated, default `*`), `GREENDOC_RATE_LIMIT` (per IP per minute, default 240).

## ML

`proofing/services.py` has no Django imports; replace or extend its functions with
spaCy / transformer models later without touching the views. The spellcheck and thesaurus
currently use the same small word lists as the frontend (`proofing/data/`).
