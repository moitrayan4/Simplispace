# Simplispace — Full Project Documentation

> An Email Clutter Manager. Connects a Gmail inbox (read-only), groups mail by
> sending service, and scores each service with a transparent, explainable
> rule-based engine so the user can decide what to keep.

This document covers the project head to toe: purpose, scope, architecture,
every component, the data model, the scoring engine, the desktop packaging,
setup, running, security, limitations, and troubleshooting.

---

## 1. What Simplispace is

Most inboxes accumulate years of newsletters, promotions, and transactional
mail. Gmail gives little visibility into *which senders are actually worth
keeping*. Simplispace connects to Gmail (read-only), pulls message **metadata**
(never the email bodies), groups everything **by sending domain**, and assigns
each "service" a 0–100 score and a category (Important / Useful / Optional /
Marketing-Inactive). Every score is **explainable** — the exact signals that
produced it are shown.

Guiding principle from the proposal: **analyze automatically, unsubscribe
manually.** Nothing is ever unsubscribed without explicit user confirmation.

**This build implements weeks 1–8, 10 and 11** of the 12-week proposal:

| Week | Deliverable | Status |
|------|-------------|--------|
| 1–2 | Gmail OAuth 2.0 (read-only) + encrypted token storage + scaffolding | ✅ |
| 3–4 | Metadata sync + group by sender domain + data model | ✅ |
| 5–6 | Rule-based scoring engine + category buckets | ✅ |
| 7–8 | Per-service decision cards with Keep / Unsubscribe controls | ✅ |
| 9 | AI classification for ambiguous senders | not built |
| 10 | Confirmation-gated unsubscribe, with automatic "did it work?" checking | ✅ |
| 11 | Inbox health score + history, monthly reduction, time saved, security hardening | ✅ |
| 12 | Pilot, feedback, final report | team work |

---

## 2. Architecture

Three layers, one language family (Python + JS), single origin at runtime.

```
┌─────────────────────────────────────────────────────────┐
│ Desktop shell (PyWebView + WebView2)   desktop/main.py    │
│  • runs the API in-process (background thread)            │
│  • shows the web UI in a native window                    │
│  • opens unsubscribe pages / Gmail in in-app windows      │
└───────────────┬─────────────────────────────────────────┘
                │ http://localhost:8000 (same origin)
┌───────────────▼─────────────────────────────────────────┐
│ Backend  (FastAPI)                      backend/app/*     │
│  • OAuth, sync, scoring, unsubscribe, sender icons       │
│  • serves the built frontend                             │
│  • SQLite via SQLAlchemy                                 │
└───────────────┬─────────────────────────────────────────┘
                │ Gmail API (OAuth 2.0, gmail.readonly)
┌───────────────▼─────────────────────────────────────────┐
│ Google Gmail API                                         │
└──────────────────────────────────────────────────────────┘

Frontend (React + Vite)  frontend/src/*  — built to static files,
served BY the backend at "/", or run on the Vite dev server in development.
```

**Two run modes:**
- **Dev**: backend on `:8000` (uvicorn), frontend on `:5173` (Vite dev server,
  hot reload). CORS allows `:5173`, and the frontend calls `http://localhost:8000`.
- **Packaged / desktop**: one `Simplispace.exe`. The backend serves the built
  frontend from `/`, so the page and API share the origin `http://localhost:8000`
  (no CORS; the OAuth callback returns into the same app). The frontend uses
  relative API paths in this mode.

---

## 3. Technology stack (and why)

| Layer | Choice | Why |
|-------|--------|-----|
| Backend API | **FastAPI** + Uvicorn | Fast, typed, minimal boilerplate; team is Python. |
| DB | **SQLite** via **SQLAlchemy** | Zero-setup, file-based, ideal for a single-user desktop app. WAL mode for concurrent read/write. |
| Gmail | **google-api-python-client** + **google-auth-oauthlib** | Official libraries; handle OAuth 2.0 + token refresh. |
| Token encryption | **cryptography (Fernet)** + **Windows DPAPI** | Fernet encrypts OAuth tokens in the DB; DPAPI seals the Fernet key so only the same Windows user can read it. |
| Frontend | **React + Vite** | Industry-standard SPA; fast builds; vendor code split into a cached chunk. |
| Desktop shell | **PyWebView** (WebView2 backend, via pythonnet) | Wraps the web UI in a native window in pure Python; no Electron. |
| Packaging | **PyInstaller** (one file, `Simplispace.spec`) | Single `.exe`, no Python install needed on the target machine. |

