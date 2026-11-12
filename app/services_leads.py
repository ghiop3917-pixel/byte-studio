"""Черновики и обратная связь только по карточкам, реально доставленным пользователю."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.bot import texts
from app.bot.accounts import AccountError, Accounts
from app.config import Settings
from app.db.models import AuthorBlacklist, Lead, LeadDelivery, LeadDraft, LeadFeedback, SourceChat, utc_now
from app.db.session import Database
from app.pipeline.classifier import LLM


class LeadActions:
    def __init__(self, database: Database, settings: Settings, llm: LLM | None = None) -> None:
        self.database, self.settings, self.llm = database, settings, llm

    async def access(self, session, actor: int, lead_id: int, *, subscribed: bool = False):
        user = await Accounts(self.database, self.settings)._user(session, actor)
        if not user.is_active or (
            subscribed and (user.subscription_until is None or user.subscription_until <= utc_now())
        ):
            raise AccountError(texts.LEAD_ACCESS_DENIED)
        lead = await session.get(Lead, lead_id)
        received = await session.scalar(
            select(LeadDelivery.id).where(LeadDelivery.lead_id == lead_id, LeadDelivery.user_id == user.id)
        )
        if lead is None or received is None:
            raise AccountError(texts.LEAD_ACCESS_DENIED)
        return user, lead

    async def feedback(self, actor: int, lead_id: int, kind: str) -> None:
        if kind not in {"spam", "contacted", "responded"}:
            raise AccountError(texts.LEAD_ACCESS_DENIED)
        async with self.database.session() as session:
            user, lead = await self.access(session, actor, lead_id)
            existing = await session.scalar(
                select(LeadFeedback.id).where(
                    LeadFeedback.lead_id == lead_id, LeadFeedback.user_id == user.id, LeadFeedback.type == kind
                )
            )
            if existing is None:
                session.add(LeadFeedback(lead_id=lead_id, user_id=user.id, type=kind))
                await session.flush()
            if kind == "spam" and lead.author_tg_id is not None:
                total = await session.scalar(
                    select(func.count(func.distinct(LeadFeedback.user_id)))
                    .join(Lead, Lead.id == LeadFeedback.lead_id)
                    .where(Lead.author_tg_id == lead.author_tg_id, LeadFeedback.type == "spam")
                )
                if (
                    total >= self.settings.spam_distinct_user_threshold
                    and await session.get(AuthorBlacklist, lead.author_tg_id) is None
                ):
                    session.add(AuthorBlacklist(author_tg_id=lead.author_tg_id, reason="distinct_user_spam_feedback"))

    async def draft(self, actor: int, lead_id: int) -> str:
        async with self.database.session() as session:
            user, lead = await self.access(session, actor, lead_id, subscribed=True)
            row = await session.scalar(
                select(LeadDraft).where(LeadDraft.lead_id == lead_id, LeadDraft.user_id == user.id)
            )
            if row and row.status == "completed":
                return row.text
            if self.llm is None or not self.settings.llm_enabled:
                raise AccountError(texts.DRAFT_DISABLED)
            if row is None:
                row = LeadDraft(lead_id=lead_id, user_id=user.id, attempts=0)
                session.add(row)
                await session.flush()
            now = utc_now()
            if (row.status == "processing" and row.lease_until > now) or row.due_at > now:
                raise AccountError(texts.DRAFT_BUSY)
            if row.attempts >= self.settings.llm_max_attempts:
                raise AccountError(texts.DRAFT_FAILED)
            row.status, row.token = "processing", uuid.uuid4().hex
            row.attempts += 1
            row.lease_until = now + timedelta(seconds=self.settings.draft_lease_seconds)
            identity, token, text = row.id, row.token, lead.text
            source = await session.get(SourceChat, lead.source_chat_id)
            title = source.title
        try:
            result = await self.llm.draft(text, title)
            async with self.database.session() as session:
                await self.access(session, actor, lead_id, subscribed=True)
                row = await session.get(LeadDraft, identity)
                if row.status != "processing" or row.token != token:
                    raise AccountError(texts.DRAFT_BUSY)
                row.status, row.text, row.token, row.lease_until = "completed", result, None, None
                return result
        except Exception as error:
            async with self.database.session() as session:
                row = await session.get(LeadDraft, identity)
                if row.token == token:
                    row.status, row.error, row.token, row.lease_until = "failed", type(error).__name__, None, None
                    row.due_at = utc_now() + timedelta(
                        seconds=min(
                            self.settings.retry_initial_seconds * 2 ** (row.attempts - 1),
                            self.settings.retry_max_seconds,
                        )
                    )
            raise AccountError(texts.DRAFT_FAILED) from None
