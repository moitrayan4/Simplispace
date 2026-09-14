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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

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

    user: Mapped["User"] = relationship(back_populates="messages")
