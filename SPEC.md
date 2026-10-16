# 番所 Bansho — private ops console + public landing for Harahachi and Bus Board

Owner: Harshavardhan (single user). Everything private is for him only.

## The three apps involved

| App | Repo (local) | Hosting | State |
|---|---|---|---|
| **Harahachi** (calorie tracker) | `D:\linux_workspace\calorietracker` | Vercel (Python FastAPI function) + Neon Postgres | Durable DB; existing password admin at `/admin` |
| **Bus Board** (maps; Trichy ↔ Thanjavur buses) | `D:\linux_workspace\maps` | Render free (Go gateway, single long-lived process, sleeps after 15 min idle) | **In memory only**, no DB; resets on sleep/deploy |
| **Bansho** (this) | `D:\linux_workspace\bansho` | Vercel — TWO projects from this repo: `site/` (public) and `console/` (private) + its own Neon Postgres | Durable DB: snapshots, site analytics, auth, audit |

## Service-to-service ops APIs (the contracts)

Both apps expose a **server-to-server ops API**, authenticated by a long random
bearer token known only to that app and Bansho's console backend:

- Header `Authorization: Bearer <token>`, constant-time compare.
- Token env var unset, shorter than 48 chars, or wrong → **404** (hide the
  surface; never 401/403). No CORS on these routes.
- Tokens never appear in any response, log line, or error message.
- Every write action is recorded in an audit table on the app side
  (`at, action, target, params_json, result`).

### Harahachi — base `/api/ops/v1`, token env `SEKISHO_OPS_TOKEN`

