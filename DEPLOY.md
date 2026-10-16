# Deploying Bansho (番所) — step by step

Bansho is two websites built from **one GitHub repository**:

| Folder | What it is | Who can see it |
|---|---|---|
| `site/` | The public landing pages for Harahachi and Bus Board | Everyone |
| `console/` | Your private ops console (CRM + analytics), with its own database | Only you, with your passkey |

Each folder becomes its **own Vercel project**. Only the console gets a database.
You will also put one secret on Harahachi and one on Bus Board, so the console can
read their numbers.

You do not need a terminal except for making secrets (one Python command).
Everything else is clicking in web dashboards.

```
                ┌──────────── GitHub repo "bansho" ────────────┐
                │   site/                    console/           │
                └─────┬──────────────────────────┬─────────────┘
       Vercel project │ "bansho-site"            │ Vercel project "bansho-console"
                      ▼                          ▼
   https://bansho-site.vercel.app      https://bansho-console.vercel.app
   (static pages, no cookies)  ──beacon──▶ /api/ingest
                                          /api/*  (Python function) ──▶ Neon Postgres
                                                    │ Bearer HARAHACHI_OPS_TOKEN
                                                    ├──────────▶ Harahachi  /api/ops/v1/*
                                                    │ Bearer BUSBOARD_OPS_TOKEN
                                                    └──────────▶ Bus Board  /v1/ops/*
   cron-job.org (every 15 min) ──Bearer CRON_SECRET──▶ /api/cron/collect
```

---

## 0. What you need

| Thing | Where | Cost |
|---|---|---|
| This folder as a **GitHub repository** (private is fine) | github.com | free |
| A **Vercel** account (sign up with GitHub) | vercel.com | Hobby, free |
| A computer with **Python 3** (only to generate secrets) | — | free |
| Access to **Harahachi's** Vercel project and **Bus Board's** Render service | — | — |
| *(optional)* a **cron-job.org** account | cron-job.org | free |
| A device that supports **passkeys** (any recent phone, or a laptop with Windows Hello / Touch ID / a security key) | — | — |

Put `bansho/` on GitHub the way you normally do (GitHub Desktop or the website's
"upload files"). The repository root must contain `site/`, `console/`, `.github/`
and this `DEPLOY.md`.

---

## 1. Make your secrets

Run these on your computer. Each command prints one long random string. Copy each
into a password manager **with its name** as you go — you will paste them in the
next steps.

```bash
# Setup token (one-time; at least 32 characters)
python -c "import secrets; print(secrets.token_urlsafe(32))"     # -> BANSHO_SETUP_TOKEN

# Cron secret (at least 32 characters)
python -c "import secrets; print(secrets.token_urlsafe(32))"     # -> CRON_SECRET

# Ops tokens (at least 48 characters; this makes 64)
python -c "import secrets; print(secrets.token_urlsafe(48))"     # -> HARAHACHI_OPS_TOKEN  (= SEKISHO_OPS_TOKEN on Harahachi)
python -c "import secrets; print(secrets.token_urlsafe(48))"     # -> BUSBOARD_OPS_TOKEN   (= MAPS_OPS_TOKEN on Bus Board)
```

Use a **different** string for each. Never send them in chat or email, never put
them in the repository.

---

## 2. Create the two Vercel projects (same repository, two root folders)

### 2a. The console

1. vercel.com → **Add New… → Project** → pick your `bansho` repository → **Import**.
2. On *Configure Project*:
   * **Project Name:** `bansho-console` (this becomes `https://bansho-console.vercel.app`;
     if the name is taken Vercel gives you another — use whatever you get from here on).
   * **Framework Preset:** `Other`.
   * **Root Directory:** click **Edit** and choose **`console`**.
   * Leave Build / Output settings alone — `console/vercel.json` sets them.
   * Skip Environment Variables for now.
3. **Deploy.** It builds; the API will not work yet (no database, no settings). That is expected.
4. Note the production address shown on the project page, e.g.
   `https://bansho-console.vercel.app`. This guide calls it **CONSOLE**.

### 2b. The landing site

1. **Add New… → Project** again → the **same** repository → **Import**.
2. **Project Name:** `bansho-site`. **Framework Preset:** `Other`.
   **Root Directory:** **`site`**.
3. **Deploy**, then note its address, e.g. `https://bansho-site.vercel.app`. This guide calls it **SITE**.
   (A custom domain works too — add it under *Settings → Domains* and use it as SITE.)

