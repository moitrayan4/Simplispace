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
    assert (msg["To"], msg["Subject"], msg["X-Simplispace"]) == ("email@email.etsy.com", "Please unsubscribe me", msg["X-Simplispace"])
    assert msg["X-Simplispace"].startswith("unsubscribe-")
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
                assert unsubscribe.ADDRESS.fullmatch(to), to
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


REPLIES = {
    "success": ["You have been unsubscribed from our mailing list.", "Hi, we've removed you from all lists. This mailbox is not monitored.",
                "Your email address has been removed from our database", "You will no longer receive emails from us", "You won't receive any more marketing.",
                "Your unsubscribe request has been processed", "We have now unsubscribed you. Please do not reply to this email.", "Done! You’ve been opted out.",
                "Your subscription was cancelled. Regards"],
    "rejected": ["Hello, This is an auto reply from Etsy.com. We are eager to assist you, but the email address to which you are replying cannot accept incoming inquiries.",
                 "This mailbox is not monitored. Please visit our help centre.", "This is an unmonitored address", "Please do not reply to this email; replies are not read",
                 "We are unable to process your request by email. Log in to change preferences", "We couldn't unsubscribe you because your address was not found",
                 "You have not been unsubscribed. We can't process requests here"],
    "acknowledged": ["We have received your request and will get back to you within 2 days", "Thank you for contacting Acme support. Your ticket has been created: #4411",
                     "Thanks for your email. A team member will look into it", "Your request has been received (#123)"],
    None: ["I am out of the office until Monday and will get back to you", "Weekend sale: 50% off everything", "Hi, could you tell us which address you used?",
           "Please remove me from every mailing list. Thank you.", "If you want to be removed, click unsubscribe", "To be unsubscribed, log in to your account", ""],
}


def test_what_a_reply_means():
    for want, texts in REPLIES.items():
        for text in texts:
            assert unsubscribe.reply_meaning(text) == want, (want, unsubscribe.reply_meaning(text), text)
    to = "email@email.etsy.com"
    kind = lambda h, sender, subject, preview, thread=True: unsubscribe.reply_kind(h, sender, subject, preview, to, thread)  # noqa: E731
    daemon, failed = "mailer-daemon@googlemail.com", "Address not found Your message wasn't delivered"
    assert kind({"x-failed-recipients": to}, daemon, "Failure", "", False) == "rejected"
    assert kind({}, daemon, "Failure", failed) == "rejected"
    # Somebody else's bounce says nothing about ours.
    assert kind({"x-failed-recipients": "other@x.com"}, daemon, "Failure", failed) is None
    assert kind({}, daemon, "Failure", failed, False) is None
    assert kind({}, daemon, "Delay", "Not delivered yet. Gmail will retry; delayed") is None
    assert kind({"auto-submitted": "auto-replied"}, "help@etsy.com", "Hello", "We love crafts") == "auto_reply"  # can't tell
    assert kind({}, "anna@etsy.com", "Re: x", "Hi, which address is it?") is None  # a person asking


def test_only_answers_to_our_email_are_read():
    ask = lambda subject, ours="Please unsubscribe me from Etsy", headers=None, thread=False, sender="a@etsy.com", after=60: (  # noqa: E731
        unsubscribe.is_reply(subject, ours, headers or {}, thread, sender, after))
    assert ask("Re: Please unsubscribe me from Etsy") and ask("[#12] Please unsubscribe me from Etsy") and ask("anything", thread=True)
    assert ask("Hello", headers={"auto-submitted": "auto-replied"})
    assert not ask("Weekend sale") and not ask("Hi there", ours="Hi") and not ask("Weekend sale", headers={"auto-submitted": "auto-generated"})


