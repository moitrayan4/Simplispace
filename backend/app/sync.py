import json
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from . import gmail, security
from .config import settings
from .models import Message, User

MAX_MESSAGES = 3000
# 50 gets * 5 units = 250 units/batch, under Gmail's per-user per-second burst.
BATCH_SIZE = 50
BATCH_PAUSE = 0.3  # seconds between batches, smooths the per-minute quota
_META_HEADERS = ["From", "Subject", "Date", "List-Unsubscribe"]


def _is_rate_limit(exc) -> bool:
    return (
        isinstance(exc, HttpError)
        and getattr(exc, "resp", None) is not None
        and exc.resp.status in (403, 429)
        and ("rateLimit" in str(exc) or "quota" in str(exc).lower())
    )


def _with_retry(fn, tries=6):
    """Exponential backoff on Gmail rate-limit errors."""
    for i in range(tries):
        try:
            return fn()
        except HttpError as e:
            if _is_rate_limit(e) and i < tries - 1:
                time.sleep(2 ** i)  # 1, 2, 4, 8, 16s
                continue
            raise
    raise RuntimeError("Gmail rate limit: retries exhausted")


def _domain(addr: str) -> str:
    _, email_addr = parseaddr(addr)
    return email_addr.split("@")[-1].lower() if "@" in email_addr else ""


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _parse_date(value: str | None, internal_ms: str | None) -> datetime | None:
    if value:
        try:
            return parsedate_to_datetime(value).astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            pass
    if internal_ms:
        return datetime.utcfromtimestamp(int(internal_ms) / 1000)
    return None


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _list_ids(service, query: str, cap: int) -> list[str]:
    ids: list[str] = []
    page = None
    while len(ids) < cap:
        resp = _with_retry(
            lambda: service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page, maxResults=500)
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page = resp.get("nextPageToken")
        if not page:
            break
    return ids[:cap]


def _batch_fetch(service, ids: list[str], headers: list[str], on_msg, progress: dict | None):
    """Fetch metadata via batched HTTP requests; re-queues rate-limited items."""
    pending = list(ids)
    backoff = 1
    while pending:
        chunk, pending = pending[:BATCH_SIZE], pending[BATCH_SIZE:]
        failed: list[str] = []

        def cb(mid, resp, exc):
            if exc is None and resp is not None:
                on_msg(resp)
                if progress is not None:
                    progress["done"] = progress.get("done", 0) + 1
            elif _is_rate_limit(exc):
                failed.append(mid)          # retry later
            elif progress is not None:
                progress["done"] = progress.get("done", 0) + 1  # skip, count as done

        batch = service.new_batch_http_request(callback=cb)
        for mid in chunk:
            batch.add(
                service.users().messages().get(
                    userId="me", id=mid, format="metadata", metadataHeaders=headers
                ),
                request_id=mid,
            )
        _with_retry(batch.execute)

        if failed:
            pending = failed + pending
            time.sleep(backoff)
            backoff = min(backoff * 2, 16)
        else:
            backoff = 1
            time.sleep(BATCH_PAUSE)


def sync_user(db: Session, user: User, progress: dict | None = None) -> dict:
    """Fetch message metadata for the last sync_months. Batched + progress-aware."""
    prog = progress if progress is not None else {}
    creds = gmail.credentials_from_json(security.decrypt(user.encrypted_token))
    service = gmail.build_gmail(creds)

    since_dt = datetime.utcnow() - timedelta(days=30 * settings.sync_months)
    since = since_dt.strftime("%Y/%m/%d")

    prog.update(phase="listing", total=0, done=0)
    existing = {m.gmail_id for m in db.query(Message.gmail_id).filter(Message.user_id == user.id)}
    ids = _list_ids(service, f"after:{since} -in:sent -in:chats", cap=MAX_MESSAGES)
    todo = [i for i in ids if i not in existing]

    prog.update(phase="fetching", total=len(todo), done=0)
    rows: list[Message] = []

    def on_msg(msg: dict):
        h = _headers(msg)
        labels = msg.get("labelIds", [])
        rows.append(
            Message(
                user_id=user.id,
                gmail_id=msg["id"],
                sender_email=parseaddr(h.get("from", ""))[1].lower(),
                sender_domain=_domain(h.get("from", "")),
                subject=h.get("subject", ""),
                date=_parse_date(h.get("date"), msg.get("internalDate")),
                labels=",".join(labels),
                snippet=msg.get("snippet", ""),
                list_unsubscribe=h.get("list-unsubscribe", ""),
                is_unread="UNREAD" in labels,
            )
        )

    _batch_fetch(service, todo, _META_HEADERS, on_msg, prog)
    db.add_all(rows)

    prog.update(phase="scanning_sent")
    sent_ids = _list_ids(service, f"in:sent after:{since}", cap=1000)
    domains: set[str] = set()

    def on_sent(msg: dict):
        h = _headers(msg)
        for field in ("to", "cc"):
            for part in h.get(field, "").split(","):
                d = _domain(part)
                if d:
                    domains.add(d)

    _batch_fetch(service, sent_ids, ["To", "Cc"], on_sent, None)

    user.replied_domains = json.dumps(sorted(domains))
    user.last_synced_at = datetime.utcnow()
    db.commit()

    prog.update(phase="done", added=len(rows))
    return {"fetched": len(ids), "added": len(rows), "total": len(existing) + len(rows)}
