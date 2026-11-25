# Simplispace

**A Windows desktop app** that connects a Gmail inbox (metadata only, read-only by default),
groups mail by sending service, scores each sender with a transparent rule-based
engine, and optionally requests an AI second opinion for ambiguous senders. It helps
you keep or unsubscribe. It ships as a single `Simplispace.exe`.

The React frontend runs inside the app's own native window (PyWebView); it is not a
hosted website. The `docs/` folder is separate: it is only the project's public
homepage, privacy policy and terms, served by GitHub Pages for Google's OAuth review.

## Project layout

| Path | What it is |
|------|------------|
| `desktop/` | App entry point (`main.py`): starts the API in-process and opens the native window |
| `backend/` | FastAPI + SQLite (SQLAlchemy): Gmail sync, scoring, unsubscribe |
| `frontend/` | React + Vite UI shown inside the app window |
| `Simplispace.spec`, `icon.ico`, `build_exe.ps1` | PyInstaller build of `dist/Simplispace.exe` |
| `setup.ps1`, `requirements.txt` | One-time developer setup |
| `server/` | AI server (Cloudflare Worker), deployed separately |
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

1. In Google Cloud Console, create a project, enable the **Gmail API**, configure
   the OAuth consent screen, and create an **OAuth 2.0 Client ID** of type *Desktop app*.
2. Add your Gmail address as a test user on the OAuth consent screen.
3. Put the id and secret in `backend\.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).
   Sign-in happens inside the Simplispace window and returns to the app's own API on
   `localhost:8000`; no redirect URI needs to be registered.

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

### Run locally on macOS

The backend and React app run on macOS in a browser. The packaged native desktop
launcher and `.exe` build are Windows-only.

Use Python 3.13 and Node.js 20.19+ (or 22.12+). From the project root:

```bash
python3 --version
node --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
cp backend/.env.example backend/.env
```

Edit `backend/.env` and set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` from a
Google OAuth *Desktop app* client. For local development, sign-in runs in the
same browser tab and returns to the app. Generate and set
`FERNET_KEY` with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run the API and frontend in two Terminal windows, from the project root:

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm run dev -- --host localhost
```

Open <http://localhost:5173>. Keep the API terminal running while using the app.
The frontend calls the API at `localhost:8000`; `backend/.env` already sets the
matching frontend origin. AI is optional; configure its provider in `backend/.env`
and enable it in Settings if you want AI second opinions.

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
the app's Google OAuth desktop client and the AI server's address (`GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET` and `AI_SERVER_URL` from `backend\.env`); the developer's `FERNET_KEY`,
`AI_API_KEY` and data are never shipped. On first
launch each Windows user gets `%LOCALAPPDATA%\Simplispace` with their own database,
sign-in, icon cache and a new encryption key sealed with Windows DPAPI.

**Public release:** the app requests `gmail.readonly`, which Google classifies as a
restricted OAuth scope, and `gmail.send` (sensitive) when someone first sends an unsubscribe
email. Google also doesn't allow sign-in inside app windows, so that has to move to the browser first. Before offering the app broadly, configure and publish the
consent screen, complete Google's OAuth verification for the scope, and satisfy any
additional review requirements Google identifies. Testing-mode accounts are limited
to configured test users and may need to reconnect when refresh tokens expire.

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

**Unsubscribe:** nothing happens until you choose a method. Senders with an unsubscribe header get an automatic
option, either a one-click request (RFC 8058, sent to the sender's own HTTPS endpoint) or an email to the
unsubscribe address they publish, sent from your Gmail. They also get Gmail's own Unsubscribe button, which
opens in a Simplispace window that reads Gmail's notice, so you're never asked whether it worked. For
senders with no option, AI drafts an email that you check and send from your own Gmail (`gmail.send` is
requested only then); each sync then
checks that it was sent and what the reply (Gmail's one-line preview of it) says: the sender is marked done,
or its card comes back with blocking in Gmail as the next option. Undo resets
Simplispace's record; it can't reverse a real unsubscribe, email or block.

**Health score:** the share of the last 90 days of mail from Important/Useful
senders (Optional counts half), excluding senders you unsubscribed from. Savings
estimates use each sender's monthly volume and 10 seconds per email.

**AI second opinions (optional, off by default):** the app can ask the Simplispace AI server (`server/`, a
Cloudflare Worker that holds the Groq key) about ambiguous senders. Set its address as `AI_SERVER_URL` in
`backend/.env`; `build_exe.ps1` ships that address, and never an AI key. Users are identified by a Google ID
token and limited per account and overall. It gets the sender's name, domain and counts,
never subject lines, and looks the company up on its website and Wikipedia. It accepts only sign-ins made
with the project's own Desktop client. Developers can set their own `AI_API_KEY` instead, which takes
priority, calls any OpenAI-compatible endpoint directly and also sends up to three recent subject lines. Results are cached per sender, and the
rule-based category always stays visible. See `server/README.md` to deploy.

**Logout vs delete:** Logout removes Gmail access and signs out of Google in the app window, but keeps
senders and analysis for the next sign-in. Delete account in Settings erases everything.

## Tests

```powershell
cd backend
..\mail\Scripts\python.exe test_scoring.py
..\mail\Scripts\python.exe test_sync.py
..\mail\Scripts\python.exe test_ai.py
..\mail\Scripts\python.exe test_unsubscribe.py
..\mail\Scripts\python.exe test_email.py
..\mail\Scripts\python.exe test_security.py

# AI server, from server\
npm install; npm test
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | /health | liveness |
| GET | /start/{code} | one-time: gives the app window its session cookie |
| GET | /auth/login | start Google OAuth in the app window (`?send=true` adds sending) |
| GET | /auth/callback | OAuth redirect target |
| GET | /auth/status | connected account and last sync |
| POST | /auth/logout | revoke Gmail access, keep local data |
| POST | /auth/delete-account | revoke access and erase all local data |
| POST | /sync | start or continue a sync |
| GET | /sync/status | sync progress |
| GET | /services | scored, categorized sender list |
| PUT | /ai/preferences | enable/disable AI with per-account consent |
| POST | /services/{domain}/ai-classification | ask AI about an eligible ambiguous sender |
| DELETE | /services/{domain}/ai-classification | dismiss the AI opinion |
| GET | /services/{domain}/emails | 10 most recent emails from a sender |
| GET | /icon/{domain} | cached sender icon |
| GET | /decisions | saved Keep/Unsubscribe decisions |
| PUT | /decisions/{domain} | keep or reset a sender (`{"decision": "keep"\|null}`) |
| GET | /unsubscribe/{domain} | unsubscribe methods the sender offers |
| POST | /unsubscribe/{domain} | run a confirmed unsubscribe (`{"method": "one_click"\|"mailto"\|"gmail"\|"block"}`) |
| POST | /unsubscribe/{domain}/window | show or cancel its open Gmail window (`{"action": "show"\|"cancel"}`) |
| POST | /unsubscribe/{domain}/email/draft | draft an unsubscribe email (sender with no unsubscribe option) |
| POST | /unsubscribe/{domain}/email/send | send the checked draft from the user's Gmail |
| POST | /unsubscribe/{domain}/answer | record whether a manual unsubscribe worked |
| POST | /open-in-app | open a link in a Simplispace window |
| GET | /insights | health score, history, emails and time saved |

In the desktop app, everything except the page itself, `/health`, `/start/…` and `/auth/callback` needs the
session cookie that only the app's own window gets, so other programs and Windows users can't use the API.
Writes must also carry the `X-Simplispace: 1` header, and only `localhost`/`127.0.0.1` Host headers are
accepted, so other websites can't drive it.
