"""Simplispace desktop app (dev + PyInstaller-frozen).

Runs the FastAPI backend in-process (it also serves the built frontend) and shows
it in a native window. Google sign-in runs in the main window; unsubscribe pages,
Gmail and other links open in Simplispace windows (see _open_in_app).

Dev:     python desktop/main.py      (after: cd frontend && npm run build)
Frozen:  Simplispace.exe             (data in %LOCALAPPDATA%\\Simplispace; no .env needed)
"""
import os
import sys
import threading
import time
import socket
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


def _prepare_user_data(app_dir: Path, bundle: Path) -> None:
    """Per-user data folder and settings for the packaged app.

    Each Windows user gets %LOCALAPPDATA%\\Simplispace with their own database,
    sign-in and encryption key, so the same .exe works for anyone, from anywhere. Nothing
    is read from the folder the exe is in: in Downloads, anyone could have put it there.
    """
    from dotenv import dotenv_values

    app_dir.mkdir(parents=True, exist_ok=True)
    env_file = app_dir / ".env"
    local = dotenv_values(env_file) if env_file.exists() else {}
    os.environ["FERNET_KEY"] = _sign_in_key(env_file, local, app_dir / "simplispace.db")
    # The app's Google OAuth client ships inside the exe (Google does not treat an
    # installed app's client secret as confidential). A local .env can override it.
    for key, value in {**dotenv_values(bundle / "oauth.env"), **dotenv_values(env_file)}.items():
        if value:
            os.environ.setdefault(key, value)


if FROZEN:
    BUNDLE = Path(sys._MEIPASS)              # bundled read-only files
    APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Simplispace"
    _prepare_user_data(APP_DIR, BUNDLE)
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
from app import session, unsubscribe as unsubscribe_actions  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

# Read into settings by now; the browser processes the window starts needn't inherit them.
for _name in ("FERNET_KEY", "GOOGLE_CLIENT_SECRET", "AI_API_KEY"):
    os.environ.pop(_name, None)

# Same host name Google's sign-in returns to, so the page, its saved settings and the
# API are always one origin.
API = "http://localhost:8000"

_action_windows = set()  # windows whose popups must stay inside Simplispace


def _ignore_page_messages():
    """Pages can't call into the app. The window library lets any page it shows call Python by
    name, and answers errors with file paths; Simplispace never uses that, so drop it all."""
    from webview.platforms.edgechromium import EdgeChrome

    EdgeChrome.on_script_notify = lambda self, sender, args: None
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False


def _keep_popups_in_app():
    """Links that open a new tab (Gmail's "Go to website") load in the same Simplispace
    window, so the app can still see the result. The main window is left alone."""
    from webview.platforms.edgechromium import EdgeChrome

    original = EdgeChrome.on_new_window_request

    def on_new_window_request(self, sender, args):
        uri = str(args.get_Uri())
        if self.pywebview_window in _action_windows and uri.lower().startswith(("https://", "http://")):
            args.set_Handled(True)
            self.load_url(uri)
        else:
            original(self, sender, args)

    EdgeChrome.on_new_window_request = on_new_window_request


class _ActionWindow:
    """What the API may do with an open Gmail window."""

    def __init__(self, win):
        self._win = win

    @property
    def closed(self) -> bool:
        return self._win.events.closed.is_set()

    def close(self):
        if not self.closed:
            self._win.destroy()

    def show(self):
        self._win.restore()
        self._win.show()


def _open_in_app(url, title, on_page, on_closed, script="document.body ? document.body.innerText : ''"):
    """Open a page (an unsubscribe page, or Gmail) in a Simplispace window and report
    what it shows, so the app can acknowledge the result itself.

    The app's own JavaScript can't read another site's page, but the window's host
    can. `script` picks what to read (the whole page, or just Gmail's notices). It is
    re-read twice a second, which also catches pages that update without reloading.
    """
    win = webview.create_window(title, url, width=980, height=760)
    _action_windows.add(win)
    closing, once = threading.Event(), threading.Lock()

    def read(out):
        try:
            url, text = win.get_current_url(), win.evaluate_js(script)
            # The page decides what this returns, so take nothing but plain text.
            out.append((url or "", text[:50_000] if isinstance(text, str) else ""))
        except Exception:
            pass  # still opening, or mid-navigation: the next read gets it

    def watch():
        while not closing.wait(0.5):  # often enough for short notices
            # In its own thread: a read caught by a page change can wait forever.
            out = []
            reader = threading.Thread(target=read, args=(out,), daemon=True)
            reader.start()
            reader.join(3)
            try:
                done = bool(out) and on_page(*out[0])
            except Exception:
                continue  # couldn't save it just now: the notice is still there, so try again
            if done:
                closing.wait(2.5)  # let the user see the confirmation
                if not closing.is_set():
                    win.destroy()
                return

    def closed():
        closing.set()
        if once.acquire(blocking=False):
            _action_windows.discard(win)
            on_closed()

    win.events.closed += closed
    if win.events.closed.is_set():  # closed before we could listen
        closed()
    # Started here, not on the "shown" event: the window may already be on screen, and a
    # listener added too late is never called.
    threading.Thread(target=watch, daemon=True).start()
    return _ActionWindow(win)


def _listen() -> list[socket.socket]:
    """Take port 8000 for this app alone, on both addresses "localhost" can mean. Otherwise
    another program could answer in our place and the window would show its page."""
    taken = []
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((address, 8000))
            sock.listen(128)
            taken.append(sock)
        except OSError as e:
            if sock:
                sock.close()
            # 10047/10049: this computer has no IPv6, so "localhost" can't mean it.
            if family == socket.AF_INET6 and e.winerror in (10047, 10049):
                continue
            for other in taken:
                other.close()
            return []
    return taken


def _start_api(timeout=20) -> bool:
    sockets = _listen()
    if not sockets:
        return False
    server = uvicorn.Server(uvicorn.Config(fastapi_app, log_level="warning"))
    threading.Thread(target=server.run, kwargs={"sockets": sockets}, daemon=True).start()
    for _ in range(timeout * 10):
        if server.started:  # our own server, not just something that answers
            return True
        time.sleep(0.1)
    return False


def main():
    if not (DIST / "index.html").exists():
        sys.exit(f"Frontend build missing at {DIST}")

    unsubscribe_actions.in_app_opener = _open_in_app
    _ignore_page_messages()
    _keep_popups_in_app()
    if not _start_api():
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
    window = webview.create_window("Simplispace", API + session.new_start_path(), width=1120, height=780, maximized=True)

    def close_others():
        # Closing Simplispace closes everything: a leftover Gmail window would keep the app
        # running invisibly, and opening it again would show nothing.
        for win in list(_action_windows):
            try:
                win.destroy()
            except Exception:
                pass

    window.events.closed += close_others

    def forget_google():
        # Signing out of Simplispace must also sign out of Google in this window, or the next
        # person could just press "Continue with Google". Then reopen the app.
        time.sleep(1)  # let the page show that it worked
        try:
            window.clear_cookies()
        finally:
            window.load_url(API + session.new_start_path())

    session.on_signed_out = lambda: threading.Thread(target=forget_google, daemon=True).start()
    webview.start(private_mode=False, storage_path=str(APP_DIR / ".webview"))


if __name__ == "__main__":
    main()
