# Deploying the GreenDoc API on Render (free)

Render's free plan wipes its disk on every restart and puts idle services to sleep. So the API keeps nothing on
its own disk: the database is a free Postgres (Neon) and the Windows installers live in a free bucket
(Cloudflare R2).

## 1. Database: Neon
1. Create a free project at neon.tech and copy its **connection string** (it starts with `postgresql://`).
2. This is `DATABASE_URL` below.

## 2. Installer storage: Cloudflare R2
1. In the Cloudflare dashboard: **R2 > Create bucket** (for example `greendoc-installers`). Keep it private.
2. **R2 > Manage API tokens > Create token** with *Object Read & Write* on that bucket. Copy the **Access Key ID**
   and **Secret Access Key**, and the account's S3 address `https://<account-id>.r2.cloudflarestorage.com`.

## 3. The API: Render
1. Push this repository to GitHub. In Render choose **New > Blueprint** and select it; `render.yaml` sets it up.
2. Fill in the secret values when asked:

| Variable | Value |
|---|---|
| `DATABASE_URL` | the Neon connection string |
| `GREENDOC_S3_BUCKET` | the bucket name |
| `GREENDOC_S3_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` |
| `GREENDOC_S3_KEY_ID`, `GREENDOC_S3_SECRET` | the R2 token |
| `GREENDOC_ADMIN_EMAIL`, `GREENDOC_ADMIN_PASSWORD` | the administrator to create (set the password here, not in a file) |
| `GREENDOC_FRONTEND_URL` | the website's address |

3. Deploy. On every start the API migrates the database and makes sure that administrator exists.
   Your API address is `https://greendoc-api.onrender.com` (or the name Render gives it).

## 4. Point the apps at it
- Windows app: put the address in `greenleaf-word-studio/.env.desktop` (`VITE_API_URL=...`).
- Website: set `VITE_API_URL` to the same address when it is built.

## 5. Publish the Windows app
The server cannot build the installer (no Rust, no signing key), so build on your computer and upload it:

```
$env:GREENDOC_SERVER = "https://greendoc-api.onrender.com"
$env:GREENDOC_ADMIN_EMAIL = "you@example.com"
$env:GREENDOC_ADMIN_PASSWORD = "..."
npm run release
```

Apps then update when someone presses the refresh button. The first request after the service has slept can take
about a minute.

## Notes
- Run **one** worker (the start script does): rate limits and sign-in lockouts are counted in memory.
- `GREENDOC_TRUST_PROXY=1` makes those limits count each visitor, not Render's proxy.
- Keep the signing key `~/.tauri/greendoc.key` on your computer only, with a backup.