Why not C++ / native widgets: the app is a dashboard talking to an API — no need
for native-level performance or a second toolchain.

---

## 4. Repository structure

```
Simplispace/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app: middleware (security headers, rate limit,
│   │   │                    #   gzip, static caching), CORS, routers, serves frontend
│   │   ├── config.py        # settings from .env (pydantic-settings)
│   │   ├── db.py            # engine/session; SQLite WAL + busy_timeout; schema upgrades
│   │   ├── models.py        # ORM models: User, Message, Decision, HealthSnapshot
│   │   ├── security.py      # Fernet encrypt/decrypt of OAuth tokens
│   │   ├── gmail.py         # OAuth flow (PKCE) + Gmail API helpers
│   │   ├── sync.py          # full + incremental sync, adaptive rate control
│   │   ├── scoring.py       # rule-based scoring engine + inbox health
│   │   ├── unsubscribe.py   # one-click / link / mailto actions + result checking
│   │   ├── icons.py         # sender logo lookup, cached on disk
│   │   └── routers/
│   │       ├── auth.py      # /auth/login, /callback, /status, /logout, /delete-account
│   │       └── services.py  # /sync, /services, /decisions, /unsubscribe, /insights, …
│   ├── requirements.txt     # minimum versions pinned past known vulnerabilities
│   ├── test_scoring.py      # self-check for the scoring engine
│   ├── test_sync.py         # self-check for sync, logout and icon caching
│   ├── .env.example         # config template (committed)
│   └── .env                 # real secrets (gitignored, NOT committed)
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # whole UI: login, privacy notice, sidebar app, dialogs
│   │   ├── App.css          # design system, light/dark themes, animations
│   │   ├── api.js           # fetch wrapper for the backend endpoints
│   │   ├── online.js        # online/offline detection (useOnline hook)
│   │   └── main.jsx, index.css
│   ├── public/              # logo-mark.png, favicon.png, 404.html
│   ├── index.html
│   └── package.json / vite.config.js
├── desktop/
│   ├── main.py              # PyWebView wrapper (dev + frozen), single-instance guard
│   └── dpapi.py             # Windows DPAPI protect/unprotect (ctypes)
├── docs/                    # public website (GitHub Pages): home, download,
│                            #   privacy, terms, 404
├── build_exe.ps1            # one-command build: frontend + exe (PowerShell)
├── Simplispace.spec         # PyInstaller recipe (gitignored)
├── icon.ico                 # exe/taskbar icon (gitignored)
├── dist/Simplispace.exe     # packaged app (gitignored)
├── README.md                # quick start
├── DOC.md                   # this document
└── .gitignore
```

Gitignored (never pushed): `mail/` (Python venv), `node_modules/`, `dist/`,
`build/`, `.env` (all), `*.db*`, `.claude/`, `proposal.pdf`, `icon.ico`,
`*.spec`, `.webview/`, `icons/`.

---

## 5. Data model (`backend/app/models.py`)

### `User`
A Gmail account that has signed in on this computer.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `email` | str, unique | Gmail address (from `users.getProfile`). |
| `encrypted_token` | text | OAuth credentials JSON, **Fernet-encrypted**. Empty after logout. |
| `signed_in` | bool | False after Log out. The data stays; signing back in restores it. |
| `replied_domains` | text (JSON list) | Domains the user has sent mail to (the "reply" signal). |
| `last_synced_at` | datetime | Last successful sync (UTC). |
| `sync_page` / `sync_page_token` | int / text | Where an unfinished full sync resumes. |
| `history_id` | str | Gmail history point for incremental sync. Empty until the whole inbox is indexed. |
| `history_start` | str | History point captured when a full sync started. |
| `created_at` | datetime | |

