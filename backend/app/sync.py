import json
import time
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from . import gmail, security
from .config import settings
from .models import Message, User

# Gmail allows ~50 metadata reads a second per account (measured ~42/s here), so a
# first sync can't be made much faster, only shown sooner: the newest few hundred
# emails are scored first (~6 s), then older pages fill in behind the scores.
FIRST_PAGE_SIZE = 250
# Later pages are the same size: a few seconds slower over a whole mailbox (more page
# overhead), but the scores on screen refresh about every 45 s instead of every 3 min.
PAGE_SIZE = 250
SENT_SCAN = 500  # newest sent emails checked for "you've written to them"
# Steady pacing beats bursts. Gmail's real per-account limit varies by project (it was
# well under the documented 50 reads/s here), and bursting past it makes Gmail refuse
# requests, after which backoff wastes more time than it saves (measured ~5 reads/s).
# So the pace adapts like TCP: halve on a rate-limit error, creep back up when clean.
BATCH_SIZE = 25
MAX_RATE = 40  # metadata reads per second, upper bound
MIN_RATE = 3
_META_HEADERS = ["From", "Subject", "Date", "List-Unsubscribe"]


class Cancelled(Exception):
    """Raised when a sync is stopped (e.g. on disconnect) so nothing more is written."""


def _is_rate_limit(exc) -> bool:
    return (
        isinstance(exc, HttpError)
        and getattr(exc, "resp", None) is not None
        and exc.resp.status in (403, 429)
        and ("rateLimit" in str(exc) or "quota" in str(exc).lower())
    )


def _is_transient(exc) -> bool:
    """Worth retrying: rate limits, Google 5xx, and dropped/reset network connections."""
    if isinstance(exc, HttpError):
        status = getattr(getattr(exc, "resp", None), "status", 0)
        return _is_rate_limit(exc) or status >= 500
    # ConnectionResetError (WinError 10054), timeouts, SSL drops are all OSError subclasses.
    return isinstance(exc, OSError)


