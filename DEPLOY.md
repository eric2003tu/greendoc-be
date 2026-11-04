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
- Desktop apps: put the address in `greenleaf-word-studio/.env.desktop` (`VITE_API_URL=...`).
- Website: set `VITE_API_URL` to the same address when it is built.

## 5. Publish desktop apps online (GitHub Actions)

The API cannot build desktop packages itself. When an administrator presses **Publish update**, the API
dispatches a GitHub Actions workflow. Hosted Windows and macOS runners build and publish Windows, Apple Silicon,
and Intel Mac packages. Your computer does not need to be on.

One-time setup:

1. Push `greenleaf-word-studio` to `eric2003tu/greenleaf-word-studio` on the `main` branch, including
   `.github/workflows/desktop-release.yml`.
2. In that GitHub repository, open **Settings > Secrets and variables > Actions**. Add these repository secrets:
   - `TAURI_SIGNING_PRIVATE_KEY`: the contents of your existing Tauri private signing key file. Keep it out of git.
   - `GREENDOC_PUBLISH_KEY`: the same shared publish key configured for the API in Render.
   - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`: only if the signing key has a password.
3. In **Actions > Variables**, add the repository variable `GREENDOC_SERVER` with the API's public base URL
   (for example, `https://greendoc-api.onrender.com`; no trailing slash).
4. In the Render service's **Environment** settings, set `GREENDOC_GITHUB_TOKEN` to the fine-grained token for
   this repository with **Actions: Read and write** permission. Also set `GREENDOC_PUBLISH_KEY` to the same shared
   key used in the GitHub secret. Set `GREENDOC_GITHUB_REPO` to `eric2003tu/greenleaf-word-studio`,
   `GREENDOC_GITHUB_REF` to `main`, and `GREENDOC_GITHUB_WORKFLOW` to `desktop-release.yml` (the Render blueprint
   supplies these values except the two secrets).
5. Deploy the API changes and apply the new desktop-release migration, then save the Render environment changes
   and wait for the service to redeploy. An administrator can then press **Publish update** on `/engine`; the API
   starts the hosted multi-platform build immediately.

The Mac DMGs are not signed or notarized by Apple, so macOS asks the user to approve the app on its first
launch. No Apple Developer secrets are needed for these builds.

The workflow can also be started manually from the repository's **Actions** tab. See
`greenleaf-word-studio/DESKTOP.md` for details. The local release agent remains an optional alternative.

## Notes
- Run **one** worker (the start script does): rate limits and sign-in lockouts are counted in memory.
- `GREENDOC_TRUST_PROXY=1` makes those limits count each visitor, not Render's proxy.
- Keep the signing key `~/.tauri/greendoc.key` on your computer only, with a backup.
