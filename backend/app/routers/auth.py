import shutil
import time
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import ai, gmail, icons, security, session, sync
from ..config import settings
from ..db import compact, get_db
from ..models import AIClassification, Decision, HealthSnapshot, Message, UnsubAttempt, User

router = APIRouter(prefix="/auth", tags=["auth"])

# ponytail: in-memory state -> PKCE verifier store. Single-process desktop app,
# so a dict is enough; move to a shared store only if the API goes multi-worker.
_pending_states: dict[str, tuple[str, float]] = {}  # state -> (verifier, expires)
_SIGN_IN_TIME = 15 * 60


def active_user(db: Session) -> User | None:
    # ponytail: single active account (one person's desktop inbox).
    return db.query(User).filter(User.signed_in.is_(True)).order_by(User.created_at.desc()).first()


def _revoke(user: User) -> None:
    """Tell Google to end Simplispace's access. Best effort: offline, it just doesn't happen."""
    try:
        creds = gmail.credentials_from_json(security.decrypt(user.encrypted_token))
        # Revoking the refresh token ends the whole grant (the access token may be expired).
        data = urllib.parse.urlencode({"token": creds.refresh_token or creds.token}).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/revoke",
            data=data,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def purge_user(db: Session, user: User) -> None:
    """Delete the account and every row stored for it."""
    gmail.forget_id_token(user.email)
    for model in (Message, Decision, HealthSnapshot, AIClassification, UnsubAttempt):
        db.query(model).filter(model.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    sync.bump_data_version()  # drop cached scores


@router.get("/login")
def login(send: bool = False, db: Session = Depends(get_db)):
    """Start sign-in. `send` also asks to send email, for the account already signed in."""
    if not settings.google_client_id:
        raise HTTPException(500, "Google OAuth not configured (see backend/.env.example).")
    user = active_user(db) if send else None
    url, state, code_verifier = gmail.authorization_url(send=bool(user), email=user.email if user else None)
    while len(_pending_states) >= 20:  # abandoned sign-ins: drop the oldest
        _pending_states.pop(next(iter(_pending_states)))
    _pending_states[state] = (code_verifier, time.time() + _SIGN_IN_TIME)
    return RedirectResponse(url)


@router.get("/callback")
def callback(
    state: str = Query(..., max_length=200),
    code: str | None = Query(None, max_length=2000),
    error: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
):
    # state is the CSRF check for sign-in: it must be one this app just issued.
    code_verifier, expires = _pending_states.get(state, ("", 0.0))
    if expires < time.time():
        _pending_states.pop(state, None)
        raise HTTPException(400, "Invalid OAuth state.")
    if error or not code:  # e.g. the user pressed Cancel on Google's consent screen
        _pending_states.pop(state, None)
        return RedirectResponse(f"{settings.frontend_origin}/")
    _pending_states.pop(state)

    try:
        creds = gmail.exchange_code(code, state, code_verifier)
        email = gmail.user_email(creds)
        enc = security.encrypt(gmail.credentials_to_json(creds))
    except gmail.GmailNotAllowed:
        return RedirectResponse(f"{settings.frontend_origin}/?login_error=gmail")
    except Exception as e:
        # Back to the app with the kind of error only: messages can contain sign-in details.
        return RedirectResponse(f"{settings.frontend_origin}/?login_error={type(e).__name__}")

    # One account signed in at a time. Signing back in to an account that logged out
    # brings its senders and choices straight back.
    db.query(User).filter(User.email != email).update({User.signed_in: False})
    user = db.query(User).filter(User.email == email).first()
    if user:
        user.encrypted_token = enc
        user.signed_in = True
    else:
        db.add(User(email=email, encrypted_token=enc, signed_in=True))
    db.commit()
    sync.bump_data_version()
    return RedirectResponse(f"{settings.frontend_origin}/?connected=1")


def _can_send(user: User) -> bool:
    try:
        return gmail.can_send(gmail.credentials_from_json(security.decrypt(user.encrypted_token)))
    except Exception:  # no usable saved sign-in
        return False


@router.get("/status")
def status(db: Session = Depends(get_db)):
    user = active_user(db)
    if not user:
        return {"connected": False}
    # Database timestamps are stored as naive UTC for SQLite compatibility.
    # Mark the value explicitly so clients convert it to their local timezone.
    last_synced_at = (
        f"{user.last_synced_at.isoformat()}Z" if user.last_synced_at else None
    )
    return {
        "connected": True,
        # Data is still here, but Google's token was revoked or expired.
        "needs_reconnect": not user.encrypted_token,
        "email": user.email,
        "can_send": _can_send(user),
        **ai.account_fields(user),
        "last_synced_at": last_synced_at,
        # True until the first pass through the whole inbox has finished.
        "indexing": not user.history_id,
    }


@router.post("/logout")
def logout(db: Session = Depends(get_db)):
    """Sign out: forget the Gmail sign-in, keep this account's data for next time."""
    from .services import cancel_sync, close_windows
    cancel_sync()
    close_windows()
    user = active_user(db)
    if user:
        _revoke(user)
        gmail.forget_id_token(user.email)
        user.encrypted_token = ""
        user.signed_in = False
        db.commit()
        sync.bump_data_version()
    session.signed_out()
    return {"logged_out": True}


@router.post("/delete-account")
def delete_account(db: Session = Depends(get_db)):
    """Delete the account: end Gmail access and erase everything stored for it."""
    from .services import cancel_sync, close_windows
    cancel_sync()
    close_windows()
    user = active_user(db)
    if user:
        _revoke(user)  # best effort; local deletion below is the guarantee
        purge_user(db, user)
        compact(db)  # so nothing is left in the file's free space
        shutil.rmtree(icons.CACHE_DIR, ignore_errors=True)  # their names are the user's senders
    session.signed_out()
    return {"deleted": True}
