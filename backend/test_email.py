"""Self-check for unsubscribe emails: who they go to, when they can be sent, what is recorded.
Run from backend/:  python test_email.py
"""
import base64
import email
from datetime import datetime, timedelta
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ai, gmail, security, sync, unsubscribe
from app.config import settings
from app.db import Base
from app.models import Decision, Message, UnsubAttempt, User
from app.routers import services

settings.ai_server_url = settings.ai_api_key = ""
AI_TEXT = {"subject": "Please unsubscribe me", "body": "Hello Etsy team, please take me off every mailing list. Thank you."}


class Gmail:
    """Just enough of the Gmail client: one Reply-To lookup and sending."""

    def __init__(self, reply_to=""):
        self.reply_to, self.sent = reply_to, []

    def users(self):
        return self

    messages = users

    def get(self, **_):
        return mock.Mock(execute=lambda: {"payload": {"headers": [{"name": "Reply-To", "value": self.reply_to}]}})

    def send(self, userId, body):
        return mock.Mock(execute=lambda: self.sent.append(body["raw"]))


def setup(scopes, reply_to="", list_unsubscribe=""):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(email="me@x.com", encrypted_token="t")
    db.add(user)
    db.flush()
    db.add(Message(user_id=user.id, gmail_id="m", sender_domain="email.etsy.com", sender_email="email@email.etsy.com",
                   subject="Sale", date=datetime(2026, 1, 1), list_unsubscribe=list_unsubscribe))
    db.commit()
    sync.bump_data_version()
    client = Gmail(reply_to)
    creds = gmail.Credentials("a", refresh_token="r", client_id="c", client_secret="s", scopes=scopes)
    gmail.build_gmail = lambda _creds: client
    gmail.credentials_from_json = lambda _data: creds
    security.decrypt = lambda token: token
    return db, client


READ = settings.scopes
SEND = settings.scopes + [gmail.SEND_SCOPE]


def refused(status, fn):
    try:
        fn()
    except HTTPException as e:
        assert e.status_code == status, (e.status_code, e.detail)
        return
    raise AssertionError("expected to be refused")


def out(to="email@email.etsy.com", subject="Stop", body="Please stop."):
    return services.EmailIn(to=to, subject=subject, body=body)


def test_draft_then_send_once():
    db, client = setup(SEND)
    with mock.patch.object(ai, "write_email", return_value=AI_TEXT):
        draft = services.draft_unsubscribe_email("email.etsy.com", db)
    assert draft["ai"] and draft["can_send"] and draft["to"] == "email@email.etsy.com" and "me@x.com" in draft["body"]
    assert client.sent == [] and db.query(Decision).count() == 0  # drafting sends and records nothing

    result = services.send_unsubscribe_email("email.etsy.com", out(subject=draft["subject"], body=draft["body"]), db)
    msg = email.message_from_bytes(base64.urlsafe_b64decode(client.sent[0]))
    assert (msg["To"], msg["Subject"], msg["X-Simplispace"]) == ("email@email.etsy.com", "Please unsubscribe me", "unsubscribe")
    assert result["status"] == "checking" and result["unsubscribed_via"] == "ai_email"  # later mail shows if it worked
    refused(409, lambda: services.send_unsubscribe_email("email.etsy.com", out(), db))
    assert len(client.sent) == 1


def test_sending_needs_permission_and_the_address_the_user_saw():
    db, client = setup(READ)
    refused(403, lambda: services.send_unsubscribe_email("email.etsy.com", out(), db))
    db, client2 = setup(SEND, reply_to="Etsy Help <help@etsy.com>")
    refused(409, lambda: services.send_unsubscribe_email("email.etsy.com", out(to="victim@other.com"), db))
    services.send_unsubscribe_email("email.etsy.com", out(to="help@etsy.com"), db)  # Reply-To is preferred
    assert client.sent == [] and len(client2.sent) == 1


def test_only_senders_without_an_unsubscribe_option():
    db, _ = setup(SEND, list_unsubscribe="<https://etsy.com/unsub>")
    refused(409, lambda: services.draft_unsubscribe_email("email.etsy.com", db))
    refused(409, lambda: services.send_unsubscribe_email("email.etsy.com", out(), db))


def test_bad_addresses_and_header_injection_are_refused():
    for bad in ("me@x.com", "a@b.com, c@d.com", "x@y.com\r\nBcc: z@z.com"):
        db, client = setup(SEND, reply_to=bad)
        if bad != "me@x.com":  # parsed down to one plain address, or refused; never several
            try:
                to = services._email_sender(db, db.query(User).first(), "email.etsy.com")[1]
                assert services._ADDRESS.fullmatch(to), to
            except HTTPException:
                pass
        else:
            refused(409, lambda: services.draft_unsubscribe_email("email.etsy.com", db))
    try:
        out(subject="Hi\r\nBcc: z@z.com")
        raise AssertionError("expected a validation error")
    except ValueError:
        pass
    no_reply = unsubscribe.is_no_reply
    assert no_reply("no-reply@x.com") and no_reply("noreply@mail.etsy.com") and no_reply("donotreply@x.com")
    assert not no_reply("email@email.etsy.com") and not no_reply("north@x.com")


def test_without_ai_there_is_a_standard_email_and_it_says_so():
    db, _ = setup(READ)
    draft = services.draft_unsubscribe_email("email.etsy.com", db)
    assert not draft["ai"] and draft["note"] and not draft["can_send"] and "Etsy team" in draft["body"]


