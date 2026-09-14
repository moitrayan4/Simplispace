"""Self-check for the scoring engine. Run: python -m backend.test_scoring
   (or from backend/: python test_scoring.py)
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app import scoring


def msg(subject="", labels="", unread=False, unsub="", days_ago=1):
    return SimpleNamespace(
        subject=subject,
        labels=labels,
        is_unread=unread,
        list_unsubscribe=unsub,
        sender_domain="x.com",
        date=datetime.utcnow() - timedelta(days=days_ago),
    )


def test_marketing_newsletter():
    # promotional + unsubscribe, never opened -> Marketing
    msgs = [msg(labels="CATEGORY_PROMOTIONS", unread=True, unsub="<mailto:u@x.com>") for _ in range(10)]
    s = scoring.score_service("news.com", msgs, replied_domains=set())
    assert s.score == 50 - 30 - 20, s.score  # 0
    assert s.category == "Marketing/Inactive", s.category


def test_important_replied():
    msgs = [msg(subject="Re: hello", unread=False) for _ in range(5)]
    s = scoring.score_service("friend.com", msgs, replied_domains={"friend.com"})
    # base 50 + replied 40 + opened_frequently 25 = 115 -> clamped 100
    assert s.score == 100, s.score
    assert s.category == "Important"


def test_transactional():
    msgs = [msg(subject="Your order #123 shipped", unread=False, unsub="<x>") for _ in range(3)]
    s = scoring.score_service("shop.com", msgs, replied_domains=set())
    # 50 + transactional 35 + opened 25 - unsub 20 = 90
    assert s.score == 90, s.score
    assert s.category == "Important"


def test_inactive_bulk():
    msgs = [msg(labels="", unread=True) for _ in range(60)]
    s = scoring.score_service("spam.com", msgs, replied_domains=set())
    # 50 - inactive_bulk 40 = 10 -> Marketing
    assert s.score == 10, s.score
    assert s.category == "Marketing/Inactive"


def test_grouping_and_sort():
    msgs = [
        msg(unread=True, unsub="<x>") ,  # will be low
    ]
    msgs[0].sender_domain = "a.com"
    good = msg(subject="invoice", unread=False)
    good.sender_domain = "b.com"
    out = scoring.score_all([msgs[0], good], replied_domains=set())
    assert len(out) == 2
    assert out[0].score <= out[1].score  # sorted ascending by score


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all scoring checks passed")
