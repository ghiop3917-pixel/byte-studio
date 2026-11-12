"""Классификация и порционный fanout через существующую долговечную очередь."""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from app.config import Settings
from app.db.models import (
    AuthorBlacklist,
    Category,
    ClassificationCache,
    DeliveryJob,
    IngestJob,
    Lead,
    RawMessage,
    SourceChat,
    User,
    utc_now,
)
from app.db.repo import LeadInput, Repository
from app.db.session import Database
from app.pipeline.classifier import LLM, Classification, InvalidResponse, LLMDisabled, cache_key
from app.pipeline.delivery import eligible
from app.pipeline.ingestion import Ingestion
from app.utils.text import text_hash


@dataclass(frozen=True)
class ClassificationClaim:
    id: int
    raw_id: int
    token: str
    phase: str
    text: str
    title: str


class ClassificationWorker:
    def __init__(self, database: Database, settings: Settings, llm: LLM) -> None:
        self.database, self.settings, self.llm = database, settings, llm

    async def claim(self) -> ClassificationClaim | None:
        async with self.database.session() as session:
            phases = ("classifier", "fanout") if self.settings.llm_enabled and self.settings.llm_model else ("fanout",)
            job = await session.scalar(
                select(IngestJob)
                .where(
                    IngestJob.phase.in_(phases),
                    IngestJob.status.in_(("pending", "retry_wait")),
                    IngestJob.due_at <= utc_now(),
                    IngestJob.attempts < self.settings.llm_max_attempts,
                )
                .order_by(IngestJob.id)
                .limit(1)
            )
            if job is None:
                return None
            raw = await session.get(RawMessage, job.raw_id)
            source = await session.get(SourceChat, raw.source_chat_id)
            if not source.is_active or (
                raw.author_tg_id is not None and await session.get(AuthorBlacklist, raw.author_tg_id) is not None
            ):
                raw.status, raw.reason, job.status = "filtered", "blocked_or_inactive", "completed"
                return None
            job.status, job.token = "processing", uuid.uuid4().hex
            job.attempts += 1
            job.lease_until = utc_now() + timedelta(seconds=self.settings.classifier_lease_seconds)
            return ClassificationClaim(job.id, raw.id, job.token, job.phase, raw.original_text, raw.source_title)

    async def fail(self, claim: ClassificationClaim, error: Exception) -> None:
        async with self.database.session() as session:
            job = await session.get(IngestJob, claim.id)
            if job.token != claim.token or job.status != "processing":
                return
            if not isinstance(error, LLMDisabled):
                job.errors = json.dumps(
                    [*json.loads(job.errors), {"at": utc_now().isoformat(), "error": type(error).__name__}]
                )
            if isinstance(error, LLMDisabled):
                job.attempts -= 1
                job.status = "retry_wait"
            elif isinstance(error, InvalidResponse) or job.attempts >= self.settings.llm_max_attempts:
                job.status = "failed"
                raw = await session.get(RawMessage, claim.raw_id)
                raw.status, raw.reason = "failed", type(error).__name__
            else:
                job.status = "retry_wait"
            job.due_at = utc_now() + timedelta(
                seconds=min(
                    self.settings.retry_initial_seconds * 2 ** max(0, job.attempts - 1), self.settings.retry_max_seconds
                )
            )
            if isinstance(error, LLMDisabled):
                job.due_at = utc_now() + timedelta(seconds=self.settings.retry_max_seconds)
            job.token = job.lease_until = None
        logging.error("classifier %s: %s", claim.id, type(error).__name__)

    async def result(self, claim: ClassificationClaim) -> Classification:
        key = cache_key(claim.text, claim.title, self.settings)
        async with self.database.session() as session:
            row = await session.get(ClassificationCache, key)
            cached = row.result if row else None
        if cached:
            return Classification.model_validate_json(cached)
        # Сеть не выполняется в транзакции.
        result = await self.llm.classify(claim.text, claim.title)
        if not isinstance(result, Classification):
            raise InvalidResponse("classification_type")
        return result

    async def save(self, claim: ClassificationClaim, result: Classification) -> None:
        async with self.database.session() as session:
            job = await session.get(IngestJob, claim.id)
            if job.token != claim.token or job.status != "processing":
                return
            raw = await session.get(RawMessage, claim.raw_id)
            source = await session.get(SourceChat, raw.source_chat_id)
            key = cache_key(claim.text, claim.title, self.settings)
            if await session.get(ClassificationCache, key) is None:
                session.add(
                    ClassificationCache(
                        key=key, text_hash=text_hash(raw.original_text), result=result.model_dump_json()
                    )
                )
            raw.language = result.language
            raw.reason = "classified"
            blocked = raw.author_tg_id is not None and await session.get(AuthorBlacklist, raw.author_tg_id) is not None
            category = await session.scalar(
                select(Category).where(Category.slug == result.category, Category.is_active.is_(True))
            )
            if not result.is_lead or blocked or not source.is_active or category is None:
                raw.status = "filtered"
                raw.reason = "not_lead" if not result.is_lead else "blocked_or_inactive"
                job.status = "completed"
            else:
                await Repository(session, self.settings).save_lead(
                    LeadInput(
                        category_id=category.id,
                        text=raw.original_text,
                        summary=result.summary,
                        score=result.score,
                        source_chat_id=raw.source_chat_id,
                        message_id=raw.message_id,
                        text_hash=text_hash(raw.original_text),
                        author_tg_id=raw.author_tg_id,
                        author_username=raw.author_username,
                        message_link=raw.message_link,
                        created_at=raw.published_at or raw.received_at,
                    )
                )
                raw.status = "lead"
                job.phase, job.status, job.attempts, job.fanout_after = "fanout", "pending", 0, 0
                job.due_at = utc_now()
            job.token = job.lease_until = None

    async def fanout(self, claim: ClassificationClaim) -> None:
        async with self.database.session() as session:
            job = await session.get(IngestJob, claim.id)
            if job.token != claim.token or job.status != "processing":
                return
            raw = await session.get(RawMessage, job.raw_id)
            lead = await session.scalar(
                select(Lead).where(Lead.source_chat_id == raw.source_chat_id, Lead.message_id == raw.message_id)
            )
            if lead is None:
                raise ValueError("fanout_lead_missing")
            source = await session.get(SourceChat, lead.source_chat_id)
            users = (
                await session.scalars(
                    select(User)
                    .where(User.id > job.fanout_after)
                    .order_by(User.id)
                    .limit(self.settings.delivery_batch_size)
                )
            ).all()
            for user in users:
                if await eligible(session, user, lead, source, self.settings):
                    existing = await session.scalar(
                        select(DeliveryJob.id).where(DeliveryJob.lead_id == lead.id, DeliveryJob.user_id == user.id)
                    )
                    if existing is None:
                        session.add(
                            DeliveryJob(
                                lead_id=lead.id,
                                user_id=user.id,
                                status="dry_run" if self.settings.dry_run else "pending",
                            )
                        )
                job.fanout_after = user.id
            job.status = "completed" if len(users) < self.settings.delivery_batch_size else "pending"
            job.token = job.lease_until = None
            job.attempts, job.due_at = 0, utc_now()

    async def process_one(self) -> bool:
        claim = await self.claim()
        if claim is None:
            return False
        try:
            if claim.phase == "fanout":
                await self.fanout(claim)
            else:
                await self.save(claim, await self.result(claim))
        except Exception as error:
            await self.fail(claim, error)
        return True

    async def loop(self) -> None:
        storage = Ingestion(self.database, self.settings)
        while True:
            await storage.recover()
            if not await self.process_one():
                await asyncio.sleep(self.settings.classifier_poll_seconds)
            await storage.heartbeat("classifier")
