"""Долговечный приём: оригинал и задание сохраняются одной транзакцией."""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import Settings
from app.db.models import (
    AuthorBlacklist,
    Category,
    DedupClaim,
    IngestJob,
    Keyword,
    RawMessage,
    ReaderState,
    SourceChat,
    SourceRuntime,
    utc_now,
)
from app.db.session import Database
from app.pipeline.prefilter import KeywordRule, prefilter
from app.utils.links import message_link
from app.utils.text import text_hash


@dataclass(frozen=True)
class IncomingMessage:
    chat_id: int
    message_id: int
    text: str
    published_at: datetime | None = None
    author_tg_id: int | None = None
    author_username: str | None = None
    author_name: str | None = None
    metadata: dict | None = None


@dataclass(frozen=True)
class SourceInfo:
    id: int
    chat_id: int
    title: str
    username: str | None
    access_hash: int | None


@dataclass(frozen=True)
class JobClaim:
    id: int
    raw_id: int
    token: str
    attempts: int


class Ingestion:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def active_sources(self) -> tuple[SourceInfo, ...]:
        async with self.database.session() as session:
            rows = (
                await session.scalars(select(SourceChat).where(SourceChat.is_active.is_(True)).order_by(SourceChat.id))
            ).all()
            return tuple(SourceInfo(row.id, row.tg_chat_id, row.title, row.username, row.access_hash) for row in rows)

    async def seed_keywords(self) -> None:
        from app.db.repo import Repository

        async with self.database.session() as session:
            categories = await Repository(session, self.settings).seed_categories()
            for category in categories:
                for kind, words in (
                    ("include", self.settings.default_keywords.get(category.slug, ())),
                    ("exclude", self.settings.default_exclude_keywords),
                ):
                    for word in words:
                        query = select(Keyword.id).where(
                            Keyword.category_id == category.id, Keyword.word == word, Keyword.type == kind
                        )
                        if await session.scalar(query) is None:
                            session.add(
                                Keyword(category_id=category.id, word=word, type=kind, is_regex=False, is_active=True)
                            )

    async def ingest(self, source_id: int, message: IncomingMessage, *, history: bool = False) -> int | None:
        if message.message_id <= 0:
            raise ValueError("Некорректный ID сообщения.")
        async with self.database.session() as session:
            source = await session.get(SourceChat, source_id)
            if source is None or not source.is_active:
                return None
            if source.tg_chat_id != message.chat_id:
                raise ValueError("ID чата не совпадает с источником.")
            raw = await session.scalar(
                select(RawMessage).where(
                    RawMessage.chat_id == message.chat_id, RawMessage.message_id == message.message_id
                )
            )
            if raw is None:
                metadata = message.metadata or {}
                raw = RawMessage(
                    source_chat_id=source_id,
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    source_title=source.title,
                    original_text=message.text,
                    published_at=message.published_at,
                    author_tg_id=message.author_tg_id,
                    author_username=message.author_username,
                    author_name=message.author_name,
                    message_link=message_link(
                        message.chat_id, source.username, message.message_id, metadata.get("thread_id")
                    ),
                    payload=json.dumps(metadata, ensure_ascii=False),
                    status="pending",
                    delivery_mode="dry_run"
                    if self.settings.dry_run
                    else "admin_only"
                    if self.settings.admin_only_delivery
                    else "normal",
                )
                session.add(raw)
                await session.flush()
                session.add(IngestJob(raw_id=raw.id, phase="prefilter", status="pending"))
            if history:
                state = await session.get(SourceRuntime, source_id)
                if state is None:
                    state = SourceRuntime(source_id=source_id, history_message_id=0)
                    session.add(state)
                state.history_message_id = max(state.history_message_id or 0, message.message_id)
            return raw.id

    async def recover(self, now: datetime | None = None) -> int:
        now = now or utc_now()
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(IngestJob).where(IngestJob.status == "processing", IngestJob.lease_until < now)
                )
            ).all()
            for job in rows:
                job.status = "failed" if job.attempts >= self.settings.ingest_max_attempts else "retry_wait"
                job.due_at = now
                job.token = None
                job.lease_until = None
                job.errors = json.dumps([*json.loads(job.errors), {"at": now.isoformat(), "error": "lease_expired"}])
                if job.status == "failed":
                    (await session.get(RawMessage, job.raw_id)).status = "failed"
            return len(rows)

    async def claim(self) -> JobClaim | None:
        now = utc_now()
        async with self.database.session() as session:
            query = (
                select(IngestJob)
                .where(
                    IngestJob.phase == "prefilter",
                    IngestJob.status.in_(("pending", "retry_wait")),
                    IngestJob.due_at <= now,
                    IngestJob.attempts < self.settings.ingest_max_attempts,
                )
                .order_by(IngestJob.id)
                .limit(1)
            )
            job = await session.scalar(query)
            if job is None:
                return None
            job.status = "processing"
            job.token = uuid.uuid4().hex
            job.attempts += 1
            job.lease_until = now + timedelta(seconds=self.settings.ingest_lease_seconds)
            return JobClaim(job.id, job.raw_id, job.token, job.attempts)

    async def retry(self, claim: JobClaim, error: Exception) -> None:
        now = utc_now()
        async with self.database.session() as session:
            job = await session.get(IngestJob, claim.id)
            if job is None or job.status != "processing" or job.token != claim.token:
                return
            job.status = "failed" if job.attempts >= self.settings.ingest_max_attempts else "retry_wait"
            job.due_at = now + timedelta(
                seconds=min(
                    self.settings.retry_initial_seconds * 2 ** (job.attempts - 1), self.settings.retry_max_seconds
                )
            )
            job.errors = json.dumps([*json.loads(job.errors), {"at": now.isoformat(), "error": type(error).__name__}])
            job.token = None
            job.lease_until = None
            if job.status == "failed":
                (await session.get(RawMessage, job.raw_id)).status = "failed"
        logging.error("ingest %s: %s", claim.id, type(error).__name__)

    async def process_one(self) -> bool:
        claim = await self.claim()
        if claim is None:
            return False
        try:
            async with self.database.session() as session:
                job = await session.get(IngestJob, claim.id)
                if job.token != claim.token or job.status != "processing":
                    return True
                raw = await session.get(RawMessage, claim.raw_id)
                source = await session.get(SourceChat, raw.source_chat_id)
                rows = (
                    await session.execute(
                        select(Category.slug, Keyword.word, Keyword.type, Keyword.is_regex)
                        .join(Keyword, Keyword.category_id == Category.id)
                        .where(Keyword.is_active.is_(True), Category.is_active.is_(True))
                    )
                ).all()
                rules = tuple(KeywordRule(*row) for row in rows)
                metadata = json.loads(raw.payload)
                result = prefilter(raw.original_text, metadata, rules, self.settings)
                blocked = (
                    raw.author_tg_id is not None and await session.get(AuthorBlacklist, raw.author_tg_id) is not None
                )
                raw.text_hash = text_hash(raw.original_text)
                raw.category_hint = result.category_hint
                raw.status = "candidate" if result.accepted else "filtered"
                raw.reason = result.reason
                if blocked or not source.is_active:
                    raw.status = "filtered"
                    raw.reason = "blacklist" if blocked else "source_disabled"
                if raw.status == "candidate":
                    key = (
                        f"user:{raw.author_tg_id}"
                        if raw.author_tg_id is not None
                        else f"unknown:{raw.chat_id}:{raw.message_id}"
                    )
                    dedup = await session.scalar(
                        select(DedupClaim).where(DedupClaim.text_hash == raw.text_hash, DedupClaim.author_key == key)
                    )
                    observed = raw.published_at or raw.received_at
                    window = timedelta(hours=self.settings.dedup_window_hours)
                    # История разных групп может поступать не по порядку. Один последний
                    # DedupClaim недостаточен: проверяем сохранённые оригиналы в окне.
                    published = func.coalesce(RawMessage.published_at, RawMessage.received_at)
                    previous_raw = None
                    if raw.author_tg_id is not None:
                        previous_raw = await session.scalar(
                            select(RawMessage)
                            .where(
                                RawMessage.id != raw.id,
                                RawMessage.text_hash == raw.text_hash,
                                RawMessage.author_tg_id == raw.author_tg_id,
                                RawMessage.status == "candidate",
                                published >= observed - window,
                                published <= observed + window,
                            )
                            .order_by(RawMessage.id)
                            .limit(1)
                        )
                    if previous_raw is not None:
                        raw.status = "duplicate"
                        raw.reason = f"duplicate:{previous_raw.id}"
                    if dedup is not None and dedup.raw_id != raw.id:
                        original = await session.get(RawMessage, dedup.raw_id)
                        previous = original.published_at or original.received_at
                        if abs(observed - previous) <= window:
                            raw.status = "duplicate"
                            raw.reason = f"duplicate:{original.id}"
                        elif observed >= previous:
                            dedup.raw_id = raw.id
                            dedup.expires_at = observed + window
                    elif dedup is None:
                        session.add(
                            DedupClaim(
                                text_hash=raw.text_hash, author_key=key, raw_id=raw.id, expires_at=observed + window
                            )
                        )
                job.token = None
                job.lease_until = None
                if raw.status == "candidate":
                    job.phase = "classifier"
                    job.status = "pending"
                    job.attempts = 0
                    job.due_at = utc_now()
                    logging.info("Кандидат %s сохранён, режим %s; отправки нет.", raw.id, raw.delivery_mode)
                else:
                    job.status = "completed"
            return True
        except Exception as error:
            await self.retry(claim, error)
            return True

    async def requeue(self, raw_id: int) -> None:
        async with self.database.session() as session:
            job = await session.scalar(select(IngestJob).where(IngestJob.raw_id == raw_id))
            if job is None or job.status == "processing":
                raise ValueError("Задание отсутствует или уже обрабатывается.")
            job.phase = "prefilter"
            job.status = "pending"
            job.attempts = 0
            job.due_at = utc_now()
            job.token = None
            job.lease_until = None
            (await session.get(RawMessage, raw_id)).status = "pending"

    async def heartbeat(self, key: str, detail: str = "") -> None:
        async with self.database.session() as session:
            state = await session.get(ReaderState, key)
            if state is None:
                session.add(ReaderState(key=key, detail=detail, updated_at=utc_now()))
            else:
                state.updated_at = utc_now()
                state.detail = detail

    async def cooldown(self, kind: str) -> datetime:
        async with self.database.session() as session:
            state = await session.get(ReaderState, "cooldown:" + kind)
            return datetime.fromisoformat(state.detail) if state else datetime.min.replace(tzinfo=timezone.utc)

    async def set_cooldown(self, kind: str, seconds: float) -> None:
        until = utc_now() + timedelta(seconds=seconds + self.settings.reader_flood_padding_seconds)
        await self.heartbeat("cooldown:" + kind, until.isoformat())

    async def history_state(self, source_id: int) -> tuple[int | None, bool]:
        async with self.database.session() as session:
            state = await session.get(SourceRuntime, source_id)
            if state is None:
                return None, True
            ready = state.status != "failed" and state.due_at <= utc_now()
            return state.history_message_id or None, ready

    async def source_success(self, source_id: int) -> None:
        async with self.database.session() as session:
            state = await session.get(SourceRuntime, source_id)
            if state is None:
                state = SourceRuntime(source_id=source_id)
                session.add(state)
            state.status = "healthy"
            state.attempts = 0
            state.error = None
            state.last_success = utc_now()
            state.due_at = utc_now() + timedelta(seconds=self.settings.reader_source_interval_seconds)

    async def source_error(self, source_id: int, error: Exception, *, unavailable: bool = False) -> None:
        async with self.database.session() as session:
            state = await session.get(SourceRuntime, source_id)
            if state is None:
                state = SourceRuntime(source_id=source_id, attempts=0)
                session.add(state)
            state.attempts = (state.attempts or 0) + 1
            state.error = type(error).__name__
            state.status = (
                "failed" if state.attempts >= self.settings.reader_source_max_attempts or unavailable else "retry_wait"
            )
            delay = min(
                self.settings.retry_initial_seconds * 2 ** (state.attempts - 1), self.settings.retry_max_seconds
            )
            state.due_at = utc_now() + timedelta(seconds=delay)
            if unavailable:
                source = await session.get(SourceChat, source_id)
                source.is_active = False
                source.status = "unavailable"
        logging.error("source %s: %s", source_id, type(error).__name__)

    async def processing_loop(self) -> None:
        while True:
            await self.recover()
            if not await self.process_one():
                await asyncio.sleep(self.settings.ingest_poll_seconds)
            await self.heartbeat("processing")
