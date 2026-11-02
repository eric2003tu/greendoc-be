# GreenDoc API

Django backend for the GreenDoc word processor frontend. Public and login-free:
each browser identifies itself with a random UUID in the `X-Client-Id` header, and
can only see its own documents. Shared documents are read through unguessable tokens.

## Run

    pip install -r requirements.txt
    python manage.py migrate
    python manage.py runserver 8000
    python manage.py test

## API docs

Swagger UI: http://localhost:8000/api/docs/ (raw spec: `/api/openapi.json`).
Click **Authorize** and paste any UUID to try the document endpoints.
The spec lives in `core/openapi.py`; a test fails if a route is added without documenting it.

## Endpoints (all under `/api/`)

Full request/response details live in Swagger. Identity is the `X-Client-Id` header unless marked public.

| Area | Endpoints |
|---|---|
| System | `GET health/`, `GET docs/`, `GET openapi.json` (public) |
| Documents | `GET/POST documents/` (`?q=` search, `templateId` to start from a template), `POST documents/import/` (docx/html/txt upload), `GET/PUT/DELETE documents/<id>/` (PUT upserts under the draft id), `POST documents/<id>/duplicate/`, `GET/DELETE recent/` |
| Layout & properties | stored on the document: `settings` (page size, margins, columns, header/footer, watermark, theme, track-changes flag...), `properties` (subject, keywords...), `protected`, `markedFinal` |
| Account (optional) | `POST auth/register/` (email, first name, last name, password), `POST auth/login/`, `POST auth/logout/`, `POST auth/logout-all/`, `GET/PUT/DELETE auth/me/`, `POST auth/refresh/`, `GET auth/sessions/`, `DELETE auth/sessions/<id>/`, `POST auth/change-password/`, `POST auth/forgot-password/`, `POST auth/reset-password/` |
| Sharing | `POST/DELETE documents/<id>/share/` (`permission`: `view` or `edit`), `GET/PUT shared/<token>/` (public; PUT only on edit links), `POST documents/<id>/invite/` and `POST documents/<id>/email-copy/` (sign-in required) |
| Support | `POST support/` (public) |
| Document tools | `GET documents/<id>/stats/`, `toc/`, `spellcheck/`, `readability/`, `export/?format=docx\|html\|txt\|rtf\|xml`, `POST find/` (find / replace all) |
| Comments | `GET/POST comments/`, `GET/PUT/DELETE comments/<cid>/`, `POST comments/<cid>/replies/`, `DELETE comments/<cid>/replies/<rid>/` |
| Track changes | `GET/POST revisions/`, `POST revisions/accept-all/`, `POST revisions/reject-all/`, `DELETE revisions/<rid>/` |
| Mail merge | `GET/PUT/DELETE mailmerge/` (csv or fields+records), `GET mailmerge/preview/?index=`, `GET mailmerge/finish/` |
| Version history | `GET/POST versions/`, `GET/DELETE versions/<id>/`, `POST versions/<id>/restore/` |
| References | `GET/POST sources/`, `GET/PUT/DELETE sources/<tag>/`, `POST citations/format/`, `GET bibliography/` |
| Preferences | `GET/PUT/DELETE preferences/` (dark mode, author name, default design, view, zoom...) |
| Catalog (public) | `GET catalog/templates/`, `catalog/templates/<id>/`, `catalog/design/` (themes + styles), `catalog/page-setup/`, `catalog/reference/` (fonts, sizes, shortcuts) |
| Proofing (public) | `POST proofing/spellcheck/`, `POST proofing/readability/`, `POST proofing/stats/`, `GET proofing/synonyms/?word=` |

Errors are `{"error": {"code", "message"}}`. Saved HTML is sanitised (5 MB max). While a document is
`protected`, content changes return 403 `document_protected` (comments are still allowed).

PDF is not produced here: the browser's Print dialog (Save as PDF) is the PDF path.

## Accounts: optional, like Word

