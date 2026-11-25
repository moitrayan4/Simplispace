"""Self-check: what the app records from the Gmail / unsubscribe window, without asking the user.
Run from backend/:  python test_unsubscribe.py
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import unsubscribe
from app.db import Base
from app.models import Decision, Message, User
from app.routers import services

GMAIL = "https://mail.google.com/mail/#all/1"
SHOP = "https://shop.com/unsubscribe"


def run(*pages, via="gmail"):
    """Open a window, show it each (url, text), close it; return the stored decision."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = services.SessionLocal = sessionmaker(bind=engine)
    db = Session()
    user = User(email="me@x.com", encrypted_token="t")
    db.add(user)
    db.flush()
    db.add(Decision(user_id=user.id, domain="shop.com", decision="unsubscribe", unsubscribed_via=via,
                    unsubscribed_at=datetime.utcnow(), page_result="in_progress"))
    db.commit()

    def opener(_url, _title, on_page, on_closed, _script):
        for url, text in pages:
            if on_page(url, text):
                break
        on_closed()

    unsubscribe.in_app_opener = opener
    services._open_in_app(GMAIL, user.id, "shop.com", db.query(Decision).first().unsubscribed_at)
    unsubscribe.in_app_opener = None
    db.expire_all()
    row = db.query(Decision).first()
    return row and (services._decision_json(row, db, user)["status"], row.unsubscribed_via)


def test_gmail_notice_is_recorded_without_asking():
    assert run((GMAIL, ""), (GMAIL, "You unsubscribed from Shop | Undo")) == ("page_confirmed", "gmail")


def test_closing_gmail_without_unsubscribing_changes_nothing():
    assert run((GMAIL, ""), (GMAIL, "Conversation archived")) is None
    assert run() is None


def test_go_to_website_is_followed():
    assert run((GMAIL, ""), ("about:blank", ""), (SHOP, "You have been unsubscribed")) == ("page_confirmed", "in_app")
    assert run((GMAIL, ""), (SHOP, "Choose your email topics")) == ("checking", "in_app")
    assert run((GMAIL, ""), (SHOP, "Link expired")) == ("page_failed", "in_app")


def test_other_links_are_not_an_unsubscribe():
    # A product link is not an unsubscribe, even if its page says "removed from".
    assert run((GMAIL, ""), ("https://shop.com/cart", "Item removed from your basket")) is None
    basket = "Item removed from your basket. Sign up to our newsletter, unsubscribe at any time. Subscribe"
    assert run((GMAIL, ""), ("https://shop.com/cart", basket)) is None
    assert run((GMAIL, ""), ("https://shop.com/unsubscribe", "You were removed from our mailing list")) == ("page_confirmed", "in_app")
    assert run((GMAIL, "")) is None


class Window:
    closed = False

    def __init__(self):
        self.shown = 0

    def close(self):
        self.closed = True

    def show(self):
        self.shown += 1


def live():
    """A sender whose Gmail window is open; returns everything a test needs to drive it."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    services.SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db = services.SessionLocal()
    user = User(email="me@x.com", encrypted_token="t")
    db.add(user)
    db.flush()
    db.add(Message(user_id=user.id, gmail_id="m", sender_domain="shop.com", sender_email="a@shop.com", subject="Sale", date=datetime(2026, 1, 1),
                   list_unsubscribe="<https://shop.com/u>"))
    db.commit()
    services._unsubscribe_options = lambda *_: unsubscribe.parse("<https://shop.com/u>")
    services._windows.clear()
    opened = []

    def opener(_url, _title, on_page, on_closed, _script):
        opened.append((Window(), on_page, on_closed))
        return opened[-1][0]

    unsubscribe.in_app_opener = opener
    start = lambda method="gmail": services.confirm_unsubscribe("shop.com", services.UnsubscribeIn(method=method), db)  # noqa: E731
    row = lambda: (db.expire_all(), db.query(Decision).first())[1]  # noqa: E731
    return db, user, opened, start, row


def test_undo_show_and_cancel_reach_the_open_window():
    db, user, opened, start, row = live()
    assert start()["status"] == "in_progress"
    assert services.unsubscribe_window("shop.com", services.WindowIn(action="show"), db) == {"open": True} and opened[0][0].shown == 1
    services.set_decision("shop.com", services.DecisionIn(decision=None), db)
    assert opened[0][0].closed and row() is None and not services._windows
    start()
    assert services.unsubscribe_window("shop.com", services.WindowIn(action="cancel"), db) == {"open": False}
    assert opened[1][0].closed and row() is None
    assert services.unsubscribe_window("shop.com", services.WindowIn(action="show"), db) == {"open": False}


def test_gmails_button_is_looked_for_where_it_exists():
    db, user, *_ = live()
    db.add(Message(user_id=user.id, gmail_id="newer", sender_domain="shop.com", sender_email="a@shop.com", subject="Receipt",
                   date=datetime(2026, 2, 1), list_unsubscribe=""))
    db.commit()
    assert services._gmail_url(db, user, "shop.com", with_button=True).endswith("/m")
    assert services._gmail_url(db, user, "shop.com").endswith("/newer")  # blocking works from any email


def test_an_old_window_cannot_touch_a_newer_attempt():
    db, user, opened, start, row = live()
    start()
    services.set_decision("shop.com", services.DecisionIn(decision=None), db)
    start()
    opened[0][1](GMAIL, "You unsubscribed from Shop")  # the first window reports late
    opened[0][2]()
    assert row().page_result == "in_progress" and services._windows[(user.id, "shop.com")] is opened[1][0]
    opened[1][1](GMAIL, "You unsubscribed from Shop")
    assert row().page_result == "success"


def test_what_gmail_says_wins_over_the_button_expected():
    db, _, opened, start, row = live()
    start("gmail")
    opened[0][1](GMAIL, "You blocked Shop")
    opened[0][2]()
    assert (row().unsubscribed_via, row().page_result) == ("block", "success")


def test_closing_after_success_keeps_it_and_saving_is_retried():
    from sqlalchemy.exc import OperationalError

    db, _, opened, start, row = live()
    start()
    real, calls = services._save_page_result, []

    def busy(*a):
        calls.append(1)
        if len(calls) < 3:
            raise OperationalError("x", {}, Exception("database is locked"))
        return real(*a)

    services._save_page_result, sleep = busy, services.time.sleep
    services.time.sleep = lambda _s: None
    try:
        assert opened[0][1](GMAIL, "You unsubscribed from Shop") is True and len(calls) == 3
        opened[0][2]()
    finally:
        services._save_page_result, services.time.sleep = real, sleep
    assert row().page_result == "success"


def test_nothing_is_left_in_progress():
    from fastapi import HTTPException

    from app.routers import auth

    db, _, opened, start, row = live()
    start()
    services.forget_unfinished()  # the app was closed mid-way and opened again
    assert row() is None

    def broken(*_):
        raise RuntimeError("no window")

    unsubscribe.in_app_opener, keep = broken, unsubscribe.in_app_opener
    try:
        start()
        raise AssertionError("expected an error")
    except HTTPException as e:
        assert e.status_code == 502 and row() is None
    unsubscribe.in_app_opener = keep
    start()
    auth._revoke = lambda _u: None
    auth.logout(db)
    assert opened[-1][0].closed and not services._windows


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all unsubscribe checks passed")
