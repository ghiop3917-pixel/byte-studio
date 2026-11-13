"""Аддитивные модели нового приложения. Рабочая схема не изменяется автоматически."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


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


class RawMessage(Base):
    __tablename__ = "lh_raw_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id"),
        Index("ix_lh_raw_author_hash", "author_tg_id", "text_hash"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_chat_id: Mapped[int] = mapped_column(ForeignKey("lh_source_chats.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    source_title: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str] = mapped_column(Text)
    message_link: Mapped[str | None] = mapped_column(Text)
    author_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    author_username: Mapped[str | None] = mapped_column(String(64))
    author_name: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(32))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    category_hint: Mapped[str | None] = mapped_column(String(64))
    delivery_mode: Mapped[str] = mapped_column(String(16), default="dry_run")


class IngestJob(Base):
    __tablename__ = "lh_ingest_jobs"
    __table_args__ = (CheckConstraint("status IN ('pending','processing','completed','failed','retry_wait')"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_id: Mapped[int] = mapped_column(ForeignKey("lh_raw_messages.id"), unique=True)
    phase: Mapped[str] = mapped_column(String(32), default="prefilter", index=True)
    fanout_after: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    token: Mapped[str | None] = mapped_column(String(32))
    errors: Mapped[str] = mapped_column(Text, default="[]")


class DedupClaim(Base):
    __tablename__ = "lh_dedup_claims"
    __table_args__ = (UniqueConstraint("text_hash", "author_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    author_key: Mapped[str] = mapped_column(String(100))
    raw_id: Mapped[int] = mapped_column(ForeignKey("lh_raw_messages.id"))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class SourceRuntime(Base):
    __tablename__ = "lh_source_runtime"
    source_id: Mapped[int] = mapped_column(ForeignKey("lh_source_chats.id"), primary_key=True)
    history_message_id: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    last_success: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)


class ReaderState(Base):
    __tablename__ = "lh_reader_state"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    detail: Mapped[str] = mapped_column(Text, default="")


class DiscoveryRun(Base):
    __tablename__ = "lh_discovery_runs"
    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    query_index: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class JoinJob(Base):
    __tablename__ = "lh_join_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending','processing','retry_wait','completed','failed','ambiguous')"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("lh_chat_candidates.id"), unique=True)
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    token: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)


class JoinAttempt(Base):
    __tablename__ = "lh_join_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("lh_join_jobs.id"), index=True)
    token: Mapped[str] = mapped_column(String(32), unique=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    outcome: Mapped[str] = mapped_column(String(64), default="started")


class ClassificationCache(Base):
    __tablename__ = "lh_classification_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class DeliveryJob(Base):
    __tablename__ = "lh_delivery_jobs"
    __table_args__ = (
        UniqueConstraint("lead_id", "user_id"),
        CheckConstraint(
            "status IN ('pending','processing','retry_wait','completed','failed','ambiguous','suppressed','dry_run')"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lh_leads.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("lh_users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    token: Mapped[str | None] = mapped_column(String(32))
    message_id: Mapped[int | None] = mapped_column(Integer)
    errors: Mapped[str] = mapped_column(Text, default="[]")


class LeadDraft(Base):
    __tablename__ = "lh_lead_drafts"
    __table_args__ = (UniqueConstraint("lead_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lh_leads.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("lh_users.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    text: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CompanyScan(Base):
    __tablename__ = "lh_company_scans"
    __table_args__ = (UniqueConstraint("provider", "city", "niche", "requested_by"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    city: Mapped[str] = mapped_column(String(64))
    niche: Mapped[str] = mapped_column(String(100), default="")
    queries: Mapped[str] = mapped_column(Text, default="[]")
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    requested_by: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    query_index: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int] = mapped_column(Integer, default=1)
    links: Mapped[str] = mapped_column(Text, default="[]")
    seen: Mapped[str] = mapped_column(Text, default="[]")
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    issues: Mapped[str] = mapped_column(Text, default="[]")
    link_index: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CompanyRecord(Base):
    __tablename__ = "lh_company_records"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(String(64), index=True)
    address: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    phones: Mapped[str] = mapped_column(Text)
    telegram: Mapped[str] = mapped_column(Text)
    max_links: Mapped[str] = mapped_column(Text)
    websites: Mapped[str] = mapped_column(Text)
    website_state: Mapped[str] = mapped_column(String(16), index=True)
    in_scope: Mapped[bool] = mapped_column(Boolean, default=False)
    possible_duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("lh_company_records.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    identity_key: Mapped[str | None] = mapped_column(String(64), index=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CompanySnapshot(Base):
    __tablename__ = "lh_company_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("lh_company_records.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class CompanySearchResult(Base):
    __tablename__ = "lh_company_search_results"
    __table_args__ = (UniqueConstraint("scan_id", "record_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("lh_company_scans.id"), index=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("lh_company_records.id"), index=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
