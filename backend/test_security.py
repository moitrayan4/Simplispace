"""Self-check for the app's defences.  Run from backend/:  python test_security.py"""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="simpli-test-"))
os.environ.update(DATABASE_URL=f"sqlite:///{(_TMP / 't.db').as_posix()}", FRONTEND_DIST=str(_TMP / "dist"))
(_TMP / "dist" / "assets").mkdir(parents=True)
(_TMP / "dist" / "index.html").write_text("app")
(_TMP / "dist" / "logo.png").write_text("x")

import asyncio  # noqa: E402
import urllib.request  # noqa: E402

from app import icons, main, session  # noqa: E402


def call(method, path, headers=None):
    """One request through every check the real server applies."""
    sent = []

    async def go():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m):
            sent.append(m)

        path_, _, query = path.partition("?")
        raw = [(b"host", b"localhost:8000")] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        await main.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
                        "path": path_, "raw_path": path_.encode(), "query_string": query.encode(), "headers": raw,
                        "client": ("127.0.0.1", 1), "server": ("localhost", 8000), "root_path": ""}, receive, send)

    asyncio.run(go())
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}


def test_only_the_apps_own_window_can_use_the_api():
    path = session.new_start_path()
    for method, p in (("GET", "/auth/status"), ("GET", "/services"), ("GET", "/auth/login"), ("GET", "/icon/x.com"), ("GET", "/sync/status"),
                      ("POST", "/auth/logout"), ("POST", "/auth/delete-account"), ("PUT", "/decisions/x.com"), ("POST", "/unsubscribe/x.com/email/send"),
                      ("DELETE", "/services/x.com/ai-classification"), ("GET", "/no-such"), ("POST", "/logo.png"), ("GET", "//auth/status"), ("GET", "/assets/../auth/status")):
        status, _ = call(method, p, {"X-Simplispace": "1"})
        assert status in (401, 404), (method, p, status)
        assert call(method, p, {"X-Simplispace": "1", "Cookie": f"{session.COOKIE}=guess"})[0] in (401, 404), p
    for p in ("/", "/logo.png", "/health"):  # the interface itself holds no data
        assert call("GET", p)[0] == 200, p
    assert call("GET", "/auth/callback?state=x&code=y")[0] == 400  # guarded by its OAuth state instead

    status, headers = call("GET", path)
    cookie = headers["set-cookie"]
    assert status == 303 and "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    good = {"Cookie": cookie.split(";")[0]}
    assert call("GET", "/auth/status", good)[0] == 200
    assert "set-cookie" not in call("GET", path)[1]  # one use only
    assert "set-cookie" not in call("GET", "/start/guess")[1]
    assert call("POST", "/auth/logout", good)[0] == 403  # the cross-site header is still needed
    assert call("GET", "/auth/status", {**good, "Host": "evil.com"})[0] in (200, 400)


def test_junk_addresses_dont_use_memory():
    main._hits.clear()
    session._key = None
    for i in range(200):
        call("POST", f"/junk{i}/x", {"X-Simplispace": "1"})
    assert len(main._hits) == 1


def test_icons_never_follow_a_redirect_inside():
    handler, request = icons._PublicRedirects(), urllib.request.Request("https://shop.com/favicon.ico")
    go = lambda url: handler.redirect_request(request, None, 302, "", {}, url)  # noqa: E731
    for bad in ("http://shop.com/i.png", "https://127.0.0.1/x", "https://192.168.0.1/reboot", "https://localhost:8000/", "https://[::1]/", "ftp://x/", "file:///c:/x"):
        assert go(bad) is None, bad
    assert go("https://8.8.8.8/i.png") is not None


def test_deleted_account_leaves_nothing_in_the_file():
    from app.db import SessionLocal, engine
    from app.models import Message, User
    from app.routers import auth

    calls, auth._revoke, session.on_signed_out = [], lambda _u: None, lambda: calls.append(1)
    icons.CACHE_DIR = _TMP / "icons"
    icons.CACHE_DIR.mkdir()
    (icons.CACHE_DIR / "secret-sender.com.v2.img").write_text("x")
    db = SessionLocal()
    user = User(email="me@x.com", encrypted_token="")
    db.add(user)
    db.flush()
    db.add_all([Message(user_id=user.id, gmail_id=f"g{i}", sender_domain="b.com", subject=f"SECRETSUBJECT{i}") for i in range(2000)])
    db.commit()
    auth.delete_account(db)
    db.close()
    engine.dispose()
    assert not any(b"SECRETSUBJECT" in f.read_bytes() for f in _TMP.glob("t.db*"))
    assert not icons.CACHE_DIR.exists() and calls == [1]  # and the window signs out of Google


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all security checks passed")
