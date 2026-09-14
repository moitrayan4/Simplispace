import json
import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import scoring, sync
from ..db import SessionLocal, get_db
from ..models import Message, User
from .auth import active_user

router = APIRouter(tags=["services"])

# ponytail: in-memory single-job progress. Fine for a single-user desktop app;
# a real multi-user backend would key this per user + persist it.
_sync = {"running": False, "phase": "idle", "total": 0, "done": 0, "added": 0, "error": None}


def _worker(user_id: int):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        sync.sync_user(db, user, _sync)
    except Exception as e:
        msg = str(e)
        if "invalid_grant" in msg or "expired or revoked" in msg:
            # Token is dead — drop it so the app returns to the login screen.
            user = db.get(User, user_id)
            if user:
                db.delete(user)
                db.commit()
            _sync.update(phase="error", error="Your Gmail session expired. Please connect again.")
        else:
            _sync.update(phase="error", error=msg)
    finally:
        _sync["running"] = False
        db.close()


@router.post("/sync")
def run_sync(db: Session = Depends(get_db)):
    if _sync["running"]:
        raise HTTPException(409, "Sync already running.")
    user = active_user(db)
    if not user:
        raise HTTPException(400, "No connected Gmail account.")
    _sync.update(running=True, phase="starting", total=0, done=0, added=0, error=None)
    threading.Thread(target=_worker, args=(user.id,), daemon=True).start()
    return {"started": True}


@router.get("/sync/status")
def sync_status():
    return _sync


@router.get("/services")
def list_services(db: Session = Depends(get_db)):
    user = active_user(db)
    if not user:
        raise HTTPException(400, "No connected Gmail account.")

    messages = db.query(Message).filter(Message.user_id == user.id).all()
    replied = set(json.loads(user.replied_domains or "[]"))
    scored = scoring.score_all(messages, replied)

    return {
        "count": len(scored),
        "services": [
            {
                "domain": s.domain,
                "name": s.name,
                "email_count": s.email_count,
                "open_rate": s.open_rate,
                "days_since_last": s.days_since_last,
                "has_unsubscribe": s.has_unsubscribe,
                "score": s.score,
                "category": s.category,
                "signals": [
                    {
                        "key": sig.label,
                        "label": scoring.SIGNAL_LABELS.get(sig.label, sig.label),
                        "points": sig.points,
                    }
                    for sig in s.signals
                ],
            }
            for s in scored
        ],
    }
