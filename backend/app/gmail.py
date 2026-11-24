import json
import os
import subprocess
import sys
import webbrowser

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import settings


class _SystemBrowser(webbrowser.BaseBrowser):
    """Launch Google OAuth through the operating system's default browser."""

    def open(self, url, new=0, autoraise=True):
        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            elif os.name == "nt":
                os.startfile(url)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            return True
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(
                "Could not open the system browser for Google sign-in. "
                "Set a default browser and try again."
            ) from error


webbrowser.register("simplispace-system", None, _SystemBrowser())


def _client_config() -> dict:
    return {
        "installed": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def authorize_desktop() -> Credentials:
    """Authorize in the system browser and receive the result on a loopback port."""
    flow = InstalledAppFlow.from_client_config(
        _client_config(), scopes=settings.scopes, autogenerate_code_verifier=True
    )
    return flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=True,
        browser="simplispace-system",
        redirect_uri_trailing_slash=False,
        authorization_prompt_message="Opening Google sign-in in your browser…",
        success_message="Google sign-in is complete. You can return to Simplispace.",
        timeout_seconds=300,
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )


def credentials_to_json(creds: Credentials) -> str:
    return creds.to_json()


def credentials_from_json(data: str) -> Credentials:
    return Credentials.from_authorized_user_info(json.loads(data), settings.scopes)


def build_gmail(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def user_email(creds: Credentials) -> str:
    profile = build_gmail(creds).users().getProfile(userId="me").execute()
    return profile["emailAddress"]
