import base64
import json
import os
import secrets
import time
from email.message import EmailMessage
from email.policy import SMTP

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token as google_id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import settings


# Google lets a Desktop client return to any localhost address, so the app's own API
# receives the sign-in and nothing needs registering in the Cloud Console.
REDIRECT_URI = "http://localhost:8000/auth/callback"


def _client_config() -> dict:
    return {
        "installed": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


# Google lets users untick permissions, and may list more than was asked for. The library
# treats any difference as a crash; exchange_code checks for what is needed instead.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class GmailNotAllowed(RuntimeError):
    """The user signed in but didn't tick the Gmail permission."""


# Lets the app send, but not read, change or delete mail. Asked for only on first use.
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def login_scopes(send: bool = False) -> list[str]:
    # "openid" lets the AI server check who is asking. It gives no access to mail, and
    # is only requested when this copy of the app has an AI server.
    openid = ["openid"] if settings.ai_server_url.strip() else []
    return openid + settings.scopes + ([SEND_SCOPE] if send else [])


def make_flow(state: str | None = None, send: bool = False) -> Flow:
    flow = Flow.from_client_config(
        _client_config(), scopes=login_scopes(send), state=state
    )
    flow.redirect_uri = REDIRECT_URI
    return flow


def authorization_url(send: bool = False, email: str | None = None) -> tuple[str, str, str]:
    flow = make_flow(send=send)
    hint = {"login_hint": email} if email else {}
    url, state = flow.authorization_url(access_type="offline", prompt="consent", **hint)
    # PKCE verifier must survive to the token exchange (separate request).
    return url, state, flow.code_verifier


def exchange_code(code: str, state: str, code_verifier: str | None) -> Credentials:
    flow = make_flow(state=state)
    flow.code_verifier = code_verifier
    token = flow.fetch_token(code=code)
    granted = token.get("scope") or []
    granted = granted.split() if isinstance(granted, str) else list(granted)
    if not set(settings.scopes) <= set(granted):
        raise GmailNotAllowed
    # Save only what this app uses and was really given, so later refreshes are accepted.
    saved = json.loads(flow.credentials.to_json())
    saved["scopes"] = [s for s in login_scopes(send=True) if s in granted]
    return credentials_from_json(json.dumps(saved))


def credentials_to_json(creds: Credentials) -> str:
    return creds.to_json()


def credentials_from_json(data: str) -> Credentials:
    # Scopes come from the saved sign-in: refreshing an older one with scopes it was
    # never granted would make Google refuse it.
    return Credentials.from_authorized_user_info(json.loads(data))


def has_openid(creds: Credentials) -> bool:
    return "openid" in (creds.scopes or [])


def can_send(creds: Credentials) -> bool:
    return SEND_SCOPE in (creds.scopes or [])


# Marks our own emails, so the sync doesn't count them as the user writing to a sender.
SENT_MARK = "X-Simplispace"


def send_email(creds: Credentials, to: str, subject: str, body: str) -> dict:
    """Send, and return Gmail's id and thread for the email so a sync can follow it."""
    mark = f"unsubscribe-{secrets.token_hex(8)}"
    # Refuses line breaks in headers. Long lines: a list's tracking address must not be folded.
    message = EmailMessage(policy=SMTP.clone(max_line_length=998))
    message["To"], message["Subject"], message[SENT_MARK] = to, subject, mark
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    messages = build_gmail(creds).users().messages()
    try:
        return messages.send(userId="me", body={"raw": raw}).execute() or {}
    except Exception as e:
        # The answer got lost, but the email may have gone: look in Sent rather than guess.
        if isinstance(e, HttpError) and e.resp.status < 500:
            raise
        found = _find_sent(creds, to, mark)
        if not found:
            raise
        return found


def _find_sent(creds: Credentials, to: str, mark: str) -> dict | None:
    try:
        messages = build_gmail(creds).users().messages()
        found = messages.list(userId="me", q=f"in:sent to:{to} newer_than:1d", maxResults=5).execute()
        for item in found.get("messages", []):
            full = {**item, **messages.get(userId="me", id=item["id"], format="metadata", metadataHeaders=[SENT_MARK]).execute()}
            if any(h["value"] == mark for h in full.get("payload", {}).get("headers", []) if h["name"].lower() == SENT_MARK.lower()):
                return full
    except Exception:
        pass
    return None


class SignInNeeded(RuntimeError):
    """This sign-in can't prove who the user is; signing in again fixes it."""


# account email -> (token, expiry). Memory only; the tokens last about an hour.
_id_tokens: dict[str, tuple[str, float]] = {}


def forget_id_token(email: str) -> None:
    _id_tokens.pop(email, None)


def id_token(email: str, creds: Credentials) -> str:
    """A Google ID token: says who the user is and cannot be used to read mail. The
    Gmail access token is never sent to the AI server."""
    if not has_openid(creds):
        raise SignInNeeded("Log out and sign in again to use AI. Your senders and choices are kept.")
    token, expires = _id_tokens.get(email, ("", 0.0))
    if token and time.time() < expires - 120:
        return token
    try:
        creds.refresh(Request())
        claims = google_id_token.verify_oauth2_token(
            creds.id_token, Request(), creds.client_id, clock_skew_in_seconds=60
        )
    except (GoogleAuthError, ValueError, TypeError) as e:
        raise SignInNeeded("Couldn't confirm your Google sign-in. Check your connection, then sign in again.") from e
    _id_tokens[email] = (creds.id_token, float(claims["exp"]))
    return creds.id_token


def build_gmail(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def user_email(creds: Credentials) -> str:
    profile = build_gmail(creds).users().getProfile(userId="me").execute()
    return profile["emailAddress"]