> Both projects now redeploy automatically on every push. That is fine. If you want a push
> that only touches `site/` not to rebuild the console, set *Settings → Git → Ignored Build Step*
> to `git diff --quiet HEAD^ HEAD -- .` in each project.

---

## 3. Database for the console (Neon, via the Vercel Marketplace)

Only the **console** gets a database.

1. Open the **bansho-console** project → **Storage** tab → **Create Database** →
   choose **Neon (Serverless Postgres)** → accept the defaults (free plan, a region near you,
   e.g. Singapore) → **Create**.
2. When asked which projects to connect, choose **bansho-console only** (all environments is fine).
3. This adds `DATABASE_URL` (and a few other `PG…`/`POSTGRES_…` variables) to the console's
   environment automatically. You don't copy anything. The console creates its tables itself on first start.

> Do **not** connect this database to `bansho-site`. The site never talks to a database.

---

## 4. Console environment variables

**bansho-console → Settings → Environment Variables.** Add each one for the
**Production** environment, then redeploy (step 4.1).

| Name | Value | Notes |
|---|---|---|
| `BANSHO_RP_ID` | `bansho-console.vercel.app` | **Exactly** the console's host name: no `https://`, no slash. |
| `BANSHO_ORIGIN` | `https://bansho-console.vercel.app` | **Exactly** `https://` + the same host. No trailing slash. |
| `BANSHO_SETUP_TOKEN` | *(from step 1)* | One-time. You will delete it after setup. |
| `CRON_SECRET` | *(from step 1)* | Vercel's daily cron sends it automatically. |
| `BANSHO_SITE_ORIGIN` | `https://bansho-site.vercel.app` | Your SITE address. The only origin allowed to send page-view beacons. |
| `HARAHACHI_URL` | e.g. `https://harahachi.vercel.app` | Harahachi's address (no trailing slash). |
| `HARAHACHI_OPS_TOKEN` | *(from step 1)* | Must equal `SEKISHO_OPS_TOKEN` on Harahachi (step 6). |
| `BUSBOARD_URL` | e.g. `https://busboard.onrender.com` | Bus Board's Render address. |
| `BUSBOARD_OPS_TOKEN` | *(from step 1)* | Must equal `MAPS_OPS_TOKEN` on Bus Board (step 7). |
| `BANSHO_TZ` | `Asia/Kolkata` | Optional. Where "today" starts and ends. |
| `DATABASE_URL` | *(already there from step 3)* | Don't edit. |

**Never** add `BANSHO_DEV_SEED` to Vercel. (It would be ignored there anyway — dev mode refuses to
run on Vercel — but it does not belong in production.)

**Why RP ID and origin matter:** passkeys are bound to the exact domain in `BANSHO_RP_ID`.
The console refuses to start a sign-in if `BANSHO_ORIGIN` is not exactly `https://` + that host.
Preview deployments (`bansho-console-git-…vercel.app`) have different addresses, so passkeys only
work on the production address — that is intentional. If you later move the console to a custom
domain, change both variables to the new domain, sign in once with a **recovery code** (they are
not tied to a domain), and register a new passkey there.

### 4.1 Redeploy the console

**Deployments** tab → the latest deployment → **⋯ → Redeploy**. Environment variables only take
effect on a new deployment.

Quick check: open `CONSOLE/api/health` → it should show `{"ok":true}`.

---

## 5. Site environment variables

**bansho-site → Settings → Environment Variables** (Production). These are read **at build time**.

| Name | Example | If you leave it empty |
|---|---|---|
| `VITE_HARAHACHI_APP_URL` | `https://harahachi.vercel.app` | The "Open Harahachi" buttons are not shown |
| `VITE_HARAHACHI_APK_URL` | a link to the APK file | The APK button is hidden |
| `VITE_BUSBOARD_APP_URL` | `https://busboard.onrender.com` | The "open the board" buttons are not shown |
| `VITE_BUSBOARD_APK_URL` | a link to the APK file | The APK button is hidden |
| `VITE_INGEST_URL` | `https://bansho-console.vercel.app/api/ingest` | No analytics at all |

Only `https://` links are accepted; anything else hides the button. Then **redeploy the site**
(Deployments → ⋯ → Redeploy).