def follow(reply=None, sent=("SENT",), via="email", gone=False, thread=False):
    """Send an unsubscribe email, let `reply` (sender, subject, preview, headers) arrive, sync once."""
    auto = via == "mailto"
    db, client = setup(SEND, list_unsubscribe="<mailto:leave@list.etsy.com>" if auto else "")
    client.send = lambda userId, body: mock.Mock(execute=lambda: (client.sent.append(body["raw"]), {"id": "S1", "threadId": "T1"})[1])
    if auto:
        services.confirm_unsubscribe("email.etsy.com", services.UnsubscribeIn(method="mailto"), db)
    else:
        services.send_unsubscribe_email("email.etsy.com", out(subject="Please unsubscribe me from Etsy"), db)
    user, now = db.query(User).first(), datetime.utcnow() + timedelta(seconds=30)
    asked = []

    def full(mid, sender, subject, preview, headers):
        h = {"From": sender, "Subject": subject, **headers}
        return {"id": mid, "labelIds": ["INBOX"], "snippet": preview, "internalDate": str(int((now - datetime(1970, 1, 1)).total_seconds() * 1000)),
                "payload": {"headers": [{"name": k, "value": v} for k, v in h.items()]}}

    if reply and not thread:
        db.add(Message(user_id=user.id, gmail_id="R", sender_domain=reply[0].split("@")[1], sender_email=reply[0], subject=reply[1], date=now, list_unsubscribe=""))
    db.add(Message(user_id=user.id, gmail_id="OTHER", sender_domain="other.com", sender_email="x@other.com", subject="Private", date=now, list_unsubscribe=""))
    db.commit()

    def get(id, **_):
        asked.append(id)
        if id == "S1":
            if gone:
                from googleapiclient.errors import HttpError
                from httplib2 import Response
                return mock.Mock(execute=mock.Mock(side_effect=HttpError(Response({"status": 404}), b"")))
            return mock.Mock(execute=lambda: {"id": "S1", "threadId": "T1", "labelIds": list(sent)})
        return mock.Mock(execute=lambda: full(id, *reply))

    client.get = get
    ours = {"id": "S1", "labelIds": ["SENT"], "snippet": "You have been unsubscribed", "payload": {"headers": []}}
    client.threads = lambda: mock.Mock(get=lambda **_: mock.Mock(execute=lambda: {"messages": [ours] + ([full("R", *reply)] if thread else [])}))
    services._check_replies(db, user.id)
    db.expire_all()
    assert "OTHER" not in asked  # nobody else's mail is opened
    return services.list_decisions(db)["email.etsy.com"], db, client, asked


def test_next_sync_says_how_the_email_went():
    status = lambda *a, **k: follow(*a, **k)[0]["status"]  # noqa: E731
    d, *_ = follow()
    assert (d["status"], d["email_sent"]) == ("checking", True)  # seen in Sent, nobody answered yet
    assert status(gone=True) == "not_sent" == status(sent=("DRAFT",))
    subject = "Please unsubscribe me from Etsy"
    assert status(("noreply@mail.etsy.com", subject, REPLIES["rejected"][0], {})) == "rejected"  # Etsy's real one
    assert status(("care@etsy.com", "Re: " + subject, "You have been unsubscribed.", {})) == "page_confirmed"
    assert status(("care@etsy.com", "Re: " + subject, "We have received your request.", {})) == "acknowledged"
    assert status(("desk@zendesk.com", "Case 9", "We&#39;ve removed you from our list", {}), thread=True) == "page_confirmed"  # helpdesk, same conversation
    assert status(("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", "", {"X-Failed-Recipients": "email@email.etsy.com"})) == "rejected"
    assert status(("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", "Address not found", {"X-Failed-Recipients": "other@x.com"})) == "checking"
    assert status(("email@email.etsy.com", "Weekend sale", "You have been unsubscribed from prices!", {})) == "checking"  # just a newsletter
    # The sender's own unsubscribe address
    assert status(("list@list.etsy.com", "Re: unsubscribe", "You are now unsubscribed", {}), via="mailto") == "page_confirmed"
    assert status(("list@list.etsy.com", "Hello", "Thanks", {"Auto-Submitted": "auto-replied"}), via="mailto") == "checking"
    assert status(("mailer-daemon@googlemail.com", "Failure", "", {"X-Failed-Recipients": "leave@list.etsy.com"}), via="mailto") == "rejected"


