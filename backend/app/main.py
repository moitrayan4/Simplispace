import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from . import session
from .config import settings
from .db import Base, engine, ensure_schema
from .routers import auth, services

# Overridable so the packaged (PyInstaller) app can point at its bundled dist.
DIST = Path(
    os.environ.get("FRONTEND_DIST")
    or Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
)

Base.metadata.create_all(bind=engine)
ensure_schema()

# No interactive API docs or schema in the shipped app: nothing for a caller to explore.
app = FastAPI(title="Simplispace API", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,  # the session cookie is for the app's own origin only
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Simplispace"],
)


# Cross-site protection for the local API. Any web page can fire a plain POST at
# localhost, but it cannot add a custom header without a CORS preflight, which
# only the app's own origin passes. Host check blocks DNS-rebinding reads.
@app.middleware("http")
async def require_app_header(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-simplispace") != "1":
        return JSONResponse({"detail": "Missing X-Simplispace header."}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def cache_static(request: Request, call_next):
    response = await call_next(request)
    # Vite puts a content hash in every /assets file name, so they never change.
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


# The app's own pages load scripts only from itself; nothing can frame them.
# Pages that need something else (the unsubscribe hand-off) set their own policy.
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://www.google.com https://t1.gstatic.com https://icons.duckduckgo.com; "
    "connect-src 'self' http://localhost:8000 https://clients3.google.com; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",  # other websites can't even load an icon
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# Rate limit for anything that changes state or talks to Google/senders on the user's
# behalf: 60 requests a minute per kind of action. Reads (lists, icons, status) are free.
# ponytail: in-memory, single process; fine for one local app.
_RATE_LIMIT, _RATE_WINDOW = 60, 60.0
_hits: dict[str, deque] = defaultdict(deque)
_KINDS = {"auth", "sync", "decisions", "unsubscribe", "services", "ai", "open-in-app"}


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path = request.url.path
    if request.method not in ("GET", "HEAD", "OPTIONS") or path.startswith("/auth/"):
        kind = path.split("/")[1]
        key = f"{request.method} {kind}" if kind in _KINDS else "other"  # bounded: junk shares one
        now, hits = time.monotonic(), _hits[key]
        while hits and now - hits[0] > _RATE_WINDOW:
            hits.popleft()
        if len(hits) >= _RATE_LIMIT:
            return JSONResponse(
                {"detail": "Too many requests. Wait a minute and try again."},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        hits.append(now)
    return await call_next(request)


# Pages and files anyone may have: the interface itself holds no data.
_PUBLIC = {"/", "/health", "/auth/callback"}  # the callback is checked by its OAuth state


def _is_public(request: Request) -> bool:
    path = request.url.path
    if request.method == "OPTIONS" or path in _PUBLIC or path.startswith(("/assets/", "/start/")):
        return True
    name = path.lstrip("/")
    return request.method in ("GET", "HEAD") and "/" not in name and name in _ROOT_FILES


_ROOT_FILES = {f.name for f in DIST.iterdir() if f.is_file()} if DIST.exists() else set()


# Added last of the request checks, so it runs first: without the session nothing else is reached.
@app.middleware("http")
async def require_session(request: Request, call_next):
    if not _is_public(request) and not session.allowed(request.cookies.get(session.COOKIE)):
        return JSONResponse({"detail": "Open Simplispace to use this."}, status_code=401)
    return await call_next(request)


app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

app.include_router(auth.router)
app.include_router(services.router)


@app.get("/start/{code}")
def start(code: str):
    """The desktop window's first page: swaps a one-time code for the session cookie."""
    key = session.redeem(code)
    response = RedirectResponse("/", status_code=303)
    if key:
        response.set_cookie(session.COOKIE, key, httponly=True, samesite="strict", path="/")
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve the built frontend (desktop / single-origin mode). API routes above win;
# unknown paths fall through to the SPA. Absent in dev — use the Vite server then.
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
