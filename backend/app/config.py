from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/callback"

    # Fernet key for encrypting OAuth tokens at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str = ""

    database_url: str = "sqlite:///./simplispace.db"
    frontend_origin: str = "http://localhost:5173"

    # How many months of metadata to sync (proposal: 6-12+).
    sync_months: int = 12

    # gmail.readonly is the only scope we ever request (proposal: minimal read-only).
    scopes: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]


settings = Settings()
