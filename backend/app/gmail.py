import json

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from .config import settings


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def make_flow(state: str | None = None) -> Flow:
    flow = Flow.from_client_config(
        _client_config(), scopes=settings.scopes, state=state
    )
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def authorization_url() -> tuple[str, str, str]:
    flow = make_flow()
    url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    # PKCE verifier must survive to the token exchange (separate request).
    return url, state, flow.code_verifier


def exchange_code(code: str, state: str, code_verifier: str | None) -> Credentials:
    flow = make_flow(state=state)
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    return flow.credentials


def credentials_to_json(creds: Credentials) -> str:
    return creds.to_json()


def credentials_from_json(data: str) -> Credentials:
    return Credentials.from_authorized_user_info(json.loads(data), settings.scopes)


def build_gmail(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def user_email(creds: Credentials) -> str:
    profile = build_gmail(creds).users().getProfile(userId="me").execute()
    return profile["emailAddress"]
