"""Only the app's own window may use the local API.

Anything on this computer can connect to localhost, including other programs and other
Windows users, so each launch has its own random key. The window gets it as a cookie by
opening a one-time address that only the desktop shell knows. Off in development, where
the API runs on its own.
"""
import hmac
import secrets
from typing import Callable

COOKIE = "simplispace_session"
_key: str | None = None
_starts: set[str] = set()
on_signed_out: Callable[[], None] | None = None  # the desktop shell clears its Google sign-in here


def new_start_path() -> str:
    """Turn the check on and return a one-time address that signs a window in to the API."""
    global _key
    _key = _key or secrets.token_urlsafe(32)
    code = secrets.token_urlsafe(32)
    _starts.add(code)
    return f"/start/{code}"


def redeem(code: str) -> str | None:
    if code in _starts:
        _starts.discard(code)
        return _key
    return None


def allowed(cookie: str | None) -> bool:
    return _key is None or (cookie is not None and hmac.compare_digest(cookie.encode(), _key.encode()))


def signed_out() -> None:
    if on_signed_out:
        on_signed_out()
