from collections import Counter
import html
import json
import secrets
import threading
import time
import urllib.parse
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, Response
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from httplib2 import ServerNotFoundError
from google.auth.exceptions import TransportError
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import gmail, icons, scoring, security, sync, unsubscribe
from ..db import SessionLocal, get_db
from ..models import Decision, HealthSnapshot, Message, User
from .auth import active_user

# Sender domains as stored by sync: lowercase host names. Anything else is rejected (422).
Domain = Annotated[str, Path(max_length=253, pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")]

router = APIRouter(tags=["services"])

# ponytail: in-memory single-job progress. Fine for a single-user desktop app;
# a real multi-user backend would key this per user + persist it.
_sync = {
    "running": False,
    "phase": "idle",
    "total": 0,
    "done": 0,
    "added": 0,
    "fetched": 0,
    "page": 0,
    "page_size": sync.PAGE_SIZE,
    "has_more": False,
    "error": None,
    "first_page_done": False,
}
_thread: threading.Thread | None = None


def cancel_sync():
    """Stop the background sync and wait for it, so it can't write after a disconnect."""
    _sync["running"] = False
    _sync["phase"] = "idle"
    # ponytail: a batch stuck in Gmail rate-limit retries can outlast this timeout.
    if _thread and _thread.is_alive():
        _thread.join(timeout=60)


def _incremental(db: Session, user_id: int) -> bool:
    """Fetch only changes since the last complete sync. False means a full sync is needed."""
    user = db.get(User, user_id)
    if not user or not user.history_id or user.sync_page_token:
        return False
    try:
        result = sync.sync_incremental(db, user, _sync, should_continue=lambda: _sync["running"])
    except sync.HistoryExpired:
        db.rollback()
        # Gmail can't say what was deleted meanwhile, so compare against what's there now;
        # the full sync that follows then fetches only mail that's new.
        _sync.update(phase="checking")
        sync.prune_missing(db, user)
        user.history_id = None
        db.commit()
        sync.bump_data_version()
        return False
    _sync.update(added=result["added"], has_more=False, first_page_done=True)
    return True


def _worker(user_id: int, page_token: str | None, page: int):
    db = SessionLocal()
    try:
        total_fetched = 0
        total_added = 0
        if page == 1 and _incremental(db, user_id):
            return
        while True:
            if not _sync["running"]:
                break
            user = db.get(User, user_id)
            if not user:
                break

            # Only report granular listing/fetching progress to _sync for the initial page.
            # Subsequent pages continue in background so the progress bar only shows once.
            prog_tracker = _sync if page == 1 else None
            if page > 1:
                _sync["phase"] = "background"

            result = sync.sync_user(
                db,
                user,
                prog_tracker,
                page_token=page_token,
                scan_sent=False,  # done right after the first page, off the critical path
                should_continue=lambda: _sync["running"],
                page_size=sync.FIRST_PAGE_SIZE if page == 1 else sync.PAGE_SIZE,
            )

            if not _sync["running"] or not db.get(User, user_id):
                break

            total_fetched += result["fetched"]
            total_added += result["added"]
            user.sync_page = page
            user.sync_page_token = result["next_page_token"]
            db.commit()
            page_token = result["next_page_token"]
            _sync.update(
                fetched=total_fetched,
                added=total_added,
                page=page,
                has_more=bool(page_token),
                first_page_done=True,
            )
            if page == 1:
                # Scores are already on screen; now refine them with sent mail.
                _sync["phase"] = "background"
                sync.scan_sent_mail(user, should_continue=lambda: _sync["running"])
                db.commit()
                sync.bump_data_version()
            if not page_token:
                break
            page += 1
            _sync["phase"] = "background"
    except sync.Cancelled:
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = str(e)
        if "invalid_grant" in msg or "expired or revoked" in msg:
            # Token is dead. Keep the account and its data; only the token goes, and the
            # UI offers "Reconnect". Signing in again with the same Gmail restores sync.
            user = db.get(User, user_id)
            if user:
                user.encrypted_token = ""
                db.commit()
            _sync.update(phase="error", error="Google ended Simplispace’s Gmail access. Reconnect to keep syncing.")
        elif isinstance(e, (OSError, TimeoutError, ServerNotFoundError, TransportError)):
            _sync.update(
                phase="error",
                offline=True,
                error="Couldn’t reach Gmail. Check your internet connection; syncing resumes when you’re back online.",
            )
        else:
            _sync.update(phase="error", error=msg)
    finally:
        _sync["running"] = False
        db.close()


def _require_user(db: Session) -> User:
    user = active_user(db)
    if not user:
        raise HTTPException(400, "No connected Gmail account.")
    return user


@router.post("/sync")
def run_sync(next_page: bool = False, db: Session = Depends(get_db)):
    global _thread
    if _sync["running"] or (_thread and _thread.is_alive()):
        raise HTTPException(409, "Sync already running.")
    user = _require_user(db)
    if not user.encrypted_token:
        raise HTTPException(401, "Reconnect your Gmail account to sync.")
    if next_page and not user.sync_page_token:
        raise HTTPException(400, "There are no more message pages to fetch.")
    if next_page:
        page = user.sync_page + 1
        page_token = user.sync_page_token
    else:
        page = 1
        page_token = None
        user.sync_page = 0
        user.sync_page_token = None
        db.commit()
    _sync.update(
        running=True,
        phase="starting",
        total=0,
        done=0,
        added=0,
        fetched=0,
        page=page,
        page_size=sync.PAGE_SIZE,
        has_more=bool(user.sync_page_token) if next_page else True,
        error=None,
        offline=False,
        first_page_done=False,
    )
    _thread = threading.Thread(target=_worker, args=(user.id, page_token, page), daemon=True)
    _thread.start()
    return {"started": True}


@router.get("/sync/status")
def sync_status(db: Session = Depends(get_db)):
    if not _sync["running"]:
        user = active_user(db)
        if user:
            _sync["page"] = user.sync_page
            _sync["has_more"] = bool(user.sync_page_token)
    return _sync


_SCORE_COLUMNS = (
    Message.sender_email,
    Message.sender_domain,
    Message.subject,
    Message.date,
    Message.labels,
    Message.list_unsubscribe,
    Message.is_unread,
)
# user id -> (data version, rows, scored). Scores only change when a sync writes,
# so /services and /insights reuse one computation between syncs.
# ponytail: single-user cache, no lock; key per user properly if this ever serves many.
_score_cache: dict[int, tuple[int, list, list]] = {}


def _load_scored(db: Session, user: User):
    version = sync.data_version()
    cached = _score_cache.get(user.id)
    if cached and cached[0] == version:
        return cached[1], cached[2]
    # Plain row tuples, not ORM objects, so the cache never holds a closed session's state.
    rows = db.query(*_SCORE_COLUMNS).filter(Message.user_id == user.id).all()
    replied = set(json.loads(user.replied_domains or "[]"))
    scored = scoring.score_all(rows, replied)
    _score_cache.clear()
    _score_cache[user.id] = (version, rows, scored)
    return rows, scored


class OpenIn(BaseModel):
    url: str


@router.post("/open-in-app")
def open_in_app(body: OpenIn):
    """Open a web link (e.g. Google account permissions) in a Simplispace window.

    `opened` is False outside the desktop app; the page then opens it itself. Only
    reachable from the app's own page (the X-Simplispace header check in main.py).
    """
    if urllib.parse.urlsplit(body.url).scheme != "https":
        raise HTTPException(400, "Only https links can be opened.")
    if not unsubscribe.in_app_opener:
        return {"opened": False}
    unsubscribe.open_external(body.url)
    return {"opened": True}


@router.get("/icon/{domain}")
def sender_icon(domain: Domain):
    found = icons.get_icon(domain)
    if not found:
        raise HTTPException(404, "No icon.")
    body, kind = found
    return Response(body, media_type=kind, headers={"Cache-Control": "max-age=604800"})


@router.get("/services")
def list_services(db: Session = Depends(get_db)):
    user = _require_user(db)
    _, scored = _load_scored(db, user)
    return {
        "count": len(scored),
        "services": [
            {
                "domain": s.domain,
                "name": s.name,
                "sender_email": s.sender_email,
                "email_count": s.email_count,
                "open_rate": s.open_rate,
                "days_since_last": s.days_since_last,
                "has_unsubscribe": s.has_unsubscribe,
                "score": s.score,
                "category": s.category,
                "facts": s.facts,
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


@router.get("/services/{domain}/emails")
def sender_emails(domain: Domain, db: Session = Depends(get_db)):
    """The sender's newest emails (subject and date only), to preview before deciding."""
    user = _require_user(db)
    rows = (
        db.query(Message.subject, Message.date, Message.is_unread)
        .filter(Message.user_id == user.id, Message.sender_domain == domain)
        .order_by(Message.date.desc())
        .limit(10)
        .all()
    )
    return [
        {"subject": s or "(no subject)", "date": f"{d.isoformat()}Z" if d else None, "unread": bool(u)}
        for s, d, u in rows
    ]


# --- Keep / Unsubscribe decisions --------------------------------------------

# Methods finished outside the app. Records made before the app asked about these
# have no page_result; treat them as unanswered rather than as done.
_FINISHED_OUTSIDE = {"gmail", "block", "mailto", "browser", "link"}


def _page_result(d: Decision) -> str | None:
    if d.page_result is None and d.unsubscribed_via in _FINISHED_OUTSIDE:
        return "unclear"
    return d.page_result


def _decision_json(
    d: Decision, db: Session | None = None, user: User | None = None, since: int | None = None
) -> dict:
    out = {
        "decision": d.decision,
        "unsubscribed_via": d.unsubscribed_via,
        "unsubscribed_at": f"{d.unsubscribed_at.isoformat()}Z" if d.unsubscribed_at else None,
    }
    if d.unsubscribed_at:
        # Verified from the mailbox: did the sender keep emailing after the grace period?
        if since is None and db is not None and user is not None:
            since = (
                db.query(Message)
                .filter(
                    Message.user_id == user.id,
                    Message.sender_domain == d.domain,
                    Message.date > d.unsubscribed_at + unsubscribe.GRACE,
                )
                .count()
            )
        since = since or 0
        out["emails_since"] = since
        out["status"] = unsubscribe.verification(
            d.unsubscribed_at, since, user.last_synced_at if user else None, _page_result(d)
        )
        out["confirm_after"] = f"{(d.unsubscribed_at + unsubscribe.CONFIRM_AFTER).isoformat()}Z"
    return out


def _get_decision(db: Session, user: User, domain: str) -> Decision | None:
    return db.query(Decision).filter(Decision.user_id == user.id, Decision.domain == domain).first()


@router.get("/decisions")
def list_decisions(db: Session = Depends(get_db)):
    user = _require_user(db)
    rows = db.query(Decision).filter(Decision.user_id == user.id).all()
    # One query for all unsubscribed senders instead of one count per sender.
    unsub = {d.domain: d.unsubscribed_at + unsubscribe.GRACE for d in rows if d.unsubscribed_at}
    since = Counter()
    if unsub:
        for domain, date in db.query(Message.sender_domain, Message.date).filter(
            Message.user_id == user.id, Message.sender_domain.in_(unsub), Message.date > min(unsub.values())
        ):
            if date and date > unsub[domain]:
                since[domain] += 1
    return {d.domain: _decision_json(d, db, user, since[d.domain]) for d in rows}


class DecisionIn(BaseModel):
    # Unsubscribing goes through POST /unsubscribe; this only keeps or resets a sender.
    decision: Literal["keep"] | None


@router.put("/decisions/{domain}")
def set_decision(domain: Domain, body: DecisionIn, db: Session = Depends(get_db)):
    user = _require_user(db)
    row = _get_decision(db, user, domain)
    # Clearing is always allowed: link/mailto unsubscribes can't be verified, so the
    # user may need to reset one they didn't finish.
    if row and row.unsubscribed_at and body.decision is not None:
        raise HTTPException(409, "Already unsubscribed from this sender.")
    if body.decision is None:
        if row:
            db.delete(row)
            db.commit()
        return None
    if not row:
        row = Decision(user_id=user.id, domain=domain, decision=body.decision)
        db.add(row)
    row.decision = body.decision
    row.updated_at = datetime.utcnow()
    db.commit()
    return _decision_json(row, db, user)


# --- Confirmation-gated unsubscribe -----------------------------------------

def _unsubscribe_options(db: Session, user: User, domain: str) -> dict:
    """Read the unsubscribe headers fresh from the newest message that has them."""
    msg = (
        db.query(Message)
        .filter(
            Message.user_id == user.id,
            Message.sender_domain == domain,
            Message.list_unsubscribe != "",
        )
        .order_by(Message.date.desc())
        .first()
    )
    if not msg:
        return unsubscribe.parse("")
    if not user.encrypted_token:  # needs reconnect: use what was stored at sync time
        return unsubscribe.parse(msg.list_unsubscribe)
    try:
        creds = gmail.credentials_from_json(security.decrypt(user.encrypted_token))
        full = (
            gmail.build_gmail(creds)
            .users()
            .messages()
            .get(
                userId="me",
                id=msg.gmail_id,
                format="metadata",
                metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post"],
            )
            .execute()
        )
        headers = sync._headers(full)
        return unsubscribe.parse(
            headers.get("list-unsubscribe", msg.list_unsubscribe),
            headers.get("list-unsubscribe-post", ""),
        )
    except (HttpError, GoogleAuthError, OSError):
        # Gmail unreachable / message gone: use the stored header (no one-click info).
        return unsubscribe.parse(msg.list_unsubscribe)


def _gmail_url(db: Session, user: User, domain: str) -> str | None:
    """Gmail web link to the newest email from this sender; Gmail shows its own
    Unsubscribe button there and sends the request from Google's servers."""
    msg = (
        db.query(Message.gmail_id)
        .filter(Message.user_id == user.id, Message.sender_domain == domain)
        .order_by(Message.date.desc())
        .first()
    )
    if not msg:
        return None
    return f"https://mail.google.com/mail/?authuser={urllib.parse.quote(user.email)}#all/{msg.gmail_id}"


@router.get("/unsubscribe/{domain}")
def unsubscribe_options(domain: Domain, db: Session = Depends(get_db)):
    user = _require_user(db)
    return {**_unsubscribe_options(db, user, domain), "gmail": bool(_gmail_url(db, user, domain))}


# One-time links for the browser hand-off page, so another website can't send the
# user's browser here to unsubscribe them without the confirmation dialog.
# ponytail: in-memory, single process; expires after 10 minutes.
_handoffs: dict[str, tuple[str, float]] = {}


def _handoff_url(request: Request, domain: str) -> str:
    now = time.time()
    for token, (_, expires) in list(_handoffs.items()):
        if expires < now:
            _handoffs.pop(token, None)
    token = secrets.token_urlsafe(24)
    _handoffs[token] = (domain, now + 600)
    return f"{str(request.base_url).rstrip('/')}/unsubscribe-handoff/{token}"


@router.get("/unsubscribe-handoff/{token}", response_class=HTMLResponse)
def unsubscribe_handoff(token: str, db: Session = Depends(get_db)):
    """Page opened in the user's own browser that sends the RFC 8058 one-click POST.

    For senders that refuse the app's direct request: the same POST, sent by the
    user's real browser after they confirmed in the app.
    """
    domain, expires = _handoffs.pop(token, (None, 0))
    if not domain or expires < time.time():
        raise HTTPException(404, "This unsubscribe link has expired. Start again from Simplispace.")
    user = _require_user(db)
    options = _unsubscribe_options(db, user, domain)
    if not options["link"]:
        raise HTTPException(404, "This sender has no unsubscribe link.")
    gmail_url = _gmail_url(db, user, domain)
    esc = html.escape
    nonce = secrets.token_urlsafe(16)
    gmail_hint = (
        f'<p class="hint">Didn’t work? <a href="{esc(gmail_url)}">Open the email in Gmail</a> '
        "and click Unsubscribe next to the sender’s name.</p>"
        if gmail_url else ""
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Unsubscribing…</title>
<style>
body{{font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:520px;margin:60px auto;padding:0 16px;color:#1c1c1e;background:#f2f2f7}}
@media (prefers-color-scheme:dark){{body{{color:#f2f2f7;background:#000}}a{{color:#64a8ff}}}}
h1{{font-size:22px;margin:0 0 8px}} button{{font:inherit;font-weight:600;color:#fff;background:#007aff;border:0;border-radius:12px;padding:12px 20px;cursor:pointer}}
.hint{{opacity:.75;font-size:14px}}
</style></head><body>
<h1>Unsubscribing from {esc(domain)}…</h1>
<p>Your browser is sending the sender’s standard unsubscribe request. If nothing happens in a moment, press the button.</p>
<form method="post" action="{esc(options['link'])}">
<input type="hidden" name="List-Unsubscribe" value="One-Click">
<button type="submit">Unsubscribe</button>
</form>
{gmail_hint}
<script nonce="{nonce}">setTimeout(function(){{document.forms[0].submit()}},600)</script>
</body></html>"""
    # Only this page's own script may run, and its form may only post to the web.
    csp = (
        f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
        "form-action https: http:; base-uri 'none'; frame-ancestors 'none'"
    )
    return HTMLResponse(page, headers={"Content-Security-Policy": csp})


class UnsubscribeIn(BaseModel):
    method: Literal["one_click", "link", "mailto", "gmail", "block"]
    # Bulk unsubscribe: if a sender refuses the one-click request, report it instead of
    # opening a window for it, so ticking 20 senders can't open 20 windows.
    no_fallback: bool = False


@router.post("/unsubscribe/{domain}")
def confirm_unsubscribe(domain: Domain, body: UnsubscribeIn, request: Request, db: Session = Depends(get_db)):
    user = _require_user(db)
    # The confirmation is the user picking a method in the dialog; this call is that choice.
    row = _get_decision(db, user, domain)
    if row and row.unsubscribed_at:
        raise HTTPException(409, "Already unsubscribed from this sender.")

    # Targets are re-read server-side; the client never supplies the URL.
    options = _unsubscribe_options(db, user, domain)
    via = body.method
    fallback_reason = None
    page_url = None  # an unsubscribe web page to show the user
    gmail_url = None  # Gmail (an email to unsubscribe/block from, or a draft) to show
    try:
        if body.method == "one_click":
            if not options["one_click"]:
                raise ValueError("This sender does not support one-click unsubscribe.")
            try:
                unsubscribe.one_click(options["link"])
            except ValueError as e:
                if body.no_fallback:
                    raise
                # Some senders refuse automated requests (bot protection, HTTP 403).
                # Send the same POST from a real browser window instead.
                page_url, fallback_reason = _handoff_url(request, domain), str(e)
        elif body.method == "link":
            if not options["link"]:
                raise ValueError("This sender did not provide that unsubscribe option.")
            # One-click URLs often only accept POST; a plain visit (GET) fails with
            # "POST required", so open the page that submits the POST instead.
            page_url = _handoff_url(request, domain) if options["one_click"] else options["link"]
        elif body.method in ("gmail", "block"):
            gmail_url = _gmail_url(db, user, domain)
            if not gmail_url:
                raise ValueError("No email from this sender to open in Gmail.")
        else:
            if not options["mailto"]:
                raise ValueError("This sender did not provide that unsubscribe option.")
            # Inside the app the draft opens in Gmail; outside it, the user's mail program.
            gmail_url = unsubscribe.gmail_compose_url(options["mailto"], user.email)
        in_app = bool((page_url or gmail_url) and unsubscribe.in_app_opener)
        if page_url and not in_app:
            unsubscribe.open_external(page_url)  # dev mode in a normal browser
            via = "browser"
        elif gmail_url and not in_app:
            unsubscribe.open_external(options["mailto"] if body.method == "mailto" else gmail_url)
    except ValueError as e:
        raise HTTPException(502, str(e)) from e

    if not row:
        row = Decision(user_id=user.id, domain=domain)
        db.add(row)
    row.decision = "unsubscribe"
    row.unsubscribed_via = "in_app" if in_app and page_url else via
    # In-app pages are read by the app. Anything finished outside it (Gmail, a mail
    # draft, the browser) can't be seen, so the app asks the user instead of assuming.
    row.page_result = "in_progress" if in_app else (None if via == "one_click" else "unclear")
    row.unsubscribed_at = datetime.utcnow()
    row.updated_at = row.unsubscribed_at
    db.commit()
    if in_app:
        _open_in_app(page_url or gmail_url, user.id, domain, gmail=bool(gmail_url))
    return {**_decision_json(row, db, user), "fallback_reason": fallback_reason}


def _set_page_result(user_id: int, domain: str, result: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(Decision).filter(Decision.user_id == user_id, Decision.domain == domain).first()
        if row and row.unsubscribed_at:  # skip if the user undid it meanwhile
            row.page_result = result
            db.commit()
    finally:
        db.close()


def _open_in_app(url: str, user_id: int, domain: str, gmail: bool = False) -> None:
    """Show the unsubscribe page (or Gmail) in a Simplispace window and read the result."""
    last = {"result": None}

    def on_page(page_url: str, text: str) -> bool:
        if gmail:
            if unsubscribe.gmail_done(text):
                last["result"] = "success"
                _set_page_result(user_id, domain, "success")
                return True
            return False
        if page_url.startswith(("http://127.0.0.1", "http://localhost")):
            return False  # our own hand-off page, before it posts to the sender
        result = unsubscribe.classify_page(text)
        if result:
            last["result"] = result
        if result == "success":
            _set_page_result(user_id, domain, "success")
            return True  # done: close the window shortly
        return False  # failed pages can be bot checks that pass on reload; keep watching

    def on_closed() -> None:
        if last["result"] != "success":
            _set_page_result(user_id, domain, "failed" if last["result"] == "failed" else "unclear")

    title = f"Gmail – {domain}" if gmail else f"Unsubscribe from {domain}"
    script = unsubscribe.GMAIL_NOTICES if gmail else unsubscribe.PAGE_TEXT
    unsubscribe.in_app_opener(url, title, on_page, on_closed, script)


class AnswerIn(BaseModel):
    worked: bool


@router.post("/unsubscribe/{domain}/answer")
def answer_unsubscribe(domain: Domain, body: AnswerIn, db: Session = Depends(get_db)):
    """The user's own answer when the sender's page didn't say clearly."""
    user = _require_user(db)
    row = _get_decision(db, user, domain)
    if not row or not row.unsubscribed_at:
        raise HTTPException(404, "No unsubscribe recorded for this sender.")
    if body.worked:
        row.page_result = "success"
        db.commit()
        return _decision_json(row, db, user)
    db.delete(row)  # didn't work: back to how it was, so another method can be tried
    db.commit()
    return None


# --- Inbox health score + savings -------------------------------------------

@router.get("/insights")
def insights(db: Session = Depends(get_db)):
    user = _require_user(db)
    messages, scored = _load_scored(db, user)
    if not scored:
        return {"health_score": None, "history": []}

    decisions = db.query(Decision).filter(Decision.user_id == user.id).all()
    result = scoring.inbox_insights(
        scored,
        scoring.monthly_rates(messages),
        {d.domain: d.decision for d in decisions},
        {d.domain for d in decisions if d.unsubscribed_at},
    )

    # One snapshot per day; the latest computation of the day wins.
    today = datetime.utcnow().date().isoformat()
    snap = db.query(HealthSnapshot).filter_by(user_id=user.id, day=today).first()
    if snap:
        snap.score = result["health_score"]
    else:
        db.add(HealthSnapshot(user_id=user.id, day=today, score=result["health_score"]))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # a concurrent request wrote today's snapshot first

    history = (
        db.query(HealthSnapshot)
        .filter_by(user_id=user.id)
        .order_by(HealthSnapshot.day.desc())
        .limit(90)
        .all()
    )
    result["history"] = [{"day": s.day, "score": s.score} for s in reversed(history)]
    return result
