"""Simplispace desktop app (dev + PyInstaller-frozen).

Runs the FastAPI backend in-process (it also serves the built frontend) and shows
it in a native window. Everything happens inside the app: Google sign-in runs in the
main window, and unsubscribe pages, Gmail and other links open in Simplispace windows
(see _open_in_app), so the external browser is never needed.

Dev:     python desktop/main.py      (after: cd frontend && npm run build)
Frozen:  Simplispace.exe             (data in %LOCALAPPDATA%\\Simplispace; no .env needed)
"""
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)


def _single_instance() -> None:
    """One Simplispace at a time, like standard apps: opening it again just brings the
    open window to the front. (A second copy can't start its server, the port is taken,
    and its window would break as soon as the first copy closed.)"""
    import ctypes

    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    global _INSTANCE_LOCK  # held for the life of the process; Windows frees it on exit
    _INSTANCE_LOCK = kernel32.CreateMutexW(None, False, "Local\\SimplispaceSingleInstance")
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return
    hwnd = user32.FindWindowW(None, "Simplispace")
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE: un-minimize
        user32.SetForegroundWindow(hwnd)
    sys.exit(0)


_single_instance()

def _sign_in_key(env_file: Path, local: dict, db_path: Path) -> str:
    """The key that encrypts the Gmail sign-in token. Made on this computer, never
    shipped, and stored sealed by Windows (DPAPI) so only this Windows user can read it."""
    import base64
    import sqlite3

    from cryptography.fernet import Fernet

    import dpapi

    sealed = local.get("FERNET_KEY_DPAPI")
    if sealed:
        try:
            return dpapi.unprotect(base64.b64decode(sealed)).decode()
        except (OSError, ValueError):
            # Sealed by another Windows account: the saved sign-in can't be read here.
            # Forget only the token (senders and choices stay) so the app offers Reconnect.
            if db_path.exists():
                db = sqlite3.connect(db_path)
                try:
                    with db:
                        db.execute("UPDATE users SET encrypted_token = ''")
                except sqlite3.Error:
                    pass
                db.close()
            key = Fernet.generate_key().decode()
    else:
        # Older builds kept the key in plain text: seal it, then drop the plain copy.
        key = local.get("FERNET_KEY") or Fernet.generate_key().decode()

    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    lines = [line for line in lines if not line.startswith(("FERNET_KEY=", "FERNET_KEY_DPAPI="))]
    lines.append("FERNET_KEY_DPAPI=" + base64.b64encode(dpapi.protect(key.encode())).decode())
    tmp = env_file.with_name(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(env_file)  # atomic: never a moment with no key on disk
    return key


def _prepare_user_data(app_dir: Path, exe_dir: Path, bundle: Path) -> None:
    """Per-user data folder and settings for the packaged app.

    Each Windows user gets %LOCALAPPDATA%\\Simplispace with their own database,
    sign-in and encryption key, so the same .exe works for anyone, from anywhere.
    """
    import shutil

    from dotenv import dotenv_values

    app_dir.mkdir(parents=True, exist_ok=True)
    # Older builds kept everything beside the exe: carry it over once, so the
    # existing account, sign-in and encryption key keep working.
    for name in (".env", "icons", ".webview"):
        old, new = exe_dir / name, app_dir / name
        if old.exists() and not new.exists():
            (shutil.copytree if old.is_dir() else shutil.copy2)(old, new)
    old_db, new_db = exe_dir / "simplispace.db", app_dir / "simplispace.db"
    if old_db.exists() and not new_db.exists():
        # SQLite's backup API, not a file copy: recent changes can still be sitting in
        # the -wal journal beside the database, and a plain copy would lose them.
        import sqlite3

        src, dst = sqlite3.connect(old_db), sqlite3.connect(new_db)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()

    env_file = app_dir / ".env"
    local = dotenv_values(env_file) if env_file.exists() else {}
    os.environ["FERNET_KEY"] = _sign_in_key(env_file, local, new_db)
    # The app's Google OAuth client ships inside the exe (Google does not treat an
    # installed app's client secret as confidential). A local .env can override it.
    for key, value in {**dotenv_values(bundle / "oauth.env"), **dotenv_values(env_file)}.items():
        if value:
            os.environ.setdefault(key, value)


if FROZEN:
    BUNDLE = Path(sys._MEIPASS)              # bundled read-only files
    APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Simplispace"
    _prepare_user_data(APP_DIR, Path(sys.executable).parent, BUNDLE)
    os.chdir(APP_DIR)                        # database, icons and sign-in live here
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
from app import unsubscribe as unsubscribe_actions  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

# Same host name Google's sign-in returns to, so the page, its saved settings and the
# API are always one origin.
API = "http://localhost:8000"

# Plain desktop-Chrome UA so Google's consent page treats the WebView2 window as a
# normal browser (avoids the embedded-webview "disallowed_useragent" path).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _open_in_app(url, title, on_page, on_closed, script="document.body ? document.body.innerText : ''"):
    """Open a page (an unsubscribe page, or Gmail) in a Simplispace window and report
    what it shows, so the app can acknowledge the result itself.

    The app's own JavaScript can't read another site's page, but the window's host
    can. `script` picks what to read (the whole page, or just Gmail's notices). It is
    re-read every second, which also catches pages that update without reloading.
    """
    win = webview.create_window(title, url, width=980, height=760)
    state = {"open": True}

    def watch():
        while state["open"]:
            time.sleep(1)
            try:
                text = win.evaluate_js(script) or ""
                current = win.get_current_url() or ""
            except Exception:
                continue  # mid-navigation; closing is handled by the closed event
            if on_page(current, text):
                threading.Timer(2.5, win.destroy).start()  # let the user see the confirmation
                return

    def closed():
        state["open"] = False
        on_closed()

    win.events.shown += lambda: threading.Thread(target=watch, daemon=True).start()
    win.events.closed += closed


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

    unsubscribe_actions.in_app_opener = _open_in_app
    threading.Thread(target=_run_api, daemon=True).start()
    if not _wait_for_api():
        # No console in the packaged app, so say it in a normal Windows message box.
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "Simplispace couldn't start. Another program may be using port 8000.\n\n"
            "Close other programs or restart your computer, then open Simplispace again.",
            "Simplispace",
            0x10,  # MB_ICONERROR
        )
        sys.exit(1)

    # Persist the WebView2 session so the Google login survives restarts.
    webview.create_window("Simplispace", API, width=1120, height=780, maximized=True)
    webview.start(user_agent=USER_AGENT, private_mode=False, storage_path=str(APP_DIR / ".webview"))


if __name__ == "__main__":
    main()
