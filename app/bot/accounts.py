"""Пользовательские операции единого бота: одна транзакция на действие."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Category, LeadDelivery, User, UserCategory, utc_now
from app.db.repo import Repository
from app.db.session import Database


class AccountError(ValueError):
    """Ошибка с безопасным сообщением из texts.py."""


@dataclass(frozen=True)
class AccountView:
    tg_id: int
    subscription_until: datetime | None
    min_score: int
    geo_filter: str | None
    categories: tuple[tuple[int, str, str, bool], ...]
    received_day: int
    received_week: int


class Accounts:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def _user(self, session: AsyncSession, tg_id: int) -> User:
        from app.bot import texts

        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is None:
            raise AccountError(texts.START_REQUIRED)
        if user.is_banned:
            raise AccountError(texts.ACCOUNT_BANNED)
        return user

    async def register(self, tg_id: int, username: str | None) -> AccountView:
        async with self.database.session() as session:
            repo = Repository(session, self.settings)
            existing = await repo.get_user(tg_id)
            if existing and existing.is_banned:
                from app.bot import texts

                raise AccountError(texts.ACCOUNT_BANNED)
            user = await repo.ensure_user(tg_id, username, is_admin=tg_id in self.settings.admin_ids)
            user.is_active = True
            user.username = username
            categories = await repo.seed_categories()
            if existing is None:
                default = next(
                    category for category in categories if category.slug == self.settings.default_category_slug
                )
                selected = await session.get(UserCategory, (user.id, default.id))
                if selected is None:
                    session.add(UserCategory(user_id=user.id, category_id=default.id))
        return await self.view(tg_id)

    async def view(self, tg_id: int) -> AccountView:
        async with self.database.session() as session:
            user = await self._user(session, tg_id)
            chosen = set(
                (await session.scalars(select(UserCategory.category_id).where(UserCategory.user_id == user.id))).all()
            )
            categories = tuple(
                (category.id, category.slug, category.title, category.id in chosen)
                for category in (
                    await session.scalars(select(Category).where(Category.is_active.is_(True)).order_by(Category.id))
                ).all()
            )
            now = utc_now()

            async def received(days: int) -> int:
                query = (
                    select(func.count())
                    .select_from(LeadDelivery)
                    .where(LeadDelivery.user_id == user.id, LeadDelivery.sent_at >= now - timedelta(days=days))
                )
                return int(await session.scalar(query) or 0)

            return AccountView(
                user.tg_id,
                user.subscription_until,
                user.min_score,
                user.geo_filter,
                categories,
                await received(1),
                await received(7),
            )

    async def set_category(self, tg_id: int, category_id: int, enabled: bool) -> AccountView:
        from app.bot import texts

        async with self.database.session() as session:
            user = await self._user(session, tg_id)
            category = await session.get(Category, category_id)
            if category is None or not category.is_active:
                raise AccountError(texts.CATEGORY_UNAVAILABLE)
            link = await session.get(UserCategory, (user.id, category_id))
            if enabled and link is None:
                session.add(UserCategory(user_id=user.id, category_id=category_id))
            elif not enabled and link is not None:
                await session.delete(link)
        return await self.view(tg_id)

    async def set_score(self, tg_id: int, score: int) -> AccountView:
        from app.bot import texts

        if score not in self.settings.score_choices:
            raise AccountError(texts.SCORE_INVALID)
        async with self.database.session() as session:
            user = await self._user(session, tg_id)
            user.min_score = score
        return await self.view(tg_id)

    async def set_geo(self, tg_id: int, value: str | None) -> AccountView:
        from app.bot import texts

        if value is not None:
            value = value.strip()
            if not value or len(value) > self.settings.geo_max_length or any(ord(c) < 32 for c in value):
                raise AccountError(texts.GEO_INVALID.format(limit=self.settings.geo_max_length))
        async with self.database.session() as session:
            user = await self._user(session, tg_id)
            user.geo_filter = value
        return await self.view(tg_id)
