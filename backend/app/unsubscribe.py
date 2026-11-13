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
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta

_TARGET = re.compile(r"<([^>]+)>")
_ONE_CLICK_BODY = b"List-Unsubscribe=One-Click"


def parse(list_unsubscribe: str, list_unsubscribe_post: str = "") -> dict:
    """Split List-Unsubscribe into the first web link and first mailto target."""
    link = mailto = None
    for target in _TARGET.findall(list_unsubscribe or ""):
        target = target.strip()
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
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Simplispace"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise ValueError(f"The sender answered with HTTP {response.status}.")
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            return  # accepted, then redirected to a confirmation page we don't follow
        raise ValueError(f"The sender answered with HTTP {e.code}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError("Could not reach the sender's unsubscribe server.") from e


def open_external(url: str) -> None:
    """Open a page or mail draft for the user: in a Simplispace window when running as
    the desktop app, otherwise (development in a browser) in the default browser/mail app."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("https", "http", "mailto"):
        raise ValueError("Unsupported unsubscribe link.")
    if in_app_opener and scheme in ("https", "http"):
        in_app_opener(url, "Simplispace", lambda *_: False, lambda: None, PAGE_TEXT)
        return
    if not webbrowser.open(url):
        raise ValueError("Could not open your browser or mail app.")


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
    if page_result == "unclear":
        return "needs_answer"
    if last_synced_at and last_synced_at >= unsubscribed_at + CONFIRM_AFTER:
        return "confirmed"
    return "checking"


# --- Reading the sender's page (desktop app only) ----------------------------
# The desktop shell sets this to open a URL in an in-app window. It is called as
# opener(url, title, on_page, on_closed, script): `script` is JavaScript whose result
# is passed as on_page(url, text) about once a second; on_page returns True to
# close the window. on_closed() runs when the window closes.
in_app_opener = None

PAGE_TEXT = "document.body ? document.body.innerText : ''"
# Gmail announces actions in small notices marked role="alert" ("Unsubscribed from…",
# "You blocked…", "Message sent"). Reading only those, never the open email, avoids
# false matches on words inside someone's message.
GMAIL_NOTICES = (
    "Array.prototype.map.call(document.querySelectorAll('[role=alert]'),"
    " function (e) { return e.innerText; }).join('\\n')"
)
_GMAIL_DONE = ("unsubscribed", "blocked", "reported as spam", "marked as spam", "message sent")


def gmail_done(notices: str) -> bool:
    text = (notices or "").lower()
    return any(p in text for p in _GMAIL_DONE)


def gmail_compose_url(mailto: str, account: str) -> str:
    """Gmail web compose window pre-filled from a mailto: unsubscribe address."""
    parts = urllib.parse.urlsplit(mailto)
    query = dict(urllib.parse.parse_qsl(parts.query))
    fields = {
        "authuser": account,
        "view": "cm",
        "fs": "1",
        "to": urllib.parse.unquote(parts.path),
        "su": query.get("subject", "Unsubscribe"),
        "body": query.get("body", "Please unsubscribe me from this mailing list."),
    }
    return "https://mail.google.com/mail/?" + urllib.parse.urlencode(fields)

_FAILED = (
    "post required", '"success":false', '"success": false', "forbidden", "access denied",
    "error", "failed", "invalid", "expired", "not found", "try again",
)
_SUCCEEDED = (
    "unsubscribed", "unsubscribe successful", '"success":true', '"success": true',
    "you have been removed", "you’ve been removed", "you've been removed", "removed from",
    "no longer receive", "won't receive", "will not receive", "won’t receive",
    "opted out", "preferences have been updated", "preferences updated",
)


def classify_page(text: str) -> str | None:
    """'success' | 'failed' | None (can't tell yet: a form to click, a challenge page...)."""
    text = (text or "").lower()
    succeeded = any(p in text for p in _SUCCEEDED)
    failed = any(p in text for p in _FAILED)
    # "Failed to unsubscribe" mentions both; anything mixed stays undecided.
    if succeeded and not failed:
        return "success"
    if failed and not succeeded:
        return "failed"
    return None
