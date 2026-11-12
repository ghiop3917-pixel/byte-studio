"""Ручное управление источниками и правилами, только для зарегистрированного админа."""

import re

from sqlalchemy import select
from telethon import types, utils

from app.bot import texts
from app.bot.accounts import AccountError
from app.config import Settings
from app.db.models import Category, ChatCandidate, Keyword, SourceChat, SourceRuntime, User, utc_now
from app.db.repo import Repository
from app.db.session import Database
from app.pipeline.prefilter import validate_keyword
from app.userbot.client import ReaderAuthorizationError, ReaderCooldown, ReaderHub
from app.utils.links import parse_chat_reference


class AdminSources:
    def __init__(self, database: Database, settings: Settings, hub: ReaderHub | None = None) -> None:
        self.database = database
        self.settings = settings
        self.hub = hub

    async def _admin(self, session, tg_id: int) -> None:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is None or not user.is_admin or user.is_banned:
            raise AccountError(texts.ADMIN_ONLY)

    async def require_admin(self, tg_id: int) -> None:
        async with self.database.session() as session:
            await self._admin(session, tg_id)

    async def add_chat(self, tg_id: int, reference: str) -> tuple[str, str]:
        await self.require_admin(tg_id)
        try:
            parsed = parse_chat_reference(reference)
        except ValueError:
            raise AccountError(texts.ADD_CHAT_USAGE) from None
        if parsed.invite_hash:
            async with self.database.session() as session:
                await self._admin(session, tg_id)
                candidate = await session.scalar(
                    select(ChatCandidate).where(ChatCandidate.invite_hash == parsed.invite_hash)
                )
                if candidate is None:
                    session.add(
                        ChatCandidate(
                            invite_hash=parsed.invite_hash, title=texts.INVITE_PENDING_TITLE, status="pending"
                        )
                    )
            return "pending", texts.INVITE_PENDING_TITLE
        if self.hub is None:
            raise AccountError(texts.READER_OFFLINE)
        try:
            entity = await self.hub.resolve(parsed.username)
        except (ReaderAuthorizationError, ReaderCooldown):
            raise AccountError(texts.READER_OFFLINE) from None
        if (
            not isinstance(entity, (types.Chat, types.Channel))
            or getattr(entity, "broadcast", False)
            or (isinstance(entity, types.Channel) and not entity.megagroup)
            or getattr(entity, "scam", False)
            or getattr(entity, "fake", False)
        ):
            raise AccountError(texts.SOURCE_NOT_GROUP)
        chat_id = utils.get_peer_id(entity)
        if getattr(entity, "left", False) or getattr(entity, "deactivated", False):
            async with self.database.session() as session:
                await self._admin(session, tg_id)
                candidate = await session.scalar(select(ChatCandidate).where(ChatCandidate.tg_chat_id == chat_id))
                if candidate is None:
                    session.add(
                        ChatCandidate(
                            tg_chat_id=chat_id,
                            username=getattr(entity, "username", None),
                            access_hash=getattr(entity, "access_hash", None),
                            title=entity.title,
                            status="pending",
                        )
                    )
            return "pending", entity.title
        async with self.database.session() as session:
            await self._admin(session, tg_id)
            source = await Repository(session, self.settings).upsert_source(
                chat_id,
                entity.title,
                username=getattr(entity, "username", None),
                access_hash=getattr(entity, "access_hash", None),
            )
            source.is_active = True
            source.status = "active"
            state = await session.get(SourceRuntime, source.id)
            if state is not None:
                state.status = "pending"
                state.attempts = 0
                state.due_at = utc_now()
                state.error = None
        await self.hub.refresh()
        return "added", entity.title

    async def remove_chat(self, tg_id: int, identity: int) -> None:
        await self.require_admin(tg_id)
        async with self.database.session() as session:
            await self._admin(session, tg_id)
            source = (
                await session.scalar(select(SourceChat).where(SourceChat.tg_chat_id == identity))
                if identity < 0
                else await session.get(SourceChat, identity)
            )
            if source is None:
                raise AccountError(texts.SOURCE_MISSING)
            source.is_active = False
            source.status = "removed"
        if self.hub is not None:
            await self.hub.refresh()

    async def chats(self, tg_id: int) -> tuple[dict, ...]:
        await self.require_admin(tg_id)
        async with self.database.session() as session:
            await self._admin(session, tg_id)
            rows = (
                await session.execute(
                    select(SourceChat, SourceRuntime)
                    .outerjoin(SourceRuntime, SourceRuntime.source_id == SourceChat.id)
                    .order_by(SourceChat.id)
                    .limit(self.settings.admin_list_limit)
                )
            ).all()
            return tuple(
                {
                    "id": source.id,
                    "chat_id": source.tg_chat_id,
                    "title": source.title,
                    "status": source.status,
                    "health": runtime.status if runtime else "pending",
                }
                for source, runtime in rows
            )

    async def keywords(
        self,
        tg_id: int,
        slug: str,
        *,
        action: str = "list",
        kind: str | None = None,
        word: str | None = None,
        keyword_id: int | None = None,
        regex: bool = False,
    ) -> tuple[dict, ...]:
        await self.require_admin(tg_id)
        async with self.database.session() as session:
            await self._admin(session, tg_id)
            category = await session.scalar(select(Category).where(Category.slug == slug))
            if category is None:
                raise AccountError(texts.CATEGORY_UNKNOWN)
            if action == "add":
                if kind not in {"include", "exclude"} or word is None:
                    raise AccountError(texts.KEYWORDS_USAGE)
                try:
                    word = validate_keyword(word, regex, self.settings)
                except (ValueError, re.error) as error:
                    # Наружу не возвращаются шаблоны или содержимое исключений библиотеки re.
                    raise AccountError(texts.KEYWORDS_USAGE) from error
                existing = await session.scalar(
                    select(Keyword).where(
                        Keyword.category_id == category.id, Keyword.word == word, Keyword.type == kind
                    )
                )
                if existing is None:
                    session.add(Keyword(category_id=category.id, word=word, type=kind, is_regex=regex, is_active=True))
                else:
                    existing.is_active = True
                    existing.is_regex = regex
                await session.flush()
            elif action == "remove":
                item = await session.get(Keyword, keyword_id) if keyword_id is not None else None
                if item is None or item.category_id != category.id:
                    raise AccountError(texts.KEYWORD_MISSING)
                item.is_active = False
            elif action != "list":
                raise AccountError(texts.KEYWORDS_USAGE)
            rows = (
                await session.scalars(
                    select(Keyword)
                    .where(Keyword.category_id == category.id)
                    .order_by(Keyword.id)
                    .limit(self.settings.admin_list_limit)
                )
            ).all()
            return tuple(
                {"id": item.id, "word": item.word, "kind": item.type, "regex": item.is_regex, "active": item.is_active}
                for item in rows
            )
