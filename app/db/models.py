"""Аддитивные модели нового приложения. Рабочая схема не изменяется автоматически."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.config import DEFAULT_MIN_SCORE


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """SQLite хранит ISO-8601; наружу всегда возвращается aware datetime UTC."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Дата должна содержать часовой пояс.")
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError("В базе обнаружена дата без часового пояса.")
        return result.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "lh_users"
    __table_args__ = (CheckConstraint("min_score BETWEEN 0 AND 100"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    subscription_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    min_score: Mapped[int] = mapped_column(default=DEFAULT_MIN_SCORE)
    geo_filter: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class Category(Base):
    __tablename__ = "lh_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserCategory(Base):
    __tablename__ = "lh_user_categories"
    user_id: Mapped[int] = mapped_column(ForeignKey("lh_users.id"), primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("lh_categories.id"), primary_key=True)


class SourceChat(Base):
    __tablename__ = "lh_source_chats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    access_hash: Mapped[int | None] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    geo: Mapped[str | None] = mapped_column(String(100))
    geo_guess: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ChatCandidate(Base):
    __tablename__ = "lh_chat_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','joined','failed','pending_approval','needs_captcha')"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    access_hash: Mapped[int | None] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64))
    invite_hash: Mapped[str | None] = mapped_column(String(255), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    participants: Mapped[int | None] = mapped_column(Integer)
    msgs_per_day: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    category_hint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    found_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Lead(Base):
    __tablename__ = "lh_leads"
    __table_args__ = (UniqueConstraint("source_chat_id", "message_id"), CheckConstraint("score BETWEEN 0 AND 100"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("lh_categories.id"))
    text: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(String(120))
    score: Mapped[int] = mapped_column(Integer)
    author_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    author_username: Mapped[str | None] = mapped_column(String(64))
    source_chat_id: Mapped[int] = mapped_column(ForeignKey("lh_source_chats.id"))
    message_id: Mapped[int] = mapped_column(Integer)
    message_link: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)


class LeadDelivery(Base):
    __tablename__ = "lh_lead_deliveries"
    __table_args__ = (UniqueConstraint("lead_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lh_leads.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("lh_users.id"))
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    message_id: Mapped[int] = mapped_column(Integer)


class LeadFeedback(Base):
    __tablename__ = "lh_lead_feedback"
    __table_args__ = (
        UniqueConstraint("lead_id", "user_id", "type"),
        CheckConstraint("type IN ('spam','contacted','responded')"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lh_leads.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("lh_users.id"))
    type: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AuthorBlacklist(Base):
    __tablename__ = "lh_author_blacklist"
    author_tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Keyword(Base):
    __tablename__ = "lh_keywords"
    __table_args__ = (UniqueConstraint("category_id", "word", "type"), CheckConstraint("type IN ('include','exclude')"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("lh_categories.id"))
    word: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(16))
    is_regex: Mapped[bool] = mapped_column(Boolean, default=False)


class Payment(Base):
    __tablename__ = "lh_payments"
    __table_args__ = (CheckConstraint("amount >= 0"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("lh_users.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
