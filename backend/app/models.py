from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    # OAuth credentials JSON, Fernet-encrypted (never stored in clear).
    encrypted_token: Mapped[str] = mapped_column(Text)
    # JSON list of domains the user has sent mail to (reply signal). ponytail:
    # flat column instead of a join table; a set of domains is small.
    replied_domains: Mapped[str] = mapped_column(Text, default="[]")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_page: Mapped[int] = mapped_column(Integer, default=0)
    sync_page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Gmail historyId the inbox is fully indexed up to; later syncs fetch only changes.
    history_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # historyId captured when the current full sync started; becomes history_id when it finishes.
    history_start: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Logged out = False: the account's senders and choices stay on this computer and
    # come back on the next sign-in. Only "Delete account" removes them.
    signed_in: Mapped[bool] = mapped_column(Boolean, default=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("user_id", "gmail_id", name="uq_user_message"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    gmail_id: Mapped[str] = mapped_column(String, index=True)

    sender_email: Mapped[str] = mapped_column(String, default="")
    sender_domain: Mapped[str] = mapped_column(String, index=True, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    labels: Mapped[str] = mapped_column(Text, default="")  # comma-joined label ids
    snippet: Mapped[str] = mapped_column(Text, default="")
    list_unsubscribe: Mapped[str] = mapped_column(Text, default="")  # header value or ""
    is_unread: Mapped[bool] = mapped_column(Boolean, default=False)
    logo_url: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User"] = relationship(back_populates="messages")


class Decision(Base):
    """User's Keep/Unsubscribe choice per service, plus the confirmed action."""

    __tablename__ = "decisions"
    __table_args__ = (UniqueConstraint("user_id", "domain", name="uq_user_decision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    domain: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)  # "keep" | "unsubscribe"
    # Set only after the user confirmed an unsubscribe action.
    unsubscribed_via: Mapped[str | None] = mapped_column(String, nullable=True)  # one_click | link | mailto
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # What the sender's page said when it was opened inside the app:
    # in_progress | success | failed | unclear (None when opened outside the app).
    page_result: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HealthSnapshot(Base):
    """One inbox health score per user per day, so the trend can be shown."""

    __tablename__ = "health_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[str] = mapped_column(String)  # YYYY-MM-DD (UTC)
    score: Mapped[int] = mapped_column(Integer)
