"""Репозиторий новых сущностей; транзакцией владеет Database.session()."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INITIAL_CATEGORIES, Settings
from app.db.models import Category, Lead, LeadDelivery, SourceChat, User, utc_now


@dataclass(frozen=True)
class LeadInput:
    category_id: int
    text: str
    summary: str
    score: int
    source_chat_id: int
    message_id: int
    text_hash: str
    author_tg_id: int | None = None
    author_username: str | None = None
    message_link: str | None = None
    created_at: datetime | None = None


class Repository:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def get_user(self, tg_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.tg_id == tg_id))

    async def ensure_user(self, tg_id: int, username: str | None = None, *, is_admin: bool = False) -> User:
        if tg_id <= 0:
            raise ValueError("Telegram ID пользователя должен быть положительным.")
        existing = await self.get_user(tg_id)
        if existing is not None:
            return existing
        now = utc_now()
        user = User(
            tg_id=tg_id,
            username=username,
            is_admin=is_admin,
            created_at=now,
            min_score=self.settings.default_min_score,
            subscription_until=now + timedelta(days=self.settings.trial_days),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(user)
                await self.session.flush()
        except IntegrityError:
            existing = await self.get_user(tg_id)
            if existing is None:
                raise
            return existing
        return user

    async def seed_categories(self) -> tuple[Category, ...]:
        for slug, title in INITIAL_CATEGORIES:
            if await self.session.scalar(select(Category).where(Category.slug == slug)) is not None:
                continue
            try:
                async with self.session.begin_nested():
                    self.session.add(Category(slug=slug, title=title))
                    await self.session.flush()
            except IntegrityError:
                if await self.session.scalar(select(Category).where(Category.slug == slug)) is None:
                    raise
        return tuple((await self.session.scalars(select(Category).order_by(Category.id))).all())

    async def upsert_source(
        self,
        tg_chat_id: int,
        title: str,
        *,
        username: str | None = None,
        access_hash: int | None = None,
        geo: str | None = None,
    ) -> SourceChat:
        if tg_chat_id >= 0 or not title.strip():
            raise ValueError("Нужен отрицательный get_peer_id группы и её название.")
        existing = await self.session.scalar(select(SourceChat).where(SourceChat.tg_chat_id == tg_chat_id))
        if existing is not None:
            return existing
        source = SourceChat(tg_chat_id=tg_chat_id, title=title, username=username, access_hash=access_hash, geo=geo)
        try:
            async with self.session.begin_nested():
                self.session.add(source)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(select(SourceChat).where(SourceChat.tg_chat_id == tg_chat_id))
            if existing is None:
                raise
            return existing
        return source

    async def active_source_ids(self) -> frozenset[int]:
        query = select(SourceChat.tg_chat_id).where(SourceChat.is_active.is_(True))
        return frozenset((await self.session.scalars(query)).all())

    async def save_lead(self, data: LeadInput) -> Lead:
        if not data.text.strip() or not 0 <= data.score <= 100 or data.message_id <= 0:
            raise ValueError("Некорректный текст, оценка или ID сообщения.")
        if len(data.summary) > self.settings.summary_max_length:
            raise ValueError("Саммари превышает настроенную длину.")
        query = select(Lead).where(Lead.source_chat_id == data.source_chat_id, Lead.message_id == data.message_id)
        existing = await self.session.scalar(query)
        if existing is not None:
            return existing
        lead = Lead(
            category_id=data.category_id,
            text=data.text,
            summary=data.summary,
            score=data.score,
            source_chat_id=data.source_chat_id,
            message_id=data.message_id,
            text_hash=data.text_hash,
            author_tg_id=data.author_tg_id,
            author_username=data.author_username,
            message_link=data.message_link,
            created_at=data.created_at or utc_now(),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(lead)
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(query)
            if existing is None:
                raise
            return existing
        return lead

    async def record_delivery(self, lead_id: int, user_id: int, message_id: int) -> bool:
        query = select(LeadDelivery.id).where(LeadDelivery.lead_id == lead_id, LeadDelivery.user_id == user_id)
        if await self.session.scalar(query) is not None:
            return False
        try:
            async with self.session.begin_nested():
                self.session.add(LeadDelivery(lead_id=lead_id, user_id=user_id, message_id=message_id))
                await self.session.flush()
        except IntegrityError:
            if await self.session.scalar(query) is None:
                raise
            return False
        return True