def test_ai_drafts_with_links_or_placeholders_are_dropped():
    settings.ai_server_url = "https://ai.example.com"
    score = mock.Mock(domain="etsy.com", email_count=4, days_since_last=2)
    score.name = "Etsy"
    try:
        with mock.patch.object(ai, "_ask_server", return_value=AI_TEXT) as ask:
            assert ai.write_email(score, "ID") == AI_TEXT
        assert set(ask.call_args.args[1]) == {"service_name", "service_domain", "email_count", "days_since_last"}
        for bad in ({**AI_TEXT, "body": AI_TEXT["body"] + " [Your name]"}, {**AI_TEXT, "body": AI_TEXT["body"] + " www.x.com"},
                    {**AI_TEXT, "body": 5}, {"subject": "x"}, [], None):
            with mock.patch.object(ai, "_ask_server", return_value=bad):
                try:
                    ai.write_email(score, "ID")
                    raise AssertionError(bad)
                except ai.AIUnavailable:
                    pass
    finally:
        settings.ai_server_url = ""


def test_our_emails_dont_count_as_the_user_writing_to_a_sender():
    def msg(to, mark):
        headers = [{"name": "To", "value": to}] + ([{"name": "X-Simplispace", "value": "unsubscribe"}] if mark else [])
        return {"payload": {"headers": headers}}

    def fetch(_service, ids, headers, on_message, *_a, **_k):
        assert "X-Simplispace" in headers
        for m in ids:
            on_message(m)

    with mock.patch.object(sync, "_batch_fetch", fetch):
        got = sync._recipient_domains(None, [msg("a@etsy.com", True), msg("b@friend.org", False)], lambda: True)
    assert got == {"friend.org"}, got


def test_one_way_at_a_time_and_the_next_after_a_failure():
    db, _ = setup(SEND)
    domain = "email.etsy.com"
    options = lambda: services.unsubscribe_options(domain, db)  # noqa: E731
    assert (options()["steps"], options()["step"]) == (["email", "block"], "email")

    services.send_unsubscribe_email(domain, out(), db)
    services.set_decision(domain, services.DecisionIn(decision=None), db)  # plain undo: not a failure
    assert options()["step"] == "email"
    services.send_unsubscribe_email(domain, out(), db)
    db.query(Decision).first().page_result = "rejected"
    db.commit()
    services.set_decision(domain, services.DecisionIn(decision=None), db)  # "Try another way"
    assert options()["step"] == "block"

    db, _ = setup(SEND, reply_to="noreply@etsy.com")  # nothing to write to: email is skipped
    assert (options()["steps"], options()["step"]) == (["block"], "block")
    refused(409, lambda: services.draft_unsubscribe_email(domain, db))
    db, _ = setup(SEND, list_unsubscribe="<https://etsy.com/unsub>")
    assert "steps" not in options()  # senders with an unsubscribe option are unchanged


def test_replies_are_told_apart_by_headers_alone():
    kind = unsubscribe.reply_kind
    to, subject = "email@email.etsy.com", "Unsubscribe Request"
    ask = lambda h, sender, s, after=60: kind(h, sender, s, after, to, subject)  # noqa: E731
    assert ask({"x-failed-recipients": to}, "mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)") == "rejected"
    assert ask({"x-failed-recipients": "someone@else.com"}, "mailer-daemon@googlemail.com", "Failure") is None
    assert ask({}, "mailer-daemon@googlemail.com", "Delivery Status Notification (Delay)") is None
    assert ask({"auto-submitted": "auto-replied"}, "help@etsy.com", "We got your message") == "auto_reply"
    assert ask({}, "noreply@mail.etsy.com", "Unsubscribe Request") == "auto_reply"  # Etsy's real one, unlabelled
    assert ask({}, "noreply@mail.etsy.com", "Re: Unsubscribe Request") == "auto_reply"
    assert ask({}, "noreply@mail.etsy.com", "Weekend sale") is None
    assert ask({}, "noreply@mail.etsy.com", "Unsubscribe Request", 7200) is None
    assert ask({}, "anna@etsy.com", "Re: Unsubscribe Request") is None  # a person
    assert ask({"auto-submitted": "auto-generated"}, "email@email.etsy.com", "Sale") is None  # a newsletter


def test_sync_marks_automatic_replies_and_bounces():
    def result(sender, subject, headers):
        db, client = setup(SEND)
        services.send_unsubscribe_email("email.etsy.com", out(subject="Unsubscribe Request"), db)
        user = db.query(User).first()
        db.add(Message(user_id=user.id, gmail_id="r", sender_domain=sender.split("@")[1], sender_email=sender,
                       subject=subject, date=datetime.utcnow() + timedelta(seconds=30), list_unsubscribe=""))
        db.add(Message(user_id=user.id, gmail_id="o", sender_domain="other.com", sender_email="noreply@other.com",
                       subject="Unsubscribe Request", date=datetime.utcnow(), list_unsubscribe=""))
        db.commit()
        asked = []

        def get(id, **_):
            asked.append(id)
            return mock.Mock(execute=lambda: {"payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]}})

        client.get = get
        services._check_replies(db, user.id)
        assert asked == ["r"], asked  # other senders' mail isn't looked at
        return services._decision_json(db.query(Decision).first(), db, user)["status"]

    assert result("noreply@mail.etsy.com", "Unsubscribe Request", {}) == "auto_reply"
    assert result("mailer-daemon@googlemail.com", "Failure", {"X-Failed-Recipients": "email@email.etsy.com"}) == "rejected"
    assert result("email@email.etsy.com", "Sale", {}) == "checking"


def test_sending_is_only_requested_when_asked_for():
    assert gmail.SEND_SCOPE not in gmail.login_scopes()
    assert gmail.SEND_SCOPE not in gmail.authorization_url()[0].replace("%2F", "/").replace("%3A", ":")
    assert gmail.SEND_SCOPE in gmail.authorization_url(send=True, email="me@x.com")[0].replace("%2F", "/").replace("%3A", ":")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all email checks passed")