New instrumentation it needs first:
- `llm_calls` table: `at, user_id, slot (analyze|body|chat|insights|passthrough), model, ok, ms, tokens_in, tokens_out, error_code` — written for every upstream NIM call (tokens from the provider's `usage` when present).
- Client kind: the app sends `X-Harahachi-Client: apk/<version>` | `pwa/<version>` | `web/<version>` on every request; the server stores `client_kind` + `client_version` on the device row.
- An `active_days` notion: a user is active on a day if they made any authenticated request.

Endpoints:
- `GET /summary` → `{generated_at, version, totals:{users, devices_active, devices_revoked, meals, workouts, weights, push_subs, keepers}, today:{new_users, active_users, meals, ai_calls, ai_errors, cap_hits, requests, errors_5xx}, ai_by_slot:{<slot>:{calls, errors, p50_ms, p95_ms, tokens_in, tokens_out}} (last 24h), clients:{apk, pwa, web}, health:{db_ok, nim_configured, push_configured}}`
- `GET /timeseries?days=30` → `{days:[YYYY-MM-DD…], series:{new_users, dau, meals, meals_photo, meals_barcode, meals_manual, meals_chat, meals_quicklog, ai_calls, ai_errors, ai_tokens_in, ai_tokens_out, cap_hits, requests, errors_5xx, p95_ms, pushes_sent, pushes_failed: [numbers aligned to days]}}`
- `GET /retention?weeks=8` → weekly signup cohorts with % active in weeks 1..N.
- `GET /funnel` → `{paired_devices, registered_users, first_meal, active_3_days, active_7_days}`.
- `GET /users?q=&page=&sort=` → paged `{users:[{id, username, created_at, last_active, meals, streak_current, client_kind, is_admin, locked, ai_calls_7d}], total}`.
- `GET /users/{id}` → profile, targets, body fat, recent meals (30), weights, workouts, devices (with client kind, last seen, revoked), failed logins / lock state, AI usage by slot (30d).
- `GET /requests?status=&route=&limit=200` → recent request-log rows (no bodies/PII beyond what the request log already stores).
- `GET /audit?limit=200` → app-side ops audit rows.
- `POST /actions` with `{action, ...params}`; actions: `revoke_device{device_id}`, `grant_keeper{user_id}`, `revoke_keeper{user_id}`, `unlock_user{user_id}`, `set_model{slot, model, fallback?}`, `push_test{user_id}`, `pairing_token{}` (→ `{token, url, qr_png_base64, expires_in}`). Unknown action → 400.

### Bus Board — base `/v1/ops`, token env `MAPS_OPS_TOKEN`

Instrumentation it needs (all in-memory, bounded):
- Per-route request metrics middleware: count, 4xx, 5xx, latency histogram (p50/p95). **Must not break SSE** on `/v1/board/stream` (wrapper must keep `http.Flusher`; streams counted on open, not timed).
- SSE gauge: open now, total opened.
- Board query popularity: count per `(from_stop, to_stop)`, keep top 50, bounded memory.
- Pings: received / accepted / rejected by reason (from the existing contribute/trust pipeline).
- A random `boot_id` per process + `started_at`, so Bansho can detect restarts/sleeps and compute deltas.

Endpoints:
- `GET /v1/ops/stats` → `{version, boot_id, started_at, uptime_s, requests:{by_route:{"<METHOD path>":{count, errors_4xx, errors_5xx, p50_ms, p95_ms}}}, sse:{open, total_opened}, board_queries:{top:[{from, to, count}]}, pings:{received, accepted, rejected:{<reason>:n}}, contributors:{count, trust_histogram:{<bucket>:n}, top:[{id_hash, score, accepted}]}, vehicles:{active, by_route:{<route>:n}}, feed:{stops, unverified_stops, routes, version}, runtime:{goroutines, heap_mb, go_version}}`. All counters are cumulative **since boot**.
- `POST /v1/ops/contributors/{id}/block` and `/unblock` — blocked contributors' pings are rejected (reason `blocked`); blocklist is included in the existing snapshot persistence if a snapshot store is configured.
- `POST /v1/ops/snapshot` — save state now (no-op + clear message when no snapshot store).
- Contributor ids in responses are **hashed** (`id_hash`), the raw id is accepted only on the block/unblock path.

## Bansho

### `site/` — public landing (Vercel project 1)
- Static (Vite), no cookies, no third-party scripts, strict CSP.
- Pages: `/` (both apps), `/harahachi` (in Harahachi's ukiyo-e washi/sakura style — see `D:\linux_workspace\calorietracker\DESIGN.md`), `/busboard` (in Bus Board's own visual style — see `D:\linux_workspace\maps\apps\web`), `/privacy`.
- Calls to action come from build-time env: `VITE_HARAHACHI_APP_URL`, `VITE_HARAHACHI_APK_URL`, `VITE_BUSBOARD_APP_URL`, `VITE_BUSBOARD_APK_URL`, `VITE_INGEST_URL` (console origin + `/api/ingest`). Missing APK URL → button hidden, not broken.
- Analytics: `navigator.sendBeacon` to the console's ingest with `{type: pageview|click, path, ref_host, target?}`; nothing else. Respects `navigator.doNotTrack === "1"` and `globalPrivacyControl`.

### `console/` — private CRM (Vercel project 2)
Frontend: React + Vite SPA. Backend: Python FastAPI as a Vercel function (`api/index.py`), Neon Postgres via `DATABASE_URL` (SQLite fallback for tests/dev).

**Auth (owner only, passkeys):**
- WebAuthn passkeys (`webauthn` / py_webauthn server-side, `@simplewebauthn/browser` client). No passwords anywhere.
- First passkey registration requires `BANSHO_SETUP_TOKEN` (env, ≥32 chars) AND zero existing passkeys; after that the setup endpoint answers 404 forever. Additional passkeys can be added only from an authenticated session (max 5).
- On first setup, issue 10 recovery codes (shown once, stored hashed). A recovery code yields a short session that can ONLY register a new passkey; each code single-use.
- Sessions: server-side table, opaque random id in a `__Host-` cookie, `Secure; HttpOnly; SameSite=Strict; Path=/`, 8 h absolute + 1 h idle timeout, listed and revocable in the UI. CSRF: custom header required on every non-GET (`X-Bansho-CSRF` matching a per-session token).
- Login/setup/recovery endpoints rate-limited (DB-backed) and locked out progressively.
- Every login (success/failure), action, and session event → `audit` table (time, country from `x-vercel-ip-country`, UA, outcome). No raw IPs stored; use a daily-salted hash if correlation is needed.
- Security headers on everything: HSTS, strict CSP (no inline scripts), X-Frame-Options DENY, Referrer-Policy no-referrer, `X-Robots-Tag: noindex, nofollow`, Permissions-Policy minimal. `robots.txt` disallow all.
- `RP_ID`/origin from env (`BANSHO_RP_ID`, `BANSHO_ORIGIN`), validated strictly.

**Data:**
- `GET /api/cron/collect` (Bearer `CRON_SECRET` ≥32 chars, constant-time, else 401): pulls Harahachi `/summary` and Bus Board `/v1/ops/stats`, stores snapshot rows; for Bus Board computes per-interval deltas using `boot_id` (counter reset → new baseline, and records a restart/wake event); records an uptime check (status, latency) for Harahachi health, Bus Board health, and the landing site. Idempotent per 5-minute bucket. Pinged every 15 min by cron-job.org or a GitHub Actions workflow (ship one, disabled until configured). A Vercel daily cron as safety net.
- `POST /api/ingest` (public): accepts only the site's origin (CORS exact match), ≤1 KB JSON, rate-limited per hashed IP, stores `{at, type, path, ref_host, target, country, device_class (mobile/tablet/desktop from UA), is_bot}`. No cookies, no IP stored.
- All app data is fetched **server-side** with the ops tokens (`HARAHACHI_URL`, `HARAHACHI_OPS_TOKEN`, `BUSBOARD_URL`, `BUSBOARD_OPS_TOKEN`); short (30–60 s) cache; tokens never sent to the browser. Upstream down → the page shows that clearly (Bus Board asleep is a normal state, labelled as such, with a "wake it" button that just calls its health endpoint).

**Console screens:**
1. **Overview** — both apps at a glance: status/uptime (last 24 h / 30 d), key KPIs (Harahachi users, DAU, meals today, AI calls + errors + token burn today; Bus Board requests today, live SSE, active vehicles, pings), landing visits today, and an **alerts** list (app down, 5xx spike, AI error rate > 10 %, daily-cap hits, Bus Board restart storm, ingest anomalies).
2. **Harahachi** — tabs: Users (searchable CRM table → user detail with all data + actions), Growth (signups, DAU/WAU/MAU, retention cohorts, funnel), Food (meals by source, top logged dishes aggregate), AI (calls/errors/latency/tokens per slot, model per slot with switcher, cap hits), Reliability (requests, 5xx, p95, recent errors from the request log), Devices (APK vs PWA vs web, versions, revoke), Pairing (generate QR).
3. **Bus Board** — Live (uptime, boot timeline showing sleeps/wakes, SSE open, vehicles), Traffic (per-route requests/latency/errors over time from deltas), Demand (top from→to queries), Contributors (trust histogram, top contributors, block/unblock), Feed (stops, unverified count).
4. **Landing** — visits, unique-ish visitors (per-day hashed), top pages, referrers, countries, device classes, CTA clicks (open app / download APK per product).
5. **Audit** — every console login and action, plus each app's own ops audit.
6. **Security** — passkeys (add/rename/remove, never the last one), active sessions (revoke), recovery codes remaining (regenerate after re-auth).

Every write action asks for confirmation, and destructive ones (revoke device, revoke keeper, block contributor, remove passkey) require a **fresh passkey re-authentication** (step-up, < 5 min old).

Design: a calm, dense, readable console — ink-on-paper neutral palette with a single vermillion accent (`#C73E3A`, shared with Harahachi), system font stack + tabular numerals, light and dark mode. Charts as hand-built SVG or one small bundled library; no CDN scripts (CSP).