def _with_retry(fn, tries=6):
    """Exponential backoff on rate limits and transient network/server errors."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if _is_transient(e) and i < tries - 1:
                time.sleep(2 ** i)  # 1, 2, 4, 8, 16s
                continue
            raise
    raise RuntimeError("Gmail: retries exhausted")


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


def _list_ids(service, query: str, cap: int, page_token: str | None = None) -> tuple[list[str], str | None]:
    ids: list[str] = []
    page = page_token
    while len(ids) < cap:
        resp = _with_retry(
            lambda: service.users()
            .messages()
            # Never list past the cap: the next page token must start right after it.
            .list(userId="me", q=query, pageToken=page, maxResults=min(500, cap - len(ids)))
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page = resp.get("nextPageToken")
        if not page:
            break
    return ids[:cap], page


def _batch_fetch(
    service,
    ids: list[str],
    headers: list[str],
    on_msg,
    progress: dict | None,
    format_name: str = "metadata",
    should_continue=lambda: True,
):
    """Fetch metadata via batched HTTP requests; re-queues rate-limited items."""
    pending = list(ids)
    rate = MAX_RATE
    while pending:
        if not should_continue():
            raise Cancelled()
        chunk, pending = pending[:BATCH_SIZE], pending[BATCH_SIZE:]
        failed: list[str] = []

        def cb(mid, resp, exc):
            if exc is None and resp is not None:
                on_msg(resp)
                if progress is not None:
                    progress["done"] = progress.get("done", 0) + 1
            elif _is_transient(exc):
                failed.append(mid)          # retry later
            elif progress is not None:
                progress["done"] = progress.get("done", 0) + 1  # skip, count as done

        started = time.monotonic()
        batch = service.new_batch_http_request(callback=cb)
        for mid in chunk:
            request_args = {"userId": "me", "id": mid, "format": format_name}
            if format_name == "metadata":
                request_args["metadataHeaders"] = headers
            batch.add(service.users().messages().get(**request_args), request_id=mid)
        _with_retry(batch.execute)

        if failed:
            # Over the per-minute budget: refused requests still cost quota, so back off
            # properly and come back slower.
            pending = failed + pending
            rate = max(MIN_RATE, rate / 2)
            time.sleep(5)
        else:
            rate = min(MAX_RATE, rate + 0.5)
        # A batch of n reads may not take less than n / rate seconds.
        time.sleep(max(0.0, len(chunk) / rate - (time.monotonic() - started)))


class HistoryExpired(Exception):
    """Gmail no longer has history that far back; a full sync is needed."""


# Bumped whenever stored messages change, so cached scores know to recompute.
_data_version = 0


def data_version() -> int:
    return _data_version


def bump_data_version() -> None:
    global _data_version
    _data_version += 1


# The full sync lists with "-in:sent -in:chats" and Gmail skips spam/trash by default;
# the incremental sync applies the same rule using labels.
_SKIP_LABELS = {"SENT", "CHAT", "DRAFT", "SPAM", "TRASH"}
_SQL_CHUNK = 500  # stay under SQLite's bound-parameter limit for IN (...)


def _service_for(user: User):
    return gmail.build_gmail(gmail.credentials_from_json(security.decrypt(user.encrypted_token)))


def _current_history_id(service) -> str:
    return str(_with_retry(lambda: service.users().getProfile(userId="me").execute())["historyId"])


def _to_row(user_id: int, msg: dict) -> Message:
    h = _headers(msg)
    labels = msg.get("labelIds", [])
    return Message(
        user_id=user_id,
        gmail_id=msg["id"],
        sender_email=parseaddr(h.get("from", ""))[1].lower(),
        sender_domain=_domain(h.get("from", "")),
        subject=h.get("subject", ""),
        date=_parse_date(h.get("date"), msg.get("internalDate")),
        labels=",".join(labels),
        list_unsubscribe=h.get("list-unsubscribe", ""),
        is_unread="UNREAD" in labels,
    )


def _recipient_domains(service, ids: list[str], should_continue) -> set[str]:
    domains: set[str] = set()

    def on_sent(msg: dict):
        h = _headers(msg)
        for field in ("to", "cc"):
            for part in h.get(field, "").split(","):
                d = _domain(part)
                if d:
                    domains.add(d)

    _batch_fetch(service, ids, ["To", "Cc"], on_sent, None, should_continue=should_continue)
    return domains


def scan_sent_mail(user: User, service=None, should_continue=lambda: True) -> None:
    """Remember which domains the user has written to (a strong "keep" signal)."""
    service = service or _service_for(user)
    sent_ids, _ = _list_ids(service, "in:sent", cap=SENT_SCAN)
    user.replied_domains = json.dumps(sorted(_recipient_domains(service, sent_ids, should_continue)))


def _full_query() -> str:
    # Whole mailbox by default; SYNC_MONTHS > 0 limits it to the last N months.
    return "-in:sent -in:chats" + (f" newer_than:{settings.sync_months}m" if settings.sync_months else "")


def prune_missing(db: Session, user: User, service=None) -> int:
    """Drop stored mail that is no longer in Gmail (deleted, trashed, spammed).

    For when Gmail's change history has expired, so deletions can't be read from it.
    Listing ids only is cheap (500 per call), so this compares against the whole mailbox.
    """
    service = service or _service_for(user)
    in_gmail = set(_list_ids(service, _full_query(), cap=10**9)[0])
    stored = [m.gmail_id for m in db.query(Message.gmail_id).filter(Message.user_id == user.id)]
    gone = [mid for mid in stored if mid not in in_gmail]
    for i in range(0, len(gone), _SQL_CHUNK):
        db.query(Message).filter(
            Message.user_id == user.id, Message.gmail_id.in_(gone[i : i + _SQL_CHUNK])
        ).delete(synchronize_session=False)
    return len(gone)


def sync_user(
    db: Session,
    user: User,
    progress: dict | None = None,
    page_token: str | None = None,
    scan_sent: bool = True,
    should_continue=lambda: True,
    page_size: int = PAGE_SIZE,
) -> dict:
    """Full sync: fetch one page of message metadata. Batched + progress-aware."""
    prog = progress if progress is not None else {}
    service = _service_for(user)

    prog.update(phase="listing", total=0, done=0)
    if page_token is None:
        # Anything that changes after this point is picked up by the next incremental sync.
        user.history_start = _current_history_id(service)
    existing = {m.gmail_id for m in db.query(Message.gmail_id).filter(Message.user_id == user.id)}
    ids, next_page_token = _list_ids(service, _full_query(), cap=page_size, page_token=page_token)
    todo = [i for i in ids if i not in existing]

    prog.update(phase="fetching", total=len(todo), done=0)
    rows: list[Message] = []
    _batch_fetch(
        service, todo, _META_HEADERS, lambda msg: rows.append(_to_row(user.id, msg)), prog,
        should_continue=should_continue,
    )
    if not should_continue():
        raise Cancelled()
    db.add_all(rows)
    db.flush()

    if scan_sent:
        prog.update(phase="scanning_sent")
        scan_sent_mail(user, service, should_continue)

    if not should_continue():
        raise Cancelled()  # flushed rows roll back with the session
    if not next_page_token:
        user.history_id = user.history_start  # whole mailbox indexed
    user.last_synced_at = datetime.utcnow()
    db.commit()
    bump_data_version()

    prog.update(phase="done", added=len(rows))
    return {
        "fetched": len(ids),
        "added": len(rows),
        "total": len(existing) + len(rows),
        "next_page_token": next_page_token,
    }


def sync_incremental(
    db: Session,
    user: User,
    progress: dict | None = None,
    should_continue=lambda: True,
) -> dict:
    """Apply only what changed since user.history_id: new, deleted and re-labelled mail.

    Raises HistoryExpired when Gmail has dropped that history (about a week old or
    more); the caller then runs a full sync, which skips mail already stored.
    """
    prog = progress if progress is not None else {}
    service = _service_for(user)
    prog.update(phase="checking", total=0, done=0)

    added: dict[str, list[str]] = {}     # new message id -> labels
    relabelled: dict[str, list[str]] = {}  # message id -> current labels
    deleted: set[str] = set()
    latest = user.history_id
    page = None
    while True:
        try:
            resp = _with_retry(
                lambda: service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=user.history_id,
                    pageToken=page,
                    maxResults=500,
                    historyTypes=["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
                )
                .execute()
            )
        except HttpError as e:
            if e.resp.status == 404:
                raise HistoryExpired() from e
            raise
        # Records come oldest first, so later entries overwrite earlier state.
        for record in resp.get("history", []):
            for item in record.get("messagesAdded", []):
                msg = item["message"]
                added[msg["id"]] = msg.get("labelIds", [])
                deleted.discard(msg["id"])
            for key in ("labelsAdded", "labelsRemoved"):
                for item in record.get(key, []):
                    msg = item["message"]
                    relabelled[msg["id"]] = msg.get("labelIds", [])
            for item in record.get("messagesDeleted", []):
                mid = item["message"]["id"]
                deleted.add(mid)
                added.pop(mid, None)
                relabelled.pop(mid, None)
        latest = resp.get("historyId", latest)
        page = resp.get("nextPageToken")
        if not page or not should_continue():
            break
    if not should_continue():
        raise Cancelled()

    for mid, labels in relabelled.items():
        if mid in added:
            added[mid] = labels
        elif {"SPAM", "TRASH"} & set(labels):
            deleted.add(mid)  # moved to spam or trash: drop it, like the full sync would

    new_inbox = [mid for mid, labels in added.items() if not _SKIP_LABELS & set(labels)]
    new_sent = [mid for mid, labels in added.items() if "SENT" in labels]
    existing = {
        m.gmail_id
        for chunk in _chunks(new_inbox, _SQL_CHUNK)
        for m in db.query(Message.gmail_id).filter(Message.user_id == user.id, Message.gmail_id.in_(chunk))
    }
    todo = [mid for mid in new_inbox if mid not in existing]

    prog.update(phase="fetching", total=len(todo), done=0)
    rows: list[Message] = []
    _batch_fetch(
        service, todo, _META_HEADERS, lambda msg: rows.append(_to_row(user.id, msg)), prog,
        should_continue=should_continue,
    )
    if not should_continue():
        raise Cancelled()
    db.add_all(rows)

    # Opened/unread and Promotions changes update the stored labels, so scores follow.
    updated = 0
    for chunk in _chunks([m for m in relabelled if m not in added and m not in deleted], _SQL_CHUNK):
        for m in db.query(Message).filter(Message.user_id == user.id, Message.gmail_id.in_(chunk)):
            labels = relabelled[m.gmail_id]
            m.labels = ",".join(labels)
            m.is_unread = "UNREAD" in labels
            updated += 1

    removed = 0
    for chunk in _chunks(list(deleted), _SQL_CHUNK):
        removed += (
            db.query(Message)
            .filter(Message.user_id == user.id, Message.gmail_id.in_(chunk))
            .delete(synchronize_session=False)
        )

    if new_sent:
        prog.update(phase="scanning_sent")
        domains = set(json.loads(user.replied_domains or "[]"))
        domains |= _recipient_domains(service, new_sent, should_continue)
        user.replied_domains = json.dumps(sorted(domains))

    if not should_continue():
        raise Cancelled()
    user.history_id = latest
    user.last_synced_at = datetime.utcnow()
    db.commit()
    bump_data_version()

    prog.update(phase="done", added=len(rows))
    return {"added": len(rows), "updated": updated, "removed": removed}
