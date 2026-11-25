"""Self-check for AI second opinions: what is sent, to whom, and how sign-in is proven.
Run from backend/:  python test_ai.py
"""
import io
import json
import time
import urllib.error
from datetime import datetime
from types import SimpleNamespace as NS
from unittest import mock

from app import ai, gmail, scoring
from app.config import settings

SERVER = "https://ai.example.workers.dev"


def use(server="", key=""):
    settings.ai_server_url, settings.ai_api_key = server, key
    settings.ai_base_url = "https://api.groq.com/openai/v1"


def sender():
    messages = [NS(subject=f"Your order {i}", date=datetime(2026, 1, i + 1), labels="", list_unsubscribe="",
                   is_unread=True, sender_email="a@shop.com", sender_domain="shop.com") for i in range(4)]
    return scoring.score_service("shop.com", messages, set()), messages


def creds(scopes):
    return gmail.credentials_from_json(json.dumps(
        {"token": "gmail-access", "refresh_token": "r", "client_id": "cid", "client_secret": "s", "scopes": scopes}))


def test_server_mode_sends_no_subject_lines():
    use(server=SERVER)
    data, digest = ai.make_input(*sender())
    assert "recent_subjects" not in data and "Your order" not in json.dumps(data), data
    # Exactly the fields the Worker accepts.
    assert set(data) == {"company", "website", "email_domain", "emails", "opened", "emails_per_month",
                         "days_since_first", "days_since_last", "you_wrote_to_them", "promotional_emails",
                         "emails_with_unsubscribe_link", "order_or_account_emails"}, set(data)
    assert data["website"] == "shop.com" and "score" not in json.dumps(data)  # not told what the rules think
    use(key="own-key")
    own, own_digest = ai.make_input(*sender())
    assert len(own["recent_subjects"]) == 3 and own_digest != digest  # results aren't mixed between the two


def test_own_key_wins_and_bad_addresses_are_refused():
    use()
    assert not ai.provider_ready() and not ai.uses_server()
    use(server=SERVER)
    assert ai.uses_server() and ai.provider_label() == "ai.example.workers.dev" and ai.setup_message() is None
    use(server=SERVER, key="own-key")
    assert ai.provider_ready() and not ai.uses_server()
    for bad in ("http://ai.example.com", "https://u:p@ai.example.com", "ftp://x", "nonsense"):
        use(server=bad)
        assert not ai.provider_ready() and ai.setup_message(), bad


def test_only_the_id_token_goes_to_the_server():
    use(server=SERVER)
    seen = []

    class Response(io.BytesIO):
        status = 200

    def open_(_self, request, timeout=None):
        seen.append(request)
        return Response(json.dumps({"category": "Useful", "confidence": 0.9, "about": "A shop.", "reason": "You open it.", "advice": "Keep.", "sources": ["shop.com"]}).encode())

    with mock.patch("urllib.request.OpenerDirector.open", open_):
        out = ai.classify(ai.make_input(*sender())[0], "ID-TOKEN")
        assert out["status"] == "applied" and out["category"] == "Useful" and out["model"] == "Simplispace AI"
        assert ai.details(out["explanation"]) == {"about": "A shop.", "reason": "You open it.", "advice": "Keep.", "sources": ["shop.com"]}
        try:
            ai.classify({}, None)
            raise AssertionError("expected AIUnavailable")
        except ai.AIUnavailable:
            pass
    assert len(seen) == 1 and seen[0].full_url == SERVER + "/v2/opinion"
    assert seen[0].get_header("Authorization") == "Bearer ID-TOKEN"


def test_an_opinion_stays_until_the_rules_change_their_mind():
    use(server=SERVER)
    service, messages = sender()
    _, digest = ai.make_input(service, messages)
    record = NS(domain="shop.com", input_hash=digest, rule_category=service.category, suggested_category="Optional",
                confidence=0.9, status="applied", model="Simplispace AI", created_at=datetime.utcnow(),
                explanation=json.dumps({"about": "A shop.", "reason": "Because."}))
    db = mock.Mock()
    db.query.return_value.filter_by.return_value.all.return_value = [record]

    def shown(change=lambda s: None):
        s, _ = sender()
        change(s)
        ai.apply_saved(db, NS(id=1, ai_enabled=True), [], [s])
        return s.ai_classification

    first = shown()
    assert first["about"] == "A shop." and first["reason"] == "Because." and first["category"] == "Optional"
    later = lambda s: (setattr(s, "email_count", 99), setattr(s, "days_since_last", 0))  # noqa: E731
    assert shown(later)  # new mail or another day doesn't make it vanish
    assert shown(lambda s: (setattr(s, "rule_category", "Other"))) is None
    record.created_at = datetime.utcnow() - ai.OPINION_LASTS - ai.OPINION_LASTS
    assert shown() is None
    assert ai.details("old plain text") == {"reason": "old plain text"}


