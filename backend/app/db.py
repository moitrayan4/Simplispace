from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
# timeout = busy_timeout: wait for locks instead of erroring during long syncs.
_connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_wal(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # readers don't block writer
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def ensure_schema():
    """Add lightweight local columns when upgrading an existing SQLite database."""
    # Minimal retention: snippets were stored by older builds but never used.
    with engine.begin() as connection:
        # Every per-sender lookup filters user + domain and sorts/compares by date.
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_messages_user_domain_date "
            "ON messages (user_id, sender_domain, date)"
        ))
        connection.execute(text("UPDATE messages SET snippet = '' WHERE snippet <> ''"))
        # The review list was removed: drop "unsubscribe" picks that were never confirmed.
        connection.execute(
            text("DELETE FROM decisions WHERE decision = 'unsubscribe' AND unsubscribed_at IS NULL")
        )
    if not _is_sqlite:
        return
    inspector = inspect(engine)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    message_columns = {column["name"] for column in inspector.get_columns("messages")}
    with engine.begin() as connection:
        if "sync_page" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN sync_page INTEGER DEFAULT 0"))
        if "sync_page_token" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN sync_page_token TEXT"))
        if "signed_in" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN signed_in BOOLEAN DEFAULT 1"))
        for column in ("history_id", "history_start"):
            if column not in user_columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {column} TEXT"))
        decision_columns = {column["name"] for column in inspector.get_columns("decisions")}
        if "page_result" not in decision_columns:
            connection.execute(text("ALTER TABLE decisions ADD COLUMN page_result TEXT"))
        if "logo_url" not in message_columns:
            connection.execute(text("ALTER TABLE messages ADD COLUMN logo_url TEXT DEFAULT ''"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