Everything works as a guest (the browser's `X-Client-Id`). Signing in is a convenience:

- **Register** with email, first name, last name and password; you are signed in immediately.
- **Sign in** returns a token; send it as `Authorization: Bearer <token>` (see Sessions and tokens below).
- **Your documents follow you.** Documents, sources and settings are stored under the account, so any device
  that signs in sees them. If the browser also sends its guest `X-Client-Id` when registering or signing in,
  the guest's work is moved into the account (`"claimGuestData": false` skips it).
- A bad or expired token is a 401 (never silently treated as a guest) so the app can ask to sign in again.
- Friendly forms: errors carry `error.fields` (`{"email": "Enter a valid email address."}`), and password
  rules come back as a list of everything that is wrong at once. Wrong email and wrong password give the
  same message; 5 failed sign-ins lock that email for 15 minutes.
- Password reset by email, change password (signs out other devices), edit profile, delete account.
- Sharing by email, "Email a Copy" and invitations need an account (they send email); everything else does not.

## Sessions and tokens

- **Token:** random, returned once at sign in or registration, and stored on the server only as a SHA-256 hash.
  Send it as `Authorization: Bearer <token>`.
- **Lifetime:** a session lasts 30 days from its last use (the window slides forward while you keep using it),
  and at most 90 days in total. `expiresAt` in the sign-in response says when it ends if unused.
- **Rotation:** `POST auth/refresh/` swaps the token for a new one (the old one stops working immediately). The web
  app does this about once a week, so a token that leaked long ago dies on its own.
- **Devices:** `GET auth/sessions/` lists where you are signed in ("Chrome on Windows", last used, current device
  flagged); `DELETE auth/sessions/<id>/` signs one device out. At most 10 devices; the oldest is dropped.
- **Ending sessions:** sign out (this device), sign out everywhere, change password (other devices), reset password
  (all devices) and delete account all revoke tokens.
- **Errors:** an invalid or expired token is always `401 invalid_token`, never silently treated as a guest.

**Demo account.** `python manage.py seed_demo` creates (or resets) a shared sample login,
`demo@greendoc.app` / `GreenDoc-demo-2026` (override with `GREENDOC_DEMO_EMAIL` and `GREENDOC_DEMO_PASSWORD`).
The web app shows it on the sign-in form (during development). Because everyone can use it, it can't be deleted and its email and
password can't be changed (403 `demo_account`), and no reset email is sent for it.

Emails print to the server console in development. Set `GREENDOC_EMAIL_BACKEND` (and SMTP settings),
`GREENDOC_FROM_EMAIL`, `GREENDOC_SUPPORT_EMAIL` and `GREENDOC_FRONTEND_URL` for production.

## Layout

`core` (http helpers, CORS, rate limit, OpenAPI), `documents` (documents, comments, track changes,
mail merge, versions, import/export; `dom.py` is the HTML tree all features share),
`workspace` (preferences, sources), `accounts` (register, sign-in, sessions), `support` (contact form), `catalog` (reference data copied from the frontend),
`proofing` (spell/thesaurus/readability).

## Configuration (environment variables)

`GREENDOC_SECRET_KEY`, `GREENDOC_DEBUG` (`0` in production), `GREENDOC_ALLOWED_HOSTS`,
`GREENDOC_CORS_ORIGINS` (comma-separated, default `*`), `GREENDOC_RATE_LIMIT` (per IP per minute, default 240).

## ML

`proofing/services.py` has no Django imports; replace or extend its functions with
spaCy / transformer models later without touching the views. The spellcheck and thesaurus
currently use the same small word lists as the frontend (`proofing/data/`).

## Learning engine

The editor trains itself on what people write, and this server pools the result into a shared dictionary.

| Endpoint | Who | What |
|---|---|---|
| `GET/PUT/DELETE /api/learning/profile/` | the person (guest or signed in) | Back up and restore their own model. Only derived counts are accepted: words, names, corrections, list style and three-word phrases. `PUT` with `shared: true` also feeds the engine; `shared: false` stops it. |
| `GET /api/learning/global/` | public | The shared dictionary: terms that at least `LEARNING_MIN_CONTRIBUTORS` (default 3) different people have used. |
| `GET /api/learning/stats/` | administrators | Totals, growth over 30 days, newest and most agreed-on terms. |
| `GET /api/learning/terms/` | administrators | Browse, filter, search and page through every term. |
| `PUT /api/learning/terms/<id>/` | administrators | `{"removed": true}` takes a term out of the dictionary for good (it keeps counting but is never published); `false` restores it. |

- **Anonymous:** the shared engine (`TermStat`) stores terms and totals only. It has no record of who sent them, so a contribution cannot be withdrawn once pooled.
- **Admin site:** `/admin/learning/termstat/` has a dashboard (growth chart, totals, what is about to be published). Only staff can open it.
- **Making an administrator:** `python manage.py seed_admin --email you@example.com --first-name Ada --last-name Lovelace --password ...` (or set `GREENDOC_ADMIN_PASSWORD`). Safe to run again.
- **Export:** `python manage.py export_dictionary [--names]` prints the shared words, one per line.
- **CORS:** browsers send the session token in the `Authorization` header, which the server now allows.
- Login responses include `isAdmin` so the app can show the Engine page to administrators. The server checks the role on every admin request.
