"""Self-check: what the app records from the Gmail / unsubscribe window, without asking the user.
Run from backend/:  python test_unsubscribe.py
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import unsubscribe
from app.db import Base
from app.models import Decision, User
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
    services._open_in_app(GMAIL, user.id, "shop.com")
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all unsubscribe checks passed")
