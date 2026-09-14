# Simplispace 

Windows desktop app that connects a Gmail inbox (read-only), groups mail by
sending service, and scores each service with a transparent rule-based engine.
Scope built: **weeks 1–6** of the proposal (OAuth, metadata sync + grouping,
rule-based scoring + categories). No unsubscribe, no AI, no health score yet.

## Stack

- **Backend**: FastAPI (JSON API) — `backend/`
- **Frontend**: React + Vite — `frontend/`
- **Desktop shell**: PyWebView + PyInstaller — `desktop/`
- **DB**: SQLite (SQLAlchemy)
- **venv**: `mail/`

## One-time setup

1. **Google Cloud Console**: create a project, enable the **Gmail API**, create
   an **OAuth 2.0 Client ID** (type: *Web application*), and add
   `http://localhost:8000/auth/callback` to its authorized redirect URIs.
   Add your Gmail address as a test user on the OAuth consent screen.
2. Copy the client id/secret into `backend/.env` (`GOOGLE_CLIENT_ID`,
   `GOOGLE_CLIENT_SECRET`). A `FERNET_KEY` is already generated there.

Dependencies are already installed in `mail/` and `frontend/node_modules/`.

## Run (dev)

Two terminals:

```
# 1. backend
mail\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000   # run from backend/

# 2. frontend
cd frontend && npm run dev                                             # http://localhost:5173
```

Open http://localhost:5173 → **Connect Gmail** → **Sync now** → scored table.

## Run as desktop app

```
cd frontend && npm run build           # produce frontend/dist
mail\Scripts\python.exe desktop\main.py
```

`desktop/main.py` starts the API in-process and opens a native window on the
built frontend. OAuth consent runs inside the window (WebView2 with a normal
browser user-agent), redirecting back to the same app on completion.

## Build the .exe (PyInstaller)

```
cd frontend && npm run build          # bundle the frontend first
cd ..
mail\Scripts\python.exe -m PyInstaller --noconfirm --clean --name Simplispace ^
  --windowed --onefile --paths backend ^
  --add-data "frontend/dist;frontend/dist" ^
  --collect-all webview --collect-all uvicorn ^
  --collect-all googleapiclient --collect-all google_auth_oauthlib ^
  --collect-submodules app --collect-submodules google ^
  --collect-submodules pythonnet --hidden-import clr ^
  desktop/main.py
```

Output: `dist/Simplispace.exe`. **Place `.env` (with Google creds + FERNET_KEY)
in the same folder as the exe** — it is read from beside the exe, and
`simplispace.db` is created there on first run. Requires the Microsoft WebView2
runtime (preinstalled on Windows 11).

## Tests

```
cd backend && ..\mail\Scripts\python.exe test_scoring.py
```

## API

| Method | Path              | Purpose                                   |
|--------|-------------------|-------------------------------------------|
| GET    | /health           | liveness                                  |
| GET    | /auth/login       | start Google OAuth                        |
| GET    | /auth/callback    | OAuth redirect target                     |
| GET    | /auth/status      | connected account + last sync             |
| POST   | /auth/disconnect  | revoke token, delete stored data          |
| POST   | /sync             | fetch + store metadata (last SYNC_MONTHS) |
| GET    | /services         | scored, categorized service list          |

## Scoring (proposal 7.1)

Base 50, then: promotional −30, List-Unsubscribe −20, replied +40,
transactional keywords +35, opened frequently +25, 50+ never-opened −40.
Buckets: ≥70 Important, 40–69 Useful, 20–39 Optional, <20 Marketing/Inactive.
Every service returns the exact signals that moved its score (explainable).
Knobs live at the top of `backend/app/scoring.py` — calibrate against pilot data.

## Not built (weeks 7–12, out of scope)

Per-service cards with Keep/Unsubscribe, AI tier for ambiguous senders,
confirmation-gated unsubscribe flow, inbox health score, security hardening.