### `Message`
One row per synced Gmail message (metadata only).

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | |
| `user_id` | FK → users.id | |
| `gmail_id` | str | Gmail message id; unique per user. |
| `sender_email` | str | Parsed from the `From` header. |
| `sender_domain` | str, indexed | The grouping key (e.g. `mailer.ajio.in`). |
| `subject` | text | |
| `date` | datetime | From `Date` header, fallback `internalDate`. |
| `labels` | text | Comma-joined Gmail label ids (e.g. `UNREAD,CATEGORY_PROMOTIONS`). |
| `snippet` | text | No longer stored (minimal retention); old values are blanked. |
| `list_unsubscribe` | text | Value of the `List-Unsubscribe` header ("" if absent). |
| `is_unread` | bool | `"UNREAD" in labels`. Proxy for "opened". |
| `logo_url` | text | Legacy column kept for old databases; unused. |

Index `ix_messages_user_domain_date` (user, domain, date) speeds up the
per-sender queries.

### `Decision`
One row per (user, sender domain): `decision` (`keep` / `unsubscribe`),
`unsubscribed_via` (`one_click` / `link` / `mailto`), `unsubscribed_at`,
`page_result` (what the sender's unsubscribe page said, when opened in the app),
`updated_at`.

### `HealthSnapshot`
One inbox health score per user per day (`day`, `score`), used for the trend line.

Email bodies are never fetched or stored.

---

## 6. Backend in detail

### 6.1 Config (`config.py`)
Loaded from `.env` via `pydantic-settings`:

| Setting | Default | Purpose |
|---------|---------|---------|
| `google_client_id` / `google_client_secret` | — | From the Google Cloud OAuth client. |
| `google_redirect_uri` | `http://localhost:8000/auth/callback` | Must match the Cloud Console redirect URI. |
| `fernet_key` | — | Key that encrypts OAuth tokens. The desktop app generates it and seals it with DPAPI. |
| `database_url` | `sqlite:///./simplispace.db` | SQLite file. |
| `frontend_origin` | `http://localhost:5173` | CORS + post-login redirect. Overridden to `:8000` in desktop mode. |
| `sync_months` | `0` | `0` = whole mailbox; `N` = only the last N months. |
| `scopes` | `["gmail.readonly"]` | The **only** scope ever requested. |

### 6.2 Sign-in, log out, delete account (`gmail.py` + `routers/auth.py`)

Authorization Code flow **with PKCE**.

1. `GET /auth/login` → builds the Google consent URL (`access_type=offline`,
   PKCE `code_challenge`), stores the `code_verifier` keyed by `state` (at most
   20 pending), redirects to Google.
2. Google redirects to `GET /auth/callback?code=…&state=…`.
   - `state` is validated (CSRF protection); an `error` from Google is handled.
   - The code is exchanged for tokens using the stored verifier.
   - The account email is fetched; the credentials are **Fernet-encrypted**.
   - That account is marked signed in; any other account is marked signed out.
     An account that logged out earlier gets its senders and choices back.
3. `GET /auth/status` → `{connected, needs_reconnect, email, last_synced_at, indexing}`.
   `indexing` is true until the first pass through the whole inbox has finished.
4. `POST /auth/logout` → revokes the token at Google (best effort), clears it,
   marks the user signed out. **All analysis is kept** for the next sign-in.
5. `POST /auth/delete-account` → revokes the token and **erases everything**
   stored for the account (messages, decisions, health history, the user row).

The user stays signed in across app restarts until they press Log out.

### 6.3 Sync (`sync.py` + `routers/services.py`)

`POST /sync` starts a **background thread** and returns immediately. The UI
polls `GET /sync/status` for live progress
(`listing → fetching (done/total) → scanning_sent → done`, or `error`).

**First (full) sync — whole mailbox:**
1. List received message ids with `-in:sent -in:chats` (plus `newer_than:Nm`
   only if `sync_months` > 0). Page 1 is small (`FIRST_PAGE_SIZE = 250`) so
   scores appear in about 4 seconds; later pages are also 250 (`PAGE_SIZE`),
   so the scores refresh about every 45 seconds.
2. Skip ids already stored.
3. **Batch-fetch metadata** (`BATCH_SIZE = 25` per Gmail batch request,
   `format=metadata`, headers `From, Subject, Date, List-Unsubscribe`).
4. After page 1, scan the newest 500 sent emails (`To`/`Cc`) to build
   `replied_domains`.
5. Commit after every page and save the page token, so an interrupted sync
   resumes where it stopped (also after the app is closed and reopened).
6. When the last page is done, store `history_id` — the inbox is fully indexed.

**Later (incremental) syncs:** use the Gmail **History API** to apply only what
changed since `history_id`: new mail, deleted mail, label changes (read/unread,
spam, trash). Takes seconds.

**Expired history:** Gmail keeps change history for about a week. If it has
expired (e.g. the user was logged out longer), the app lists every message id
still in Gmail, **removes stored mail that is gone** (`prune_missing`), then
runs a full sync that fetches only mail it doesn't already have.

**Adaptive rate control:** Gmail limits quota units per minute per user. The
sync starts fast, **halves its rate and waits 5 s** when Gmail pushes back, and
speeds up by 0.5 msg/s after each clean batch (`MIN_RATE = 3`, `MAX_RATE = 40`).
Transient network errors are retried with backoff. With the default project
quota the sustained rate is about 5–6 messages/second, so a first full pass
over ~17k emails takes roughly 50 minutes; scores keep updating throughout.

**Offline:** network failures stop the sync with a friendly "offline" status
instead of an error; it resumes when the connection is back.

**Expired token:** if a sync hits `invalid_grant`, the token is cleared and the
UI shows **Reconnect** — the account's data is kept.

### 6.4 Scoring engine (`scoring.py`)

Tier-1, transparent, **pure functions**, no external calls. (Tier-2 AI for
ambiguous senders is week 9 — not built.)

**Rubric** — start at `BASE_SCORE = 50`, then add/subtract per signal:

| Signal | Points | Condition |
|--------|-------:|-----------|
| Promotional | −30 | ≥50% of the service's messages have Gmail's `CATEGORY_PROMOTIONS` label |
| Has List-Unsubscribe | −20 | ≥50% of messages carry a `List-Unsubscribe` header |
| Replied | +40 | The sender's domain is in `replied_domains` |
| Transactional | +35 | Any subject contains a transactional keyword (order, invoice, otp, …) |
| Opened frequently | +25 | Open rate ≥ 0.5 (open rate = fraction not `UNREAD`) |
| 50+ never opened | −40 | ≥50 messages AND open rate < 0.1 AND not replied |

Final score is **clamped to 0–100**.

| Score | Category |
|-------|----------|
| ≥ 70 | Important |
| 40–69 | Useful |
| 20–39 | Optional |
| < 20 | Marketing/Inactive |

**Explainability:** every scored service returns the exact signals that fired,
each with its label and point value — shown under **Show why** on each card.

**Per-service stats:** `email_count`, `open_rate`, `days_since_last`,
`has_unsubscribe`, and `name` (from the domain, e.g. `mailer.ajio.in` → "Ajio").

`BASE_SCORE`, the ratios and thresholds at the top of `scoring.py` are
**calibration knobs** — tune them against pilot Keep/Unsubscribe decisions to hit
the proposal's ≥85% classification-agreement target.

**Worked example:** a promotional newsletter (all promotional-labelled, has
List-Unsubscribe, never opened): `50 − 30 − 20 = 0` → **Marketing/Inactive**.
An order-confirmation service you reply to and open: `50 + 40 + 35 + 25 − 20 =
130 → 100` → **Important**.

**Inbox health:** value-weighted share of the last 90 days of mail
(Important/Useful = 1, Optional = 0.5, Marketing = 0), excluding unsubscribed
senders. Time saved assumes 10 s per email (`SECONDS_PER_EMAIL`).

### 6.5 Unsubscribe (`unsubscribe.py`)

Runs only after the user confirms in a dialog. Targets are re-read from Gmail on
the server at confirmation time; the client never supplies a URL. Sender headers
are untrusted: only `https`/`http`/`mailto` targets are accepted.

- **One-click** (sender supports RFC 8058): HTTPS POST of
  `List-Unsubscribe=One-Click` to the sender's URL. Only public internet hosts;
  redirects refused.
- **Open page:** the sender's unsubscribe page opens in a Simplispace window.
- **Email:** a Gmail compose window opens pre-filled; the user sends it.

**Bulk unsubscribe:** the dialog checks each selected sender. Those that support
one-click are unsubscribed together after one confirmation (`no_fallback`, so a
refusal is reported instead of opening a window per sender); the rest are listed
to do one by one, since they need their own page or an email.

**Did it work?** The in-app window reads the page text (or Gmail's own notices,
never the email itself) and records `success` / `failed`; if unclear, the app
asks the user. Independently, mail from the sender after a 2-day grace period
marks it **still sending**; a sync 3+ days later with no new mail marks it
**confirmed**.

### 6.6 Sender icons (`icons.py`)
Logos are looked up by **domain only** (never anything from an email), from
Google's and DuckDuckGo's icon services or the site's `/favicon.ico`, capped at
200 KB, SVG rejected, cached in `icons/`. Real misses are cached for 7 days;
lookups that fail because the computer is offline are **not** cached.

### 6.7 API reference

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness check. |
| GET | `/auth/login` | Start Google OAuth. |
| GET | `/auth/callback` | OAuth redirect target. |
| GET | `/auth/status` | Sign-in and sync state. |
| POST | `/auth/logout` | Sign out; keep data. |
| POST | `/auth/delete-account` | Sign out and erase all data. |
| POST | `/sync` | Start a background sync (full or incremental). |
| GET | `/sync/status` | Live progress. |
| GET | `/services` | Scored, categorized senders with signals. |
| GET | `/services/{domain}/emails` | The sender's 10 newest emails (subject, date, unread) for the preview. |
| GET | `/decisions` | Keep/Unsubscribe history with verification status. |
| PUT | `/decisions/{domain}` | Save or undo a decision. |
| GET | `/unsubscribe/{domain}` | Available unsubscribe methods for a sender. |
| POST | `/unsubscribe/{domain}` | Run the chosen method. |
| POST | `/unsubscribe/{domain}/answer` | User's answer when the page result was unclear. |
| GET | `/unsubscribe-handoff/{token}` | Page that sends a one-click POST from the user's browser. |
| POST | `/open-in-app` | Open a web link in a Simplispace window. |
| GET | `/icon/{domain}` | Cached sender logo. |
| GET | `/insights` | Health score, trend, reductions, time saved. |
| GET | `/` (+ assets) | Built frontend (packaged mode). |

Interactive API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled.

### 6.8 Database (`db.py`)
SQLite with `check_same_thread=False`, `PRAGMA journal_mode=WAL` and a 30 s
busy timeout (readers don't block the sync writer). `ensure_schema` adds new
columns and indexes to older databases on startup, so updates never lose data.
All queries go through SQLAlchemy (parameterized). The decisions list uses one
grouped query instead of one query per sender (N+1 fix).

---

## 7. Frontend in detail (`frontend/src/App.jsx`)

Single-file SPA with three stages:

1. **Login** — logo, one-line value prop, **Continue with Google**. Shows an
   offline notice when there is no connection.
2. **Privacy notice** — shown once after the first sign-in (`privacyAck` in
   `localStorage`).
3. **App** — a collapsible sidebar with views:
   - **Overview:** health score with stat tiles, category tiles, senders still
     emailing after an unsubscribe, and suggested senders to unsubscribe from.
   - **All senders:** paginated service cards, filterable by category,
     sortable, and searchable by name, domain or address (`/` focuses search).
     Tick cards (or Select all) to **bulk unsubscribe**; keyboard shortcuts:
     arrows move between cards, K keep, U unsubscribe, X select, E recent emails.
   - **History:** Keep/Unsubscribe decisions with Undo and verification status
     ("Show more" paging).
   - **Profile:** account, last sync, senders found, emails analysed, Sync now,
     Log out.
   - **Privacy & data:** what is stored and why.
   - **Settings:** theme (system / light / dark), zoom, sender logos on/off,
     and a danger zone with **Delete account**.

**Service cards:** name/domain, logo or first letter, category, score, email
count, open rate, last-email age, **Show why**, and **Recent emails** (the 10
newest subjects and dates, unread in bold). **Keep** confirms and moves
the sender to History. **Unsubscribe** opens the method dialog (§6.5). Cards are
memoized so a sync update doesn't redraw every card.

**Sync behaviour:**
- Syncs automatically **every time the app opens or the user signs back in**,
  when the connection comes back, and every 30 minutes while open.
- The very first sync after sign-up shows a progress bar until the first scores
  appear; later syncs run quietly.
- While the first full pass is still reading the inbox, the sidebar status reads
  "Reading inbox… N emails"; afterwards "Synced just now".

**Dialogs:** Log out explains that data is kept; Delete account shows real counts
of what will be erased and needs explicit confirmation.

**States and messages:** loading skeletons, empty states, dismissible errors,
auto-hiding success messages, and a 404 view for unknown pages.

**Offline (`online.js`):** checks connectivity (`generate_204`) every few
seconds. While offline, a banner says so, saved results stay usable (Keep / Undo
still work), and Sync / Reconnect / Unsubscribe are disabled. "You're back
online" appears on reconnect and a sync starts.

**API layer (`api.js`):** `fetch` wrapper. Base URL from `VITE_API_BASE`,
`http://localhost:8000` on the Vite dev server, otherwise same-origin. Every
write sends `X-Simplispace: 1`.

**Build:** Vite minifies JS/CSS and splits `node_modules` into a cached `vendor`
chunk. Lighthouse scores 100 in Performance, Accessibility, Best Practices and SEO.

---

## 8. Desktop app & packaging

### 8.1 The wrapper (`desktop/main.py`)
- **Single instance:** a named Windows mutex; opening the app again brings the
  existing window to the front.
- **Per-user data:** frozen mode stores everything in
  `%LOCALAPPDATA%\Simplispace` (database, icons, `.env`, WebView session).
  Data from older builds beside the exe is migrated once (SQLite backup API).
- **Sign-in key:** generated on first run, stored as `FERNET_KEY_DPAPI` sealed
  with Windows DPAPI. Older plain-text keys are sealed and removed. A key
  sealed by another Windows account can't be read, so the app asks to reconnect.
- **OAuth client:** shipped inside the exe (`oauth.env`); a local `.env` can
  override it.
- Starts Uvicorn on `127.0.0.1:8000`, waits for `/health`, then opens the window
  at `http://localhost:8000`. If the server can't start, a Windows message box
  explains why.
- OAuth runs **inside the window** with a desktop-Chrome user-agent; the
  WebView2 session persists so the user stays signed in.

### 8.2 Building the exe (`build_exe.ps1`)
From the repo root:
```
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```
Steps: stop any running instance (so the exe isn't locked) → `npm run build` →
write `build/oauth.env` with only the Google client settings from
`backend/.env` → `PyInstaller --clean Simplispace.spec` (uses the `mail` venv).
Output: `dist/Simplispace.exe` (~45 MB). No developer key or data is bundled.

**Standing rule:** after any frontend/backend change, rebuild the exe.

### 8.3 Icon
White mark on a black rounded tile, `icon.ico` (16→256 px), for the exe and
taskbar. `logo-mark.png` / `favicon.png` are used inside the UI. If Windows shows
a stale icon, clear the icon cache and restart Explorer.

---

## 9. Setup & running

### 9.1 One-time Google setup
1. Google Cloud Console → new project → **enable the Gmail API**.
2. **OAuth consent screen**: User type External; add testers as **Test users**
   (or publish the app so tokens stop expiring after 7 days).
3. **Credentials → OAuth client ID**; add redirect URI
   `http://localhost:8000/auth/callback`.
4. Put the client id/secret in `backend/.env` (see `.env.example`). For dev, add
   a `FERNET_KEY`:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
5. Optional: request a higher Gmail API quota to speed up first syncs.

### 9.2 Run in development
```
# backend (from backend/)
..\mail\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
# frontend
cd frontend && npm run dev        # http://localhost:5173
```

### 9.3 Run the desktop app
```
cd frontend && npm run build
mail\Scripts\python.exe desktop\main.py      # or run dist\Simplispace.exe
```
The exe needs only the Windows WebView2 runtime (preinstalled on Windows 11).

### 9.4 Tests
```
cd backend
..\mail\Scripts\python.exe test_scoring.py
..\mail\Scripts\python.exe test_sync.py
```
`test_scoring.py` (10 checks): scoring buckets, grouping, inbox health,
unsubscribe parsing, verification and page reading.
`test_sync.py` (8 checks): incremental sync, expired history, pruning deleted
mail, retries, paging, offline icon lookups, the email preview, logout vs
delete account.

---

## 10. Security & privacy

- **Minimal scope:** only `gmail.readonly`. The app cannot send, delete, or
  modify mail.
- **No bodies stored:** only metadata (sender, subject, date, labels,
  List-Unsubscribe header).
- **Tokens encrypted at rest** with Fernet; the key is sealed with DPAPI and
  never shipped in the exe.
- **Local API hardening:**
  - POST/PUT need an `X-Simplispace: 1` header (other sites can't add it without
    a CORS preflight); CORS allows only the app's origin, no credentials.
  - Only `localhost`/`127.0.0.1` Host headers are accepted (blocks DNS rebinding).
  - Rate limit: 60 requests/minute per route for writes and `/auth/*` (429).
  - Path inputs (sender domains) are validated by length and pattern.
- **Security headers:** Content-Security-Policy, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, Permissions-Policy,
  Cross-Origin-Opener-Policy. The website (`docs/`) sets a CSP too.
- **CSRF:** OAuth `state` validated on callback; PKCE used.
- **Safe sign-out:** a running sync is stopped before data changes. Log out
  revokes Google access; Delete account also erases all local data.
- **Unsubscribe safety:** confirmation first; one-click only to public hosts.
- **Dependencies:** minimum versions pinned past known vulnerabilities
  (pip-audit, npm audit: 0 known issues at the last audit).
- **Secrets never committed:** `.env`, databases and the venv are gitignored.

---

## 11. Known limitations & deliberate shortcuts

Marked with `ponytail:` comments in code where relevant.

- **One signed-in account at a time** — other accounts' data is kept but hidden
  until they sign in again.
- **In-memory OAuth state and sync progress** — fine for a single-process
  desktop app; one sync at a time.
- **`replied_domains` as a flat JSON column** — small set, no join table.
- **First full sync is quota-bound** (~5–6 msg/s with the default quota).
- **Open/reply are proxies** — "opened" = not `UNREAD`; "replied" = you sent
  mail to that domain.
- **Unverified OAuth app** — in Testing mode Google shows an "unverified app"
  screen and tokens expire after 7 days. Removing that needs Google verification
  (CASA assessment for a restricted scope).
- **One-file exe** — a few seconds of extraction on launch.

---

## 12. Evaluation criteria (from the proposal)

- **Primary — Classification agreement:** % of services where the system's
  category matches the user's own Keep/Unsubscribe decision. Target ≥ 85% in a
  5–10 user pilot.
- **Secondary:** monthly low-value emails removed; projected time saved; a
  0–100 inbox health score tracked over time.

---

## 13. Roadmap

- **9 (not built):** tier-2 AI classification for ambiguous mid-band senders
  (Essential / Transactional / Marketing / Social) with a confidence value.
- **12:** testing, pilot feedback, final report.
- Publishing: Google app verification, a GitHub Release with the exe, a higher
  Gmail quota.

---

## 14. Troubleshooting / FAQ

- **"Google OAuth not configured"** → client id/secret missing in `backend/.env`
  (dev) or the exe was built without them. Fill them in and rebuild.
- **"Google hasn't verified this app" / 403 access_denied** → app in Testing
  mode. Click **Continue**, and make sure the Gmail is a Test user.
- **Asked to Reconnect** → the Google token expired or was revoked (Testing-mode
  tokens last 7 days). Reconnect; data is kept.
- **First sync is slow** → Gmail quota; scores appear early and keep updating.
  Closing the app is fine — it resumes on reopen.
- **"You're offline" banner** → no internet; saved results still work and sync
  resumes automatically.
- **"Simplispace couldn't start" / port 8000 in use** → another program holds
  the port; close it or restart the computer.
- **ERR_CONNECTION_REFUSED in the window** → the app was stopped underneath the
  window (e.g. by a rebuild); reopen it.
- **Exe shows an old icon** → Windows icon cache; clear it and restart Explorer.

---

## 15. Glossary

- **Service** — a group of messages sharing one sender domain (e.g. `quora.com`).
- **Signal** — a single scoring factor that fired for a service, with its point
  value; the basis of explainability.
- **Open rate** — fraction of a service's messages without the `UNREAD` label.
- **Full / incremental sync** — reading the whole mailbox vs applying only
  Gmail's recorded changes (History API).
- **Tier 1 / Tier 2** — rule-based scorer (built) vs AI classifier (not built).
- **Frozen** — running inside the PyInstaller-packaged exe.
- **PKCE** — Proof Key for Code Exchange; the OAuth security extension used here.
- **DPAPI** — Windows Data Protection API; encrypts data so only the same Windows
  user can decrypt it.
