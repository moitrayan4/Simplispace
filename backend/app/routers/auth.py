import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import gmail, security
from ..config import settings
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/auth", tags=["auth"])

# ponytail: in-memory state -> PKCE verifier store. Single-process desktop app,
# so a dict is enough; move to a shared store only if the API goes multi-worker.
_pending_states: dict[str, str] = {}


def active_user(db: Session) -> User | None:
    # ponytail: single active account (one person's desktop inbox). Latest wins.
    return db.query(User).order_by(User.created_at.desc()).first()


@router.get("/login")
def login():
    if not settings.google_client_id:
        raise HTTPException(500, "Google OAuth not configured (see backend/.env.example).")
    url, state, code_verifier = gmail.authorization_url()
    _pending_states[state] = code_verifier
    return RedirectResponse(url)


@router.get("/callback")
def callback(
    state: str = Query(...), code: str = Query(...), db: Session = Depends(get_db)
):
    if state not in _pending_states:
        raise HTTPException(400, "Invalid OAuth state.")
    code_verifier = _pending_states.pop(state)

    creds = gmail.exchange_code(code, state, code_verifier)
    email = gmail.user_email(creds)
    enc = security.encrypt(gmail.credentials_to_json(creds))

    user = db.query(User).filter(User.email == email).first()
    if user:
        user.encrypted_token = enc
    else:
        db.add(User(email=email, encrypted_token=enc))
    db.commit()
    return RedirectResponse(f"{settings.frontend_origin}/?connected=1")


@router.get("/status")
def status(db: Session = Depends(get_db)):
    user = active_user(db)
    if not user:
        return {"connected": False}
    return {
        "connected": True,
        "email": user.email,
        "last_synced_at": user.last_synced_at.isoformat() if user.last_synced_at else None,
    }


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    user = active_user(db)
    if not user:
        return {"disconnected": True}
    try:
        creds = gmail.credentials_from_json(security.decrypt(user.encrypted_token))
        data = urllib.parse.urlencode({"token": creds.token}).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/revoke",
            data=data,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # best-effort revoke; local deletion below is the guarantee
    db.delete(user)
    db.commit()
    return {"disconnected": True}
