from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    # Anchor the dotenv file to this backend package so launching uvicorn from
    # either the repository root or backend/ loads the same credentials.
    model_config = SettingsConfigDict(env_file=BACKEND_ENV_FILE, extra="ignore")

    google_client_id: str = ""
    google_client_secret: str = ""

    # Fernet key for encrypting OAuth tokens at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str = ""

    database_url: str = "sqlite:///./simplispace.db"
    frontend_origin: str = "http://localhost:5173"

    # How many months of metadata to sync (proposal: 6-12+).
    sync_months: int = 0  # 0 = whole mailbox; N = only the last N months

    # gmail.readonly is the only scope we ever request (proposal: minimal read-only).
    scopes: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]

    # AI classification is local-first and opt-in per account. The provider uses
    # the OpenAI Chat Completions API shape, so compatible hosted/local endpoints
    # can be configured without changing application code.
    ai_api_key: str = ""
    # Groq defaults let a user add only AI_API_KEY to enable the documented setup.
    ai_model: str = "openai/gpt-oss-20b"
    ai_base_url: str = "https://api.groq.com/openai/v1"
    ai_timeout_seconds: float = 12.0
    ai_confidence_threshold: float = 0.75
    ai_ambiguity_margin: int = 10

    @field_validator("ai_model", mode="before")
    @classmethod
    def default_ai_model_when_blank(cls, value):
        if isinstance(value, str):
            return value.strip() or "openai/gpt-oss-20b"
        return value

    @field_validator("ai_base_url", mode="before")
    @classmethod
    def default_ai_url_when_blank(cls, value):
        if isinstance(value, str):
            return value.strip() or "https://api.groq.com/openai/v1"
        return value


settings = Settings()
