"""Sender logos: the brand site's own icon, fetched once and cached on disk.

Only the sender's domain is looked up (never anything from an email). Tries the
site's own /favicon.ico, Google's favicon service (with and without www.), then
DuckDuckGo's icon service. Misses are cached too, so a domain without an icon isn't
retried on every page view; being offline doesn't count as a miss.
"""
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

from .scoring import root_domain
from .unsubscribe import _is_public_host

CACHE_DIR = Path("icons")  # beside the database (the app's working directory)
# Recheck missing icons periodically. A temporary upstream outage should not hide
# logos for a week after one failed lookup.
RETRY_MISSES_AFTER = 60 * 60
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


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """A sender's site may redirect, but not to this computer or the local network."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme != "https" or not parts.hostname or not _is_public_host(parts.hostname):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(request):
    tls = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.build_opener(_PublicRedirects, urllib.request.HTTPSHandler(context=tls)).open(request, timeout=5)


def _google(site: str):
    # `default=404` prevents Google's generic globe icon being mistaken for a
    # sender logo, allowing the next source (or letter fallback) to be used.
    return _fetch(f"https://www.google.com/s2/favicons?domain={site}&sz=64&default=404", strict=True)


def _fetch(url: str, strict: bool = False) -> tuple[bytes, str] | None:
    request = urllib.request.Request(url, headers={"User-Agent": "Simplispace"})
    try:
        with _open(request) as response:
            kind = response.headers.get_content_type()
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if strict and (e.code == 429 or e.code >= 500):
            raise _Offline from e  # the service is having trouble; not an answer
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        # A sender's own site may simply not exist, so only an icon service being
        # unreachable (strict) means we're offline.
        if strict:
            raise _Offline from e
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
    # Versioned cache names discard older generic globe images and misses.
    hit, miss = CACHE_DIR / f"{root}.v2.img", CACHE_DIR / f"{root}.v2.none"
    if hit.exists():
        kind, _, body = hit.read_bytes().partition(b"\n")
        return body, kind.decode()
    if miss.exists() and time.time() - miss.stat().st_mtime < RETRY_MISSES_AFTER:
        return None

    found = _fetch(f"https://{root}/favicon.ico") if _is_public_host(root) else None
    offline = False
    try:
        found = found or _google(root) or _google(f"www.{root}")
    except _Offline:
        offline = True
    found = found or _fetch(f"https://icons.duckduckgo.com/ip3/{root}.ico")
    if not found:
        if not offline:  # no internet isn't "no icon": don't remember it, try again next time
            miss.touch()
        return None
    body, kind = found
    hit.write_bytes(kind.encode() + b"\n" + body)
    miss.unlink(missing_ok=True)
    return body, kind