Check: open SITE, then the console's **Landing** screen later — your visit appears (unless your
browser sends Do Not Track / Global Privacy Control, in which case nothing is sent, by design).

---

## 6. Harahachi: give it the same ops token

In **Harahachi's** Vercel project → Settings → Environment Variables → add

| Name | Value |
|---|---|
| `SEKISHO_OPS_TOKEN` | the **same** string you used for `HARAHACHI_OPS_TOKEN` |

Redeploy Harahachi. Until the two are identical (and ≥ 48 characters), Harahachi answers the
console with 404 and the console shows **"Token rejected"**.

## 7. Bus Board: give it the same ops token

On **render.com** → your Bus Board service → **Environment** → **Add Environment Variable**:

| Key | Value |
|---|---|
| `MAPS_OPS_TOKEN` | the **same** string you used for `BUSBOARD_OPS_TOKEN` |

**Save Changes** (Render redeploys by itself).

---

## 8. First sign-in: create your passkey

1. On the device you want to use (phone or laptop), open **`CONSOLE/setup`**.
2. Paste `BANSHO_SETUP_TOKEN`, give the passkey a name ("Pixel", "MacBook"…), press
   **Create passkey**, and approve the prompt (fingerprint / face / PIN / security key).
3. **Save your 10 recovery codes now.** They are shown **once**. Put them in your password
   manager *and* on paper somewhere safe. Each code works one time and lets you register a new
   passkey if you lose your device — nothing else.
4. Tick "I have saved these codes" → **Continue**. You are in.
5. Back in Vercel, **delete `BANSHO_SETUP_TOKEN`** from the console's environment variables and
   redeploy. (The setup page already answers "not found" forever now that a passkey exists; deleting
   the token is belt and braces.)

Recommended: on the **Security** screen, **Add passkey** for a second device (it asks for your
current passkey first). You can have up to 5. The last one can never be removed.

### Signing in later

Open CONSOLE → **Sign in with passkey**. Sessions end after **1 hour of inactivity** and always
**8 hours** after sign-in. Destructive actions (revoke a device, revoke a keeper, block a
contributor, remove a passkey, regenerate recovery codes, add a passkey) ask for your passkey again
if you have not confirmed it in the last 5 minutes.

### Lost your passkey?

Open **`CONSOLE/recover`**, type one recovery code, then register a new passkey on the page that
follows (that is all a recovery sign-in can do; it expires after 15 minutes). Then sign in normally.
Afterwards, on **Security**, remove the lost passkey and **Regenerate** recovery codes if you are
running low.

---

## 9. Collect data every 15 minutes

The console stores snapshots when something calls `CONSOLE/api/cron/collect` with
`Authorization: Bearer <CRON_SECRET>`. Vercel's free plan only allows a daily cron, which is
already configured as a safety net. For a 15-minute rhythm use **one** of these:

### Option A — cron-job.org (simplest)

1. cron-job.org → **Create cronjob**.
2. **URL:** `https://bansho-console.vercel.app/api/cron/collect`
3. **Execution schedule:** every 15 minutes.
4. **Advanced → Request method:** `GET`. **Headers → Add:** key `Authorization`,
   value `Bearer ` followed by your CRON_SECRET (one space after `Bearer`).
5. **Timeout:** the maximum allowed (30 s). **Save**, then press **Test run** — the response should be
   HTTP 200 with `"status":"collected"` (or `"already_collected"` if you ran it twice in 5 minutes).

### Option B — GitHub Actions (already in the repo, off by default)

`.github/workflows/collect.yml` runs every 15 minutes but does nothing until you switch it on:

1. GitHub repo → **Settings → Secrets and variables → Actions**.
2. **Secrets → New repository secret:** `BANSHO_CONSOLE_URL` = `https://bansho-console.vercel.app`,
   and `BANSHO_CRON_SECRET` = your CRON_SECRET.
3. **Variables → New repository variable:** `BANSHO_COLLECT_ENABLED` = `true`.
4. **Actions** tab → *collect* → **Run workflow** once to test.

(GitHub may delay scheduled runs by a few minutes; that is fine. Using both A and B is harmless —
each 5-minute window is collected only once — just redundant.)

> **About Bus Board sleeping.** Render's free tier puts Bus Board to sleep after 15 idle minutes,
> and any request — including the collector's — wakes it. Pinging every 15 minutes therefore keeps it
> mostly awake, which uses Render free instance hours (one always-on service fits in the monthly free
> allowance, but they are shared with any other free Render services you run). If you prefer it to
> sleep, ping less often (e.g. every 30–60 minutes). The console labels "asleep" as a normal state
> and has a **Wake it** button.

