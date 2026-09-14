"""Simplispace desktop app (dev + PyInstaller-frozen).

Runs the FastAPI backend in-process (it also serves the built frontend) and shows
it in a native window. OAuth consent opens in the system browser — Google blocks
embedded webviews — and the window polls /auth/status until connected.

Dev:     python desktop/main.py      (after: cd frontend && npm run build)
Frozen:  Simplispace.exe             (.env must sit beside the exe)
"""
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    BUNDLE = Path(sys._MEIPASS)              # bundled read-only files
    APP_DIR = Path(sys.executable).parent    # .env + simplispace.db live here
    os.chdir(APP_DIR)
    sys.path.insert(0, str(BUNDLE))
    DIST = BUNDLE / "frontend" / "dist"
else:
    ROOT = Path(__file__).resolve().parent.parent
    APP_DIR = ROOT / "backend"
    os.chdir(APP_DIR)
    sys.path.insert(0, str(APP_DIR))
    DIST = ROOT / "frontend" / "dist"

# Single origin: frontend served by the API at :8000 so the OAuth callback
# lands back in the same app. Set before importing the app (config reads env).
os.environ["FRONTEND_ORIGIN"] = "http://localhost:8000"
os.environ["FRONTEND_DIST"] = str(DIST)

import uvicorn  # noqa: E402
import webview  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

API = "http://127.0.0.1:8000"

# Plain desktop-Chrome UA so Google's consent page treats the WebView2 window as a
# normal browser (avoids the embedded-webview "disallowed_useragent" path).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _run_api():
    uvicorn.run(fastapi_app, host="127.0.0.1", port=8000, log_level="warning")


def _wait_for_api(timeout=20):
    for _ in range(timeout * 2):
        try:
            urllib.request.urlopen(f"{API}/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    if not (DIST / "index.html").exists():
        sys.exit(f"Frontend build missing at {DIST}")

    threading.Thread(target=_run_api, daemon=True).start()
    if not _wait_for_api():
        sys.exit("Backend did not start on :8000")

    # Persist the WebView2 session so the Google login survives restarts.
    webview.create_window("Simplispace", API, width=1120, height=780)
    webview.start(user_agent=USER_AGENT, private_mode=False, storage_path=str(APP_DIR / ".webview"))


if __name__ == "__main__":
    main()
