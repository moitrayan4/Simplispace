import urllib.parse
import urllib.request
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ai, gmail, security, sync
from ..config import settings
from ..db import SessionLocal, get_db
from ..models import AIClassification, Decision, HealthSnapshot, Message, User

router = APIRouter(prefix="/auth", tags=["auth"])

# Sign-in runs as a single background task in this single-process desktop app.
# The installed-app OAuth flow owns its state and PKCE verifier through the callback.
_login_lock = threading.Lock()
_login_status = {"running": False, "error": None}


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
    for model in (Message, Decision, HealthSnapshot, AIClassification):
        db.query(model).filter(model.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    sync.bump_data_version()  # drop cached scores


def _desktop_login_worker() -> None:
    db = SessionLocal()
    try:
        creds = gmail.authorize_desktop()
        email = gmail.user_email(creds)
        encrypted = security.encrypt(gmail.credentials_to_json(creds))

        db.query(User).filter(User.email != email).update({User.signed_in: False})
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.encrypted_token = encrypted
            user.signed_in = True
        else:
            db.add(User(email=email, encrypted_token=encrypted, signed_in=True))
        db.commit()
        sync.bump_data_version()
    except Exception as error:
        db.rollback()
        with _login_lock:
            _login_status["error"] = str(error) or "Google sign-in did not complete."
    finally:
        db.close()
        with _login_lock:
            _login_status["running"] = False


@router.post("/login")
def login():
    if not settings.google_client_id:
        raise HTTPException(500, "Google OAuth not configured (see backend/.env.example).")
    with _login_lock:
        if not _login_status["running"]:
            _login_status["running"] = True
            _login_status["error"] = None
            threading.Thread(target=_desktop_login_worker, daemon=True).start()
    return {"started": True}


@router.get("/status")
def status(db: Session = Depends(get_db)):
    user = active_user(db)
    with _login_lock:
        login_state = {
            "login_in_progress": _login_status["running"],
            "login_error": _login_status["error"],
        }
    if not user:
        return {"connected": False, **login_state}
    # Database timestamps are stored as naive UTC for SQLite compatibility.
    # Mark the value explicitly so clients convert it to their local timezone.
    last_synced_at = (
        f"{user.last_synced_at.isoformat()}Z" if user.last_synced_at else None
    )
    return {
        "connected": True,
        **login_state,
        # Data is still here, but Google's token was revoked or expired.
        "needs_reconnect": not user.encrypted_token,
        "email": user.email,
        "ai_enabled": bool(user.ai_enabled),
        "ai_available": ai.provider_ready(),
        "ai_setup_hint": ai.setup_message(),
        "ai_provider": ai.provider_label(),
        "ai_model": settings.ai_model.strip() if ai.provider_ready() else None,
        "last_synced_at": last_synced_at,
        # True until the first pass through the whole inbox has finished.
        "indexing": not user.history_id,
    }


@router.post("/logout")
def logout(db: Session = Depends(get_db)):
    """Sign out: forget the Gmail sign-in, keep this account's data for next time."""
    from .services import cancel_sync
    cancel_sync()
    user = active_user(db)
    if user:
        _revoke(user)
        user.encrypted_token = ""
        user.signed_in = False
        db.commit()
        sync.bump_data_version()
    return {"logged_out": True}


@router.post("/delete-account")
def delete_account(db: Session = Depends(get_db)):
    """Delete the account: end Gmail access and erase everything stored for it."""
    from .services import cancel_sync
    cancel_sync()
    user = active_user(db)
    if user:
        _revoke(user)  # best effort; local deletion below is the guarantee
        purge_user(db, user)
    return {"deleted": True}
