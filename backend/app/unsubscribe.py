"""Unsubscribe actions, run only after the user confirms (proposal week 10).

Header values come from senders, so they are untrusted: only https/http/mailto
targets are accepted, and the one-click POST only goes to public internet hosts.
No Gmail write scope is needed: one-click is an HTTPS POST to the sender's own
endpoint (RFC 8058), and the link/mailto options hand off to the user's browser
or mail app, where the user finishes the action themselves.
"""
import ipaddress
import re
import socket
import ssl
import certifi
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta

ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
# Mailing lists put long tracking codes before the @, well past the usual 64 characters.
_LIST_ADDRESS = re.compile(ADDRESS.pattern.replace("{1,64}", "{1,600}"))
_TARGET = re.compile(r"<([^>]+)>")
_ONE_CLICK_BODY = b"List-Unsubscribe=One-Click"


def parse(list_unsubscribe: str, list_unsubscribe_post: str = "") -> dict:
    """Split List-Unsubscribe into the first web link and first mailto target."""
    link = mailto = None
    header = (list_unsubscribe or "").strip()
    # Some senders leave out the <...> the standard asks for.
    for target in _TARGET.findall(header) or header.split():
        target = target.strip().rstrip(",")
        scheme = urllib.parse.urlsplit(target).scheme.lower()
        if scheme in ("https", "http") and not link:
            link = target
        elif scheme == "mailto" and not mailto:
            mailto = target
    post = (list_unsubscribe_post or "").lower().replace(" ", "")
    one_click = bool(link) and link.lower().startswith("https:") and "list-unsubscribe=one-click" in post
    return {"one_click": one_click, "link": link, "mailto": mailto}


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return False
    return bool(infos) and all(
        ipaddress.ip_address(info[4][0].split("%")[0]).is_global for info in infos
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect could point at a private address; treat it as failure


def one_click(url: str) -> None:
    """Send the RFC 8058 one-click POST. Raises ValueError on unsafe URL or failure."""
    parts = urllib.parse.urlsplit(url)
    # ponytail: resolve-then-connect leaves a tiny DNS-rebinding window; pin the
    # resolved IP in the connection if this ever runs on a shared server.
    if parts.scheme != "https" or not parts.hostname or not _is_public_host(parts.hostname):
        raise ValueError("The sender's unsubscribe address is not a safe public HTTPS URL.")
    request = urllib.request.Request(
        url,
        data=_ONE_CLICK_BODY,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Simplispace/1.0 (unsubscribe)",
        },
    )
    tls = ssl.create_default_context(cafile=certifi.where())
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=tls))
    try:
        with opener.open(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise ValueError(f"The sender answered with HTTP {response.status}.")
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            return  # accepted, then redirected to a confirmation page we don't follow
        raise ValueError(f"The sender answered with HTTP {e.code}.") from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise ValueError("The sender’s unsubscribe server has an invalid or untrusted HTTPS certificate.") from e
        raise ValueError("Could not reach the sender’s unsubscribe server.") from e
    except (TimeoutError, OSError) as e:
        raise ValueError("Could not reach the sender's unsubscribe server.") from e


def open_external(url: str) -> None:
    """Open a page or mail draft for the user: in a Simplispace window when running as
    the desktop app, otherwise (development in a browser) in the default browser/mail app."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme != "https":
        raise ValueError("Only https links can be opened.")
    if in_app_opener:
        in_app_opener(url, "Simplispace", lambda *_: False, lambda: None, PAGE_TEXT)
        return
    if not webbrowser.open(url):
        raise ValueError("Could not open your browser.")


# --- Did it work? ------------------------------------------------------------
# Senders can't be asked directly, but Gmail's bulk-sender rules give them two days
# to honour an unsubscribe. So: mail after that grace period means it failed; a
# sync that ran a day past the grace period with no new mail means it worked.
GRACE = timedelta(days=2)
CONFIRM_AFTER = timedelta(days=3)


def verification(
    unsubscribed_at: datetime,
    emails_after_grace: int,
    last_synced_at: datetime | None,
    page_result: str | None = None,
) -> str:
    """Status of a recorded unsubscribe.

    Mail after the grace period always wins: whatever the page said, the sender
    is still emailing. Otherwise the sender's own page (when it was opened inside
    the app) decides, and failing that, a quiet mailbox does.
    """
    if emails_after_grace > 0:
        return "still_sending"
    if page_result == "in_progress":
        return "in_progress"
    if page_result == "success":
        return "page_confirmed"
    if page_result == "failed":
        return "page_failed"
    if page_result in ("rejected", "not_sent", "acknowledged"):
        return page_result
    if page_result == "unclear":
        return "needs_answer"
    if last_synced_at and last_synced_at >= unsubscribed_at + CONFIRM_AFTER:
        return "confirmed"
    return "auto_reply" if page_result == "auto_reply" else "checking"


# Outcomes that mean "offer the next way".
FAILED_STATUSES = {"still_sending", "page_failed", "rejected", "not_sent", "auto_reply"}
# For senders with no unsubscribe option, in the order they are offered.
STEPS = ("email", "block")
STEP_OF = {"ai_email": "email", "block": "block"}

_NO_REPLY = re.compile(r"^(no|do-?_?\.?not)[-_.]?reply|^(bounces?|mailer-daemon)\b", re.I)


def is_no_reply(address: str) -> bool:
    return bool(_NO_REPLY.match(address or ""))


# --- What became of an unsubscribe email --------------------------------------
# Only replies to emails this app sent are looked at: their headers, subject and the one-line
# preview Gmail shows in its list. The preview is used here and never stored or sent on.
REPLY_HEADERS = ["From", "Subject", "Auto-Submitted", "X-Autoreply", "X-Autorespond", "Precedence", "X-Failed-Recipients"]

_SENTENCE = re.compile(r"[.!?;\n]+\s*|\s+[-–|]\s+")
_WILL_NOT_GET = re.compile(r"\b(?:will not|won['’]t|should not|shouldn['’]t|no longer)\b.{0,20}\b(?:receive|get|hear from|be (?:sent|emailed|contacted))\b")
_DONE = re.compile(
    r"\b(?:you|you['’]re|you['’]ve|your (?:e-?mail(?: address)?|address|subscription|account|details|name))\b.{0,60}\b(?:unsubscribed|removed|opted out|taken off|cancell?ed)\b"
    r"|\bwe(?:['’]ve| have)?(?: now| successfully)? (?:unsubscribed|removed|opted|taken) you"
    r"|\b(?:successfully|now) (?:unsubscribed|removed|opted out)\b"
    r"|\bunsubscri(?:be|ption)(?: request)?(?: was| is| has been)? (?:successful|processed|completed?|confirmed)\b"
    r"|\brequest (?:has been|was|is) (?:processed|completed?|actioned|honou?red)\b"
)
_NO = re.compile(r"\bnot\b|\bcannot\b|n['’]t\b|\bunable\b|\bfailed\b|\bif you\b|\bto be (?:removed|unsubscribed)\b|\b(?:would|want|wish|like|need) to\b")
_REFUSED = re.compile(
    r"\b(?:can ?not|can['’]t|unable to|not able to|does(?: not|n['’]t)|do(?: not|n['’]t)|could(?: not|n['’]t)|will not|won['’]t|is(?: not|n['’]t)|are(?: not|n['’]t)|was(?: not|n['’]t))\b"
    r".{0,40}\b(?:accept|receive|respond|reply|process|deliver(?:ed)?|read|answered|monitored|unsubscribe|remove|complete|attended|in use|valid)\b"
    r"|\bunmonitored\b|\bunattended\b|\bundeliverable\b|\bno one (?:reads|monitors)\b|\bno longer (?:in use|active|monitored|exists?|valid)\b"
    r"|\bmailbox (?:is )?(?:full|unavailable|not found)\b|\b(?:address|user|recipient)\b.{0,30}\b(?:not found|unknown|invalid|does not exist)\b"
    r"|\bdelivery\b.{0,20}\bfail"
)
_RECEIVED = re.compile(
    r"\b(?:received|got) your (?:unsubscribe |opt[- ]?out )?(?:request|message|e-?mail|enquiry|inquiry|query)\b"
    r"|\byour (?:request|ticket|case|message|e-?mail|enquiry|inquiry) (?:has been |was |is )?(?:received|created|logged|opened|submitted|registered)\b"
    r"|\b(?:request|ticket|case) (?:has been |was )(?:received|created|logged|opened|submitted|registered)\b"
    r"|\bthank(?:s| you) for (?:contacting|reaching|writing|getting in touch|your (?:e-?mail|message|request))\b"
    r"|\bwe(?:['’]ll| will| are)\b.{0,40}\b(?:get back|respond|look(?:ing)? into|process(?:ing)?|review(?:ing)?|unsubscribe|remove)\b"
)
_AWAY = re.compile(r"\bout of (?:the )?office\b|\bon (?:annual |sick )?leave\b|\bvacation\b|\baway from\b")
_DELAYED = re.compile(r"\bdelay|\bincomplete\b|\btemporar|\bretry|\btry again\b|\bstill being\b")


def reply_meaning(text: str) -> str | None:
    """'success' (you're unsubscribed) | 'rejected' (not accepted) | 'acknowledged' (received) | None."""
    sentences = [x for x in _SENTENCE.split((text or "").lower()) if x]
    # Saying it's done wins: such emails often end with "this mailbox is not monitored".
    for x in sentences:
        if (_WILL_NOT_GET.search(x) and "you" in x and not re.search(r"\bif you\b", x)) or (_DONE.search(x) and not _NO.search(x)):
            return "success"
    if any(_REFUSED.search(x) for x in sentences):
        return "rejected"
    if any(_AWAY.search(x) for x in sentences):
        return None
    return "acknowledged" if any(_RECEIVED.search(x) for x in sentences) else None


def is_reply(subject: str, sent_subject: str | None, headers: dict, in_thread: bool, sender: str, seconds_after: float) -> bool:
    """Whether a message answers our email. Anything else from the sender is just their mail."""
    if in_thread or _labelled_automatic(headers):
        return True
    strip = lambda s: re.sub(r"^((re|aw|fwd?|auto(matic)?([- ]?reply)?)\s*:\s*)+", "", (s or "").strip().lower())  # noqa: E731
    ours, theirs = strip(sent_subject), strip(subject)
    if ours and (ours == theirs or (len(ours) >= 12 and ours in theirs)):
        return True
    return not ours and seconds_after <= 600 and is_no_reply(sender)


def _labelled_automatic(headers: dict) -> bool:
    return (
        headers.get("auto-submitted", "").lower().startswith("auto-replied")
        or "x-autoreply" in headers or "x-autorespond" in headers
        or headers.get("precedence", "").lower() == "auto_reply"
    )


def reply_kind(headers: dict, sender: str, subject: str, preview: str, to: str | None, in_thread: bool) -> str | None:
    """What a reply says happened: 'success' | 'rejected' | 'acknowledged' | 'auto_reply' | None."""
    bounced = [a.strip().lower() for a in headers.get("x-failed-recipients", "").split(",")]
    if to and to.lower() in bounced:
        return "rejected"
    text = f"{subject}. {preview}"
    if sender.split("@")[0] in ("mailer-daemon", "postmaster"):
        # A bounce that names nobody is ours only in our own conversation; others are about other emails.
        return "rejected" if in_thread and not any(bounced) and _REFUSED.search(text.lower()) and not _DELAYED.search(text.lower()) else None
    meaning = reply_meaning(text)
    if meaning:
        return meaning
    return "auto_reply" if _labelled_automatic(headers) or is_no_reply(sender) else None


# A link in an email can lead anywhere; only pages about email settings count.
_EMAIL_URL = re.compile(r"unsub|subscription|opt[-_]?out|preference|email[-_]?settings|notification", re.I)
# Not just "subscribe": shops put a newsletter sign-up on every page.
_EMAIL_TEXT = re.compile(r"unsubscribed|unsubscribe from|no longer receive|opted out|(email|communication|subscription) preferences|email (topics|settings)", re.I)


def is_unsubscribe_page(url: str, text: str) -> bool:
    return bool(_EMAIL_URL.search(url) or _EMAIL_TEXT.search(text or ""))


# --- Reading the sender's page (desktop app only) ----------------------------
# The desktop shell sets this to open a URL in an in-app window. It is called as
# opener(url, title, on_page, on_closed, script) and returns the window (close(), show()),
# or None: `script` is JavaScript whose result
# is passed as on_page(url, text) about once a second; on_page returns True to
# close the window. on_closed() runs when the window closes.
in_app_opener = None

# No backslashes or line breaks in these scripts: the window library passes them through
# eval("...") without escaping those, so a "\n" becomes a syntax error on every page.
PAGE_TEXT = "(document.body ? document.body.innerText : '').slice(0, 50000)"
# Gmail announces actions in small notices marked role="alert" ("Unsubscribed from…",
# "You blocked…", "Message sent"). Reading only those, never the open email, avoids
# false matches on words inside someone's message. If Gmail hands over to the sender's
# own website, that page is read whole instead.
GMAIL_NOTICES = (
    "/(^|[.])google[.]com$/.test(location.hostname)"
    " ? Array.prototype.map.call(document.querySelectorAll('[role=alert]'),"
    " function (e) { return e.innerText; }).join(' | ')"
    " : " + PAGE_TEXT
)


def on_google(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "google.com" or host.endswith(".google.com")

_GMAIL_UNSUBSCRIBED = re.compile(r"\bunsubscribed\b|\bunsubscribe request sent\b")
_GMAIL_BLOCKED = re.compile(r"\bblocked\b|\b(reported|marked) as spam\b")  # not "unblocked"
_GMAIL_NOT = re.compile(r"\bnot\b|\bunable\b|n['’]t\b|\bfailed\b|\berror\b|\bsomething went wrong\b")


def gmail_result(notices: str) -> str | None:
    """'unsubscribed' | 'blocked' | None, from Gmail's own notices (one per line or " | ")."""
    for notice in re.split(r"\||\n", (notices or "").lower()):
        if _GMAIL_NOT.search(notice):
            continue
        if _GMAIL_UNSUBSCRIBED.search(notice):
            return "unsubscribed"
        if _GMAIL_BLOCKED.search(notice):
            return "blocked"
    return None


def email_target(mailto: str | None) -> tuple[str, str] | None:
    """(address, subject) of a List-Unsubscribe mailto, if it is safe to send for the user.

    The sender writes this, so only an address and a short subject are taken; a body, extra
    recipients and everything else are ignored."""
    parts = urllib.parse.urlsplit(mailto or "")
    if parts.scheme.lower() != "mailto":
        return None
    to = urllib.parse.unquote(parts.path).strip()  # tracking codes can be case-sensitive
    subject = dict(urllib.parse.parse_qsl(parts.query)).get("subject", "unsubscribe")
    subject = " ".join(subject.split())[:150] or "unsubscribe"
    return (to, subject) if _LIST_ADDRESS.fullmatch(to) and subject.isprintable() else None


_FAILED = (
    "post required", '"success":false', '"success": false', "forbidden", "access denied",
    "error", "failed", "invalid", "expired", "not found", "try again",
)
_SUCCEEDED = (
    "unsubscribed", "unsubscribe successful", '"success":true', '"success": true',
    "you have been removed", "you’ve been removed", "you've been removed",
    "no longer receive", "won't receive", "will not receive", "won’t receive",
    "opted out", "preferences have been updated", "preferences updated",
)


# "Removed from our mailing list", but not "removed from your basket".
_REMOVED = re.compile(r"removed from .{0,40}(list|newsletter|subscription|emails)")


def classify_page(text: str) -> str | None:
    """'success' | 'failed' | None (can't tell yet: a form to click, a challenge page...)."""
    text = (text or "").lower()
    succeeded = any(p in text for p in _SUCCEEDED) or bool(_REMOVED.search(text))
    failed = any(p in text for p in _FAILED)
    # "Failed to unsubscribe" mentions both; anything mixed stays undecided.
    if succeeded and not failed:
        return "success"
    if failed and not succeeded:
        return "failed"
    return None
