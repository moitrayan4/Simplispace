# Simplispace

**A Windows desktop app** that connects a Gmail inbox (read-only, metadata only),
groups mail by sending service, scores each sender with a transparent rule-based
engine, and helps you keep or unsubscribe. It ships as a single `Simplispace.exe`.

The React frontend runs inside the app's own native window (PyWebView); it is not a
hosted website. The `docs/` folder is separate: it is only the project's public
homepage, privacy policy and terms, served by GitHub Pages for Google's OAuth review.

Built so far: **weeks 1–8, 10 and 11** of the proposal (OAuth, metadata sync and
grouping, rule-based scoring and categories, per-sender decision cards, the
confirmation-gated unsubscribe flow, and the inbox health score with savings
estimates). Week 9 (AI classification) is not built yet. See `DOC.md` for the full
design notes.

## Project layout

| Path | What it is |
|------|------------|
| `desktop/` | App entry point (`main.py`): starts the API in-process and opens the native window |
| `backend/` | FastAPI + SQLite (SQLAlchemy): Gmail sync, scoring, unsubscribe |
| `frontend/` | React + Vite UI shown inside the app window |
| `Simplispace.spec`, `icon.ico`, `build_exe.ps1` | PyInstaller build of `dist/Simplispace.exe` |
| `setup.ps1`, `requirements.txt` | One-time developer setup |
| `docs/` | Project website (GitHub Pages), not part of the app |

## Setup (once, after cloning)

Needs Windows 10/11, Python 3.13, Node.js 20+ and the Microsoft WebView2 runtime
(preinstalled on Windows 11).

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

This creates the Python venv in `mail\`, installs Python and npm packages, and
creates `backend\.env` from `backend\.env.example` with a fresh `FERNET_KEY`.

Then add Google OAuth credentials to `backend\.env`:

1. In Google Cloud Console, create a project, enable the **Gmail API**, and create an
   **OAuth 2.0 Client ID** (type *Web application*) with
   `http://localhost:8000/auth/callback` as an authorized redirect URI.
2. Add your Gmail address as a test user on the OAuth consent screen.
3. Put the id and secret in `backend\.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).

Ask the project owner for the shared client if you'd rather not create your own.
Never commit `backend\.env`; it is gitignored.

## Run the app

```powershell
cd frontend; npm run build; cd ..
mail\Scripts\python.exe desktop\main.py
```

The app serves the built frontend from its own API on `localhost:8000` and shows it
in a native window. Google sign-in runs inside the window and returns to the app.
In dev mode the database (`simplispace.db`) and icon cache live in `backend\`.

### Faster UI work (hot reload)

For frontend changes you can skip the window and use Vite's dev server, in two terminals:

```powershell
# 1. API, from backend\
..\mail\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
# 2. UI
cd frontend; npm run dev        # http://localhost:5173
```

## Build the .exe

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Output: `dist\Simplispace.exe`, a single file anyone can run. The build bundles only
the app's Google OAuth client (`GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` from
`backend\.env`); the developer's `FERNET_KEY` and data are never shipped. On first
launch each Windows user gets `%LOCALAPPDATA%\Simplispace` with their own database,
sign-in, icon cache and a new encryption key sealed with Windows DPAPI.

**Letting any Gmail account sign in:** in Google Cloud Console, *Google Auth
Platform*, *Audience*, press **Publish app**. Until Google verifies the app, users
see an "unverified app" warning and at most 100 accounts can sign in. While the app
is in *Testing* mode, Google expires refresh tokens after 7 days and the app shows
**Reconnect Gmail**.

## How it works

**Sync:** the first sync indexes the whole received mailbox in pages of 250
messages, so the first senders appear quickly while later pages continue in the
background. After that, the app keeps Gmail's `historyId` and every app open or
sign-in fetches only new, deleted and re-labelled mail through the History API. If
Gmail has dropped that history (about a week), the app does a full pass and removes
mail that was deleted meanwhile. Rate limits, 5xx errors and dropped connections are
retried with backoff.

**Senders view:** search (`/`), bulk select and unsubscribe, a recent-emails preview
per sender, and keyboard shortcuts (arrows, `K` keep, `U` unsubscribe, `X` select,
`E` recent emails).

**Unsubscribe:** nothing happens until you confirm a method: a one-click request
(RFC 8058, sent to the sender's own HTTPS endpoint), the sender's unsubscribe page,
or an unsubscribe email draft. The Gmail scope stays read-only; nothing is sent from
Gmail. Any unsubscribe can be undone from its card or from History.

**Health score:** the share of the last 90 days of mail from Important/Useful
senders (Optional counts half), excluding senders you unsubscribed from. Savings
estimates use each sender's monthly volume and 10 seconds per email.

**Logout vs delete:** Logout removes Gmail access but keeps senders and analysis for
the next sign-in. Delete account in Settings erases everything.

## Tests

```powershell
cd backend
..\mail\Scripts\python.exe test_scoring.py
..\mail\Scripts\python.exe test_sync.py
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | /health | liveness |
| GET | /auth/login | start Google OAuth |
| GET | /auth/callback | OAuth redirect target |
| GET | /auth/status | connected account and last sync |
| POST | /auth/logout | revoke Gmail access, keep local data |
| POST | /auth/delete-account | revoke access and erase all local data |
| POST | /sync | start or continue a sync |
| GET | /sync/status | sync progress |
| GET | /services | scored, categorized sender list |
| GET | /services/{domain}/emails | 10 most recent emails from a sender |
| GET | /icon/{domain} | cached sender icon |
| GET | /decisions | saved Keep/Unsubscribe decisions |
| PUT | /decisions/{domain} | keep or reset a sender (`{"decision": "keep"\|null}`) |
| GET | /unsubscribe/{domain} | unsubscribe methods the sender offers |
| POST | /unsubscribe/{domain} | run a confirmed unsubscribe (`{"method": "one_click"\|"link"\|"mailto"}`) |
| POST | /unsubscribe/{domain}/answer | record whether a manual unsubscribe worked |
| POST | /open-in-app | open a link in a Simplispace window |
| GET | /insights | health score, history, emails and time saved |

POST/PUT requests must carry the `X-Simplispace: 1` header, and only
`localhost`/`127.0.0.1` Host headers are accepted, so other websites can't drive the
local API.