def test_a_failed_email_brings_the_next_way_and_an_answer_is_not_still_emailing():
    d, db, *_ = follow(("noreply@mail.etsy.com", "Please unsubscribe me from Etsy", REPLIES["rejected"][0], {}))
    services.set_decision("email.etsy.com", services.DecisionIn(decision=None), db)  # Try another way
    assert services.unsubscribe_options("email.etsy.com", db)["step"] == "block"

    d, db, client, _ = follow(("email@email.etsy.com", "Re: Please unsubscribe me from Etsy", "We have received your request.", {}))
    row = db.query(Decision).first()
    started = row.unsubscribed_at
    # A later, clearer answer still counts, and a busy Gmail just means trying again.
    client.get = lambda **_: mock.Mock(execute=mock.Mock(side_effect=OSError("offline")))
    client.threads = lambda: mock.Mock(get=mock.Mock(side_effect=OSError("offline")))
    services._check_replies(db, db.query(User).first().id)
    assert db.query(Decision).first().page_result == "acknowledged"
    # Mail after the two days from their answer is what counts as still emailing.
    user = db.query(User).first()
    db.add(Message(user_id=user.id, gmail_id="ad", sender_domain="email.etsy.com", sender_email="email@email.etsy.com", subject="Sale",
                   date=started + timedelta(days=3), list_unsubscribe=""))
    db.commit()
    assert services.list_decisions(db)["email.etsy.com"]["status"] == "still_sending"


def test_unsubscribe_address_is_written_to_by_the_app():
    header = "<mailto:leave@list.etsy.com?subject=unsub%20123>"
    db, client = setup(SEND, list_unsubscribe=header)
    send = lambda: services.confirm_unsubscribe("email.etsy.com", services.UnsubscribeIn(method="mailto"), db)  # noqa: E731
    o = services.unsubscribe_options("email.etsy.com", db)
    assert o["email"] == {"to": "leave@list.etsy.com", "subject": "unsub 123"} and "steps" not in o
    assert send()["status"] == "checking"
    msg = email.message_from_bytes(base64.urlsafe_b64decode(client.sent[0]))
    assert (msg["To"], msg["Subject"], db.query(Decision).first().unsubscribed_via) == ("leave@list.etsy.com", "unsub 123", "auto_email")
    refused(409, send)
    assert len(client.sent) == 1
    db, client = setup(READ, list_unsubscribe=header)
    refused(403, lambda: services.confirm_unsubscribe("email.etsy.com", services.UnsubscribeIn(method="mailto"), db))
    db, client = setup(SEND, list_unsubscribe="<mailto:a@x.com,b@y.com>")
    refused(409, lambda: services.confirm_unsubscribe("email.etsy.com", services.UnsubscribeIn(method="mailto"), db))
    assert client.sent == [] and db.query(Decision).count() == 0


def test_long_tracking_addresses_arrive_unchanged():
    for to in (f"{'Ab-9' * 114}@list.shop.com", "u+a=b@x.com", "short@x.com"):
        db, client = setup(SEND)
        gmail.send_email(gmail.credentials_from_json(""), to, "S" * 150, "Please unsubscribe.")
        raw = base64.urlsafe_b64decode(client.sent[0])
        assert b"To: " + to.encode() + bytes([13, 10]) in raw, raw[:200]
        assert "".join(email.message_from_bytes(raw)["Subject"].split()) == "S" * 150


def test_a_lost_answer_is_looked_up_instead_of_guessed():
    for arrived in (True, False):
        db, client = setup(SEND)

        def send(userId, body):
            def lost():
                if arrived:
                    client.sent.append(body["raw"])
                raise ConnectionResetError(10054, "gone")
            return mock.Mock(execute=lost)

        client.send = send
        client.list = lambda **_: mock.Mock(execute=lambda: {"messages": [{"id": f"s{i}"} for i in range(len(client.sent))]})

        def get(id, metadataHeaders=(), **_):
            mark = email.message_from_bytes(base64.urlsafe_b64decode(client.sent[0]))["X-Simplispace"] if id.startswith("s") else ""
            return mock.Mock(execute=lambda: {"payload": {"headers": [{"name": "X-Simplispace", "value": mark}] if mark else []}})

        client.get = get
        go = lambda: services.send_unsubscribe_email("email.etsy.com", out(), db)  # noqa: E731
        if arrived:
            assert go()["status"] == "checking" and db.query(Decision).count() == 1
        else:
            refused(502, go)
            assert db.query(Decision).count() == 0


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