---

## 10. Check everything

| Where | What you should see |
|---|---|
| `CONSOLE/api/health` | `{"ok":true}` |
| `CONSOLE/robots.txt` | `Disallow: /` |
| Console → **Overview** | Harahachi and Bus Board **Up** (or Bus Board **Asleep**), numbers in the KPI tiles, "Collector: n min ago" |
| Overview shows **Token rejected** | The ops token on that app differs from the console's, or is shorter than 48 characters |
| Overview shows **Not configured** | `HARAHACHI_URL`/`BUSBOARD_URL` or a token is missing on the console |
| Console → **Audit** | Your sign-in, and a `collect` row every time the cron runs |
| SITE | Both apps' pages; buttons only for links you configured |
| securityheaders.com on SITE and CONSOLE | A or A+ |

---

## Every environment variable, in one place

| Project | Variable | Required | Make it with |
|---|---|---|---|
| console | `DATABASE_URL` | yes | Neon Marketplace integration (automatic) |
| console | `BANSHO_RP_ID` | yes | the console's host name |
| console | `BANSHO_ORIGIN` | yes | `https://` + that host |
| console | `BANSHO_SETUP_TOKEN` | first setup only | `secrets.token_urlsafe(32)` |
| console | `CRON_SECRET` | yes | `secrets.token_urlsafe(32)` |
| console | `BANSHO_SITE_ORIGIN` | for analytics + landing uptime | the site's `https://` origin |
| console | `HARAHACHI_URL` | yes | Harahachi's address |
| console | `HARAHACHI_OPS_TOKEN` | yes | `secrets.token_urlsafe(48)` |
| console | `BUSBOARD_URL` | yes | Bus Board's Render address |
| console | `BUSBOARD_OPS_TOKEN` | yes | `secrets.token_urlsafe(48)` |
| console | `BANSHO_TZ` | no | default `Asia/Kolkata` |
| site | `VITE_HARAHACHI_APP_URL` | no | Harahachi's address |
| site | `VITE_HARAHACHI_APK_URL` | no | APK download link |
| site | `VITE_BUSBOARD_APP_URL` | no | Bus Board's address |
| site | `VITE_BUSBOARD_APK_URL` | no | APK download link |
| site | `VITE_INGEST_URL` | no | `CONSOLE/api/ingest` |
| Harahachi (Vercel) | `SEKISHO_OPS_TOKEN` | yes | **same value** as `HARAHACHI_OPS_TOKEN` |
| Bus Board (Render) | `MAPS_OPS_TOKEN` | yes | **same value** as `BUSBOARD_OPS_TOKEN` |
| GitHub Actions secret | `BANSHO_CONSOLE_URL` | option B | CONSOLE |
| GitHub Actions secret | `BANSHO_CRON_SECRET` | option B | same value as `CRON_SECRET` |
| GitHub Actions variable | `BANSHO_COLLECT_ENABLED` | option B | `true` |

### Rotating a secret

* **Ops token:** make a new one, set it on the app (`SEKISHO_OPS_TOKEN` / `MAPS_OPS_TOKEN`) and on the
  console at the same time, redeploy both. In between, the console shows "Token rejected" — nothing breaks.
* **CRON_SECRET:** change it on the console, in cron-job.org and/or the GitHub secret.
* **Sessions:** Security → *Sign out other sessions*.

---

## Running it on your computer (optional)

```bash
# Backend tests (SQLite; set BANSHO_TEST_DATABASE_URL=postgresql://... to run them on Postgres)
cd console
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # macOS/Linux: .venv/bin/pip
cd backend && ../.venv/Scripts/python -m pytest

# Console with fake data (seed/dev mode: fake apps, 30 days of history, a passkey-free dev sign-in)
cd console && .venv/Scripts/python backend/dev_server.py     # API on 127.0.0.1:8787
cd console && npm ci && npm run dev                          # open http://localhost:5173

# Landing site
cd site && npm ci && npm run dev                             # http://localhost:5174
```

The dev sign-in exists only in `npm run dev` builds and only works when the backend runs with
`BANSHO_DEV_SEED=1` on a `http://localhost` origin outside Vercel; production builds do not contain it.