def test_personal_mail_and_odd_domains_are_not_looked_up():
    use(server=SERVER)
    service, messages = sender()
    for domain, website in (("gmail.com", None), ("mail.sendgrid.net", None), ("facebookmail.com", "facebook.com"),
                            ("email.etsy.com", "etsy.com"), ("localhost", None)):
        service.domain = domain
        assert ai.make_input(service, messages)[0]["website"] == website, domain


def test_server_errors_show_our_wording_only():
    use(server=SERVER)
    for status, body, expect in (
        (429, b'{"error":"user_limit"}', "limit"), (401, b'{"error":"sign_in_required"}', "sign in again"),
        (502, b"<html>raw provider text</html>", "right now"), (500, b'{"error":["raw"]}', "right now"),
    ):
        error = urllib.error.HTTPError("u", status, "x", {}, io.BytesIO(body))
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=error):
            try:
                ai.classify({}, "t")
                raise AssertionError("expected AIUnavailable")
            except ai.AIUnavailable as e:
                assert expect in str(e) and "raw" not in str(e), str(e)


def test_openid_only_asked_for_with_a_server_and_old_sign_ins_keep_working():
    use()
    assert gmail.login_scopes() == settings.scopes
    use(server=SERVER)
    assert gmail.login_scopes() == ["openid"] + settings.scopes
    old = creds(settings.scopes)
    assert old.scopes == settings.scopes and not gmail.has_openid(old)  # refreshes with what it was granted
    try:
        gmail.id_token("me@x.com", old)
        raise AssertionError("expected SignInNeeded")
    except gmail.SignInNeeded:
        pass
    assert "sign in again" in (ai._sign_in_hint(NS(encrypted_token="")) or "")


def test_id_token_is_cached_and_forgotten_on_logout():
    use(server=SERVER)
    c, refreshes = creds(["openid"] + settings.scopes), []

    def refresh(self, _request):
        refreshes.append(1)
        self._id_token = "ID"

    with mock.patch("google.oauth2.credentials.Credentials.refresh", refresh), mock.patch.object(
        gmail.google_id_token, "verify_oauth2_token", return_value={"exp": time.time() + 3600}
    ):
        assert gmail.id_token("me@x.com", c) == gmail.id_token("me@x.com", c) == "ID" and len(refreshes) == 1
        gmail.forget_id_token("me@x.com")
        gmail.id_token("me@x.com", c)
        assert len(refreshes) == 2
    gmail.forget_id_token("me@x.com")


def test_sign_in_copes_with_whatever_permissions_google_grants():
    import requests

    use(server=SERVER)
    read = settings.scopes[0]

    def exchange(scope):
        def answer(self, method, url, **_):
            r = requests.Response()
            r.status_code, r.request = 200, mock.Mock(headers={}, body="")
            r._content = json.dumps({"access_token": "a", "refresh_token": "r", "expires_in": 3599,
                                     "token_type": "Bearer", "id_token": "x.y.z", "scope": scope}).encode()
            return r

        with mock.patch("requests.Session.request", answer):
            return gmail.credentials_from_json(gmail.credentials_to_json(gmail.exchange_code("c", "s", "v")))

    assert exchange(f"{read} openid").scopes == ["openid", read]
    extra = exchange(f"openid {read} https://www.googleapis.com/auth/userinfo.email")  # more than we asked for
    assert extra.scopes == ["openid", read] and extra.refresh_token == "r" and not extra.expired
    assert not gmail.has_openid(exchange(read))  # signed in, AI just unavailable
    try:
        exchange("openid")  # Gmail box not ticked
        raise AssertionError("expected GmailNotAllowed")
    except gmail.GmailNotAllowed:
        pass


def test_links_in_ai_text_are_removed():
    use(server=SERVER)
    out = ai._validated({"category": "Useful", "confidence": 0.9, "about": "Go to https://evil.example/x now", "reason": "Mail help@evil.example", "advice": "See www.evil.example."})
    assert "evil" not in out["explanation"], out["explanation"]


def test_sign_in_attempts_expire():
    from fastapi import HTTPException

    from app.routers import auth

    auth._pending_states["old"] = ("v", time.time() - 1)
    try:
        auth.callback(state="old", code="c", error=None, db=None)
        raise AssertionError("expected to be refused")
    except HTTPException as e:
        assert e.status_code == 400 and "old" not in auth._pending_states


def test_failed_sign_in_returns_to_the_app_without_details():
    from app.routers import auth

    for error, expect in ((gmail.GmailNotAllowed(), "gmail"), (ValueError("secret detail"), "ValueError")):
        auth._pending_states["st"] = ("v", time.time() + 60)
        with mock.patch.object(gmail, "exchange_code", side_effect=error):
            r = auth.callback(state="st", code="c", error=None, db=None)
        assert r.headers["location"].endswith(f"/?login_error={expect}") and "st" not in auth._pending_states


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all AI checks passed")
