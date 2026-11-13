"""Self-check for the incremental (History API) sync against a fake Gmail.
Run from backend/:  python test_sync.py
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import sync
from app.db import Base
from app.models import Message, User


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _Batch:
    def __init__(self, callback):
        self.callback, self.items = callback, []

    def add(self, request, request_id):
        self.items.append((request_id, request))

    def execute(self):
        for mid, request in self.items:
            self.callback(mid, request.execute(), None)


class FakeGmail:
    """Just enough of the Gmail client for sync_incremental."""

    def __init__(self, history, messages):
        self.history_pages, self.msgs = history, messages

    def users(self):
        return self

    def history(self):
        return self

    def messages(self):
        return self

    def list(self, pageToken=None, **_):
        return _Call(self.history_pages[int(pageToken or 0)])

    def get(self, id, **_):
        return _Call(self.msgs[id])

    def new_batch_http_request(self, callback):
        return _Batch(callback)


def _msg(mid, sender, labels, subject="Hi", to=""):
    headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    if to:
        headers.append({"name": "To", "value": to})
    return {"id": mid, "labelIds": labels, "internalDate": "1700000000000", "payload": {"headers": headers}}


def test_incremental():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(email="me@x.com", encrypted_token="", history_id="100", replied_domains='["old.com"]')
    db.add(user)
    db.flush()
    db.add_all([
        Message(user_id=user.id, gmail_id="read-me", sender_domain="a.com", labels="UNREAD,INBOX", is_unread=True),
        Message(user_id=user.id, gmail_id="gone", sender_domain="b.com"),
        Message(user_id=user.id, gmail_id="spammed", sender_domain="c.com"),
    ])
    db.commit()

    history = [
        {"history": [
            {"messagesAdded": [{"message": {"id": "new1", "labelIds": ["INBOX", "UNREAD"]}}]},
            {"messagesAdded": [{"message": {"id": "sent1", "labelIds": ["SENT"]}}]},
            {"messagesAdded": [{"message": {"id": "draft1", "labelIds": ["DRAFT"]}}]},
            {"labelsRemoved": [{"message": {"id": "read-me", "labelIds": ["INBOX"]}}]},
        ], "nextPageToken": "1", "historyId": "150"},
        {"history": [
            {"messagesDeleted": [{"message": {"id": "gone"}}]},
            {"labelsAdded": [{"message": {"id": "spammed", "labelIds": ["SPAM"]}}]},
        ], "historyId": "200"},
    ]
    messages = {
        "new1": _msg("new1", "Shop <deals@shop.com>", ["INBOX", "UNREAD"], "Your order shipped"),
        "sent1": _msg("sent1", "me@x.com", ["SENT"], to="Friend <f@friend.org>"),
    }
    sync._service_for = lambda _user: FakeGmail(history, messages)
    sync.MAX_RATE = 10_000  # no pacing in tests
    before = sync.data_version()

    out = sync.sync_incremental(db, user)

    stored = {m.gmail_id: m for m in db.query(Message)}
    assert out == {"added": 1, "updated": 1, "removed": 2}, out
    assert set(stored) == {"read-me", "new1"}, set(stored)          # deleted + spammed removed, draft/sent skipped
    assert stored["new1"].sender_domain == "shop.com" and stored["new1"].is_unread
    assert stored["read-me"].is_unread is False and stored["read-me"].labels == "INBOX"
    assert json.loads(user.replied_domains) == ["friend.org", "old.com"], user.replied_domains
    assert user.history_id == "200" and sync.data_version() == before + 1


def test_expired_history():
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    class Expired(FakeGmail):
        def list(self, **_):
            raise HttpError(Response({"status": 404}), b"history gone")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(email="me@x.com", encrypted_token="", history_id="1")
    db.add(user)
    db.commit()
    sync._service_for = lambda _user: Expired([], {})
    try:
        sync.sync_incremental(db, user)
        raise AssertionError("expected HistoryExpired")
    except sync.HistoryExpired:
        pass


def test_transient_errors_are_retried():
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    assert sync._is_transient(ConnectionResetError(10054, "forcibly closed"))  # the real failure seen
    assert sync._is_transient(TimeoutError())
    assert sync._is_transient(HttpError(Response({"status": 503}), b"backend error"))
    assert not sync._is_transient(HttpError(Response({"status": 404}), b"not found"))
    assert not sync._is_transient(ValueError("bug"))

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError(10054, "forcibly closed")
        return "ok"

    sleep, sync.time.sleep = sync.time.sleep, lambda _s: None
    try:
        assert sync._with_retry(flaky) == "ok" and len(calls) == 3
    finally:
        sync.time.sleep = sleep


def test_offline_icon_lookup_is_not_cached_as_missing():
    import tempfile
    import urllib.error
    from pathlib import Path
    from unittest import mock

    from app import icons

    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(icons, "CACHE_DIR", Path(tmp)):
        offline = urllib.error.URLError(OSError("getaddrinfo failed"))
        with mock.patch("urllib.request.urlopen", side_effect=offline):
            assert icons.get_icon("example-shop.com") is None
        assert not (Path(tmp) / "example-shop.com.none").exists()  # retried once online

        not_found = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=not_found):
            assert icons.get_icon("example-shop.com") is None
        assert (Path(tmp) / "example-shop.com.none").exists()  # a real miss is remembered


def test_pages_do_not_skip_messages():
    """A 250 first page, then 1000-pages, must list every id exactly once."""
    all_ids = [f"m{i}" for i in range(2600)]

    class _Lister:
        def users(self):
            return self

        def messages(self):
            return self

        def list(self, pageToken=None, maxResults=100, **_):
            start = int(pageToken or 0)
            end = start + maxResults
            page = {"messages": [{"id": i} for i in all_ids[start:end]]}
            if end < len(all_ids):
                page["nextPageToken"] = str(end)
            return _Call(page)

    seen, token, size = [], None, sync.FIRST_PAGE_SIZE
    while True:
        ids, token = sync._list_ids(_Lister(), "q", cap=size, page_token=token)
        seen += ids
        size = sync.PAGE_SIZE
        if not token:
            break
    assert seen == all_ids, (len(seen), len(set(seen)))


def test_prune_missing_after_history_expired():
    """Mail deleted in Gmail while history was unavailable is dropped; the rest stays."""
    class _Lister:
        def users(self):
            return self

        def messages(self):
            return self

        def list(self, pageToken=None, maxResults=100, **_):
            return _Call({"messages": [{"id": "kept"}, {"id": "new"}]})

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(email="me@x.com", encrypted_token="")
    db.add(user)
    db.flush()
    db.add_all([Message(user_id=user.id, gmail_id="kept", sender_domain="a.com"),
                Message(user_id=user.id, gmail_id="deleted", sender_domain="b.com")])
    db.commit()

    assert sync.prune_missing(db, user, _Lister()) == 1
    assert [m.gmail_id for m in db.query(Message)] == ["kept"]


def test_sender_emails_preview_newest_first():
    from datetime import datetime

    from app.routers import services

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(email="me@x.com", encrypted_token="tok")
    db.add(user)
    db.flush()
    db.add_all([Message(user_id=user.id, gmail_id=f"m{i}", sender_domain="shop.com", subject=f"Deal {i}",
                        date=datetime(2026, 1, i + 1), is_unread=i % 2 == 0) for i in range(12)]
               + [Message(user_id=user.id, gmail_id="other", sender_domain="other.com", subject="x")])
    db.commit()

    out = services.sender_emails("shop.com", db)
    assert len(out) == 10 and out[0]["subject"] == "Deal 11" and out[-1]["subject"] == "Deal 2", out
    assert out[0]["date"].endswith("Z") and out[1]["unread"] is True


def test_logout_keeps_data_delete_account_erases_it():
    from app.models import Decision
    from app.routers import auth, services

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(email="me@x.com", encrypted_token="tok")
    db.add(user)
    db.flush()
    db.add_all([Message(user_id=user.id, gmail_id="m1", sender_domain="a.com"),
                Decision(user_id=user.id, domain="a.com", decision="keep")])
    db.commit()
    auth._revoke = lambda _user: None
    services.cancel_sync = lambda: None

    auth.logout(db)
    assert auth.status(db) == {"connected": False}
    assert db.query(Message).count() == 1 and db.query(Decision).count() == 1  # kept for next sign-in
    assert user.encrypted_token == "" and not user.signed_in

    user.signed_in, user.encrypted_token = True, "tok"  # what signing back in does
    db.commit()
    assert auth.status(db)["connected"] is True

    auth.delete_account(db)
    assert auth.status(db) == {"connected": False}
    assert db.query(Message).count() == 0 and db.query(Decision).count() == 0 and db.query(User).count() == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all sync checks passed")
