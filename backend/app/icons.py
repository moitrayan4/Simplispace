"""Sender logos: the brand site's own icon, fetched once and cached on disk.

Only the sender's domain is looked up (never anything from an email). Tries
Google's favicon service (with and without www.), DuckDuckGo's icon service,
then the site's own /favicon.ico. Misses are cached too, so a domain without an icon isn't retried
on every page view.
"""
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .scoring import root_domain
from .unsubscribe import _is_public_host

CACHE_DIR = Path("icons")  # beside the database (the app's working directory)
RETRY_MISSES_AFTER = 7 * 24 * 3600
MAX_BYTES = 200_000
_VALID = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
# Mail-only domains whose brand site has the logo.
# ponytail: hand-kept list; add a line when a well-known sender shows a letter.
_BRAND_SITE = {
    "redditmail.com": "reddit.com",
    "microsoftonline.com": "microsoft.com",
    "shopifyemail.com": "shopify.com",
    "greenhouse-mail.io": "greenhouse.io",
    "e-redbus.in": "redbus.in",
    "facebookmail.com": "facebook.com",
    "linkedinmail.com": "linkedin.com",
}
# Bulk-email services send for many companies; their logo would be the wrong brand.
_EMAIL_SERVICES = {
    "brevosend.com", "sendinblue.com", "sendgrid.net", "mcsv.net", "rsgsv.net", "mailchimpapp.net",
    "amazonses.com", "mailgun.org", "sparkpostmail.com", "exacttarget.com", "hubspotemail.net",
    "mailjet.com", "mandrillapp.com", "postmarkapp.com",
}


class _Offline(Exception):
    """Couldn't reach the icon service at all, so this isn't a real "no icon"."""


def _google(site: str):
    return _fetch(strict=True, url=
        "https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON"
        f"&fallback_opts=TYPE,SIZE,URL&url=https://{site}&size=64"
    )


def _fetch(url: str, strict: bool = False) -> tuple[bytes, str] | None:
    request = urllib.request.Request(url, headers={"User-Agent": "Simplispace"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            kind = response.headers.get_content_type()
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        if strict:
            raise _Offline
        return None
    # Raster images only: an SVG served from the app's own origin could carry script.
    if not kind.startswith("image/") or "svg" in kind or not body or len(body) > MAX_BYTES:
        return None
    return body, kind


def get_icon(domain: str) -> tuple[bytes, str] | None:
    """(image bytes, content type) for a sender domain, or None if it has no icon."""
    root = root_domain(domain)
    root = _BRAND_SITE.get(root, root)
    if root in _EMAIL_SERVICES or not _VALID.match(root):
        return None
    CACHE_DIR.mkdir(exist_ok=True)
    hit, miss = CACHE_DIR / f"{root}.img", CACHE_DIR / f"{root}.none"
    if hit.exists():
        kind, _, body = hit.read_bytes().partition(b"\n")
        return body, kind.decode()
    if miss.exists() and time.time() - miss.stat().st_mtime < RETRY_MISSES_AFTER:
        return None

    try:
        found = (
            _google(root)
            or _google(f"www.{root}")
            or _fetch(f"https://icons.duckduckgo.com/ip3/{root}.ico")
        )
    except _Offline:
        return None  # no internet: don't remember it as "no icon", try again next time
    if not found and _is_public_host(root):  # never fetch from local/private addresses
        found = _fetch(f"https://{root}/favicon.ico")
    if not found:
        miss.touch()
        return None
    body, kind = found
    hit.write_bytes(kind.encode() + b"\n" + body)
    miss.unlink(missing_ok=True)
    return body, kind
