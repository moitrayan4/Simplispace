"""Self-check for the scoring engine. Run: python -m backend.test_scoring
   (or from backend/: python test_scoring.py)
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app import scoring, unsubscribe


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
    assert s.facts["transactional_words"] == ["order", "shipped"], s.facts
    assert s.facts["transactional_count"] == 3 and s.facts["read_count"] == 3


def test_inactive_bulk():
    msgs = [msg(labels="", unread=True) for _ in range(60)]
    s = scoring.score_service("spam.com", msgs, replied_domains=set())
    # 50 - inactive_bulk 40 = 10 -> Marketing
    assert s.score == 10, s.score
    assert s.category == "Marketing/Inactive"
    # opening them would add +25 and remove the -40 inactive penalty: 10 + 65 = 75
    what_if = {w["action"]: w for w in s.facts["what_if"]}
    assert what_if["open"]["score"] == 75 and what_if["open"]["category"] == "Important", what_if
    assert s.facts["next_category"] == "Optional" and s.facts["points_to_next"] == 10, s.facts


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



def test_inbox_insights():
    def svc(domain, category, count):
        return scoring.ServiceScore(domain, domain, count, 0, 1, False, 50, category)

    scored = [svc("good.com", "Important", 10), svc("ads.com", "Marketing/Inactive", 10), svc("meh.com", "Optional", 10)]
    monthly = {"good.com": 10, "ads.com": 10, "meh.com": 10}
    # (10*1 + 10*0 + 10*0.5) / 30 = 50
    out = scoring.inbox_insights(scored, monthly, {}, set())
    assert out["health_score"] == 50, out
    assert out["monthly_reduction"] == 0 and out["potential_monthly_reduction"] == 10, out
    # dropping the ads.com candidate would give (10 + 5) / 20 = 75
    assert out["potential_health_score"] == 75 and out["candidate_count"] == 1, out
    assert out["mix"] == {"Important": 33, "Useful": 0, "Optional": 33, "Marketing/Inactive": 33}, out
    # unsubscribing ads.com removes it: (10 + 5) / 20 = 75, 10/month cut = 20 min/year
    out = scoring.inbox_insights(scored, monthly, {"ads.com": "unsubscribe"}, {"ads.com"})
    assert out["health_score"] == 75, out
    assert out["monthly_reduction"] == 10 and out["minutes_saved_per_year"] == 20, out
    # keep overrides the Marketing category for the "potential" estimate
    out = scoring.inbox_insights(scored, monthly, {"ads.com": "keep"}, set())
    assert out["potential_monthly_reduction"] == 0, out
    # no recent mail: falls back to all-time counts
    assert scoring.inbox_insights(scored, {}, {}, set())["health_score"] == 50


def test_unsubscribe_parse():
    opts = unsubscribe.parse(
        "<mailto:u@x.com?subject=unsub>, <https://x.com/u?id=1>", "List-Unsubscribe=One-Click"
    )
    assert opts == {"one_click": True, "link": "https://x.com/u?id=1", "mailto": "mailto:u@x.com?subject=unsub"}, opts
    # one-click requires the Post header and an https link
    assert unsubscribe.parse("<https://x.com/u>")["one_click"] is False
    assert unsubscribe.parse("<http://x.com/u>", "List-Unsubscribe=One-Click")["one_click"] is False
    # unsupported schemes are ignored
    assert unsubscribe.parse("<javascript:alert(1)>, <file:///c:/x>") == {"one_click": False, "link": None, "mailto": None}
    # one-click refuses private/loopback targets
    for bad in ("https://127.0.0.1/u", "https://localhost/u", "http://example.com/u"):
        try:
            unsubscribe.one_click(bad)
            raise AssertionError(bad)
        except ValueError:
            pass



def test_unsubscribe_verification():
    t = datetime(2026, 11, 1)
    v = unsubscribe.verification
    assert v(t, 0, None) == "checking"                       # never synced since
    assert v(t, 0, t + timedelta(days=1)) == "checking"      # still inside the window
    assert v(t, 0, t + timedelta(days=3)) == "confirmed"     # quiet past the grace period
    assert v(t, 2, t + timedelta(days=3)) == "still_sending"  # mail after the grace period
    assert v(t, 0, None, "success") == "page_confirmed"
    assert v(t, 0, None, "failed") == "page_failed"
    assert v(t, 0, None, "unclear") == "needs_answer"
    assert v(t, 0, None, "in_progress") == "in_progress"
    assert v(t, 1, None, "success") == "still_sending"         # mail beats what the page claimed


def test_classify_page():
    c = unsubscribe.classify_page
    assert c('{"success":false,"message":"POST required."}') == "failed"   # the real theresanaiforthat reply
    assert c('{"success":true}') == "success"
    assert c("You have been unsubscribed from our newsletter.") == "success"
    assert c("You’ll no longer receive these emails") == "success"
    assert c("Failed to unsubscribe. Please try again.") == "failed"
    assert c("You were unsubscribed, but an error occurred updating preferences") is None  # mixed: undecided
    assert c("Click the button below to unsubscribe") is None              # a form still to submit
    assert c("Verifying you are human. This may take a few seconds.") is None



def test_gmail_in_app():
    done = unsubscribe.gmail_done
    assert done("Unsubscribed from Intrainz") and done("You blocked x@intrainz.com")
    assert done("Message sent. Undo View message") and done("Conversation reported as spam")
    assert not done("") and not done("Conversation archived. Undo")
    url = unsubscribe.gmail_compose_url("mailto:off@list.example.com?subject=Unsubscribe%20me", "me@gmail.com")
    assert "view=cm" in url and "to=off%40list.example.com" in url and "su=Unsubscribe+me" in url, url


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all scoring checks passed")
