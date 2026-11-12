"""Персональная outbox: отправка вне SQLite, ambiguous без автоповтора."""

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.bot import texts
from app.bot.keyboards import LeadActionData
from app.config import Settings
from app.db.models import (
    AuthorBlacklist,
    Category,
    DeliveryJob,
    Lead,
    LeadDelivery,
    ReaderState,
    SourceChat,
    User,
    UserCategory,
    utc_now,
)
from app.db.repo import Repository
from app.db.session import Database
from app.utils.links import USERNAME


async def bot_pause(database: Database):
    async with database.session() as session:
        state = await session.get(ReaderState, "bot_retry_until")
        return datetime.fromisoformat(state.detail) if state else utc_now()


async def set_bot_pause(session, delay: float) -> None:
    until = utc_now() + timedelta(seconds=delay)
    state = await session.get(ReaderState, "bot_retry_until")
    if state is None:
        session.add(ReaderState(key="bot_retry_until", detail=until.isoformat(), updated_at=utc_now()))
    elif datetime.fromisoformat(state.detail) < until:
        state.detail, state.updated_at = until.isoformat(), utc_now()


class MessageRateLimiter:
    """Один общий шлюз для SendMessage и EditMessageText всех сервисов бота."""

    def __init__(self, settings: Settings, *, database: Database | None = None) -> None:
        self.database = database
        self.interval = 1 / settings.delivery_messages_per_second
        self.lock = asyncio.Lock()
        self.last = 0.0

    async def __call__(self, make_request, bot, method):
        if isinstance(method, (SendMessage, EditMessageText)):
            if self.database is not None:
                remaining = ((await bot_pause(self.database)) - utc_now()).total_seconds()
                if remaining > 0:
                    raise TelegramRetryAfter(
                        method=method, message="Сохранённый cooldown", retry_after=max(1, int(remaining) + 1)
                    )
            async with self.lock:
                await asyncio.sleep(max(0, self.interval - (time.monotonic() - self.last)))
                self.last = time.monotonic()
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as error:
            if self.database is not None:
                async with self.database.session() as session:
                    await set_bot_pause(session, error.retry_after)
            raise


async def eligible(session, user: User, lead: Lead, source: SourceChat, settings: Settings) -> bool:
    if (
        not user.is_active
        or user.is_banned
        or user.subscription_until is None
        or user.subscription_until <= utc_now()
        or lead.score < user.min_score
        or (settings.admin_only_delivery and not user.is_admin)
        or not source.is_active
    ):
        return False
    category = await session.get(Category, lead.category_id)
    if (
        category is None
        or not category.is_active
        or await session.get(UserCategory, (user.id, lead.category_id)) is None
    ):
        return False
    if user.geo_filter and (source.geo or "").strip().casefold() != user.geo_filter.strip().casefold():
        return False
    return lead.author_tg_id is None or await session.get(AuthorBlacklist, lead.author_tg_id) is None


def card(lead: Lead, source: SourceChat, category: Category, settings: Settings):
    excerpt = lead.text[: settings.lead_excerpt_length]
    if len(excerpt) < len(lead.text):
        excerpt += "…"
    values = dict(
        score=lead.score,
        category=category.title,
        summary=lead.summary,
        text=excerpt,
        author="@" + lead.author_username
        if lead.author_username and USERNAME.fullmatch(lead.author_username)
        else texts.HIDDEN_USERNAME,
        source=source.title,
        geo=source.geo or texts.UNKNOWN_GEO,
        date=lead.created_at.astimezone(ZoneInfo(settings.display_tz)).strftime("%d.%m.%Y %H:%M"),
    )
    rendered = texts.LEAD_CARD.format(**{k: escape(str(v)) for k, v in values.items()})
    # Укорачиваем исходные поля до escape, сохраняя целую HTML-разметку.
    for field in ("text", "source", "summary", "category", "geo"):
        while len(rendered) > settings.telegram_text_limit and len(values[field]) > 1:
            values[field] = values[field][: max(1, len(values[field]) // 2)]
            rendered = texts.LEAD_CARD.format(**{k: escape(str(v)) for k, v in values.items()})
    if len(rendered) > settings.telegram_text_limit:
        raise ValueError("card_limit")
    links = []
    if lead.author_username and USERNAME.fullmatch(lead.author_username):
        links.append(InlineKeyboardButton(text=texts.BTN_WRITE, url="https://t.me/" + lead.author_username))
    if lead.message_link and re.fullmatch(
        r"https://t\.me/(?:[A-Za-z][A-Za-z0-9_]{3,31}|c/\d+)/(?:\d+/)?\d+", lead.message_link
    ):
        links.append(InlineKeyboardButton(text=texts.BTN_ORIGINAL, url=lead.message_link))
    actions = [
        InlineKeyboardButton(
            text=texts.BTN_DRAFT, callback_data=LeadActionData(lead_id=lead.id, action="draft").pack()
        ),
        InlineKeyboardButton(text=texts.BTN_SPAM, callback_data=LeadActionData(lead_id=lead.id, action="spam").pack()),
    ]
    return rendered, InlineKeyboardMarkup(inline_keyboard=([links] if links else []) + [actions])


@dataclass(frozen=True)
class DeliveryClaim:
    id: int
    token: str
    tg_id: int
    text: str
    markup: InlineKeyboardMarkup


class Delivery:
    def __init__(self, database: Database, settings: Settings, bot: Bot) -> None:
        self.database, self.settings, self.bot = database, settings, bot

    async def recover(self) -> int:
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(DeliveryJob).where(DeliveryJob.status == "processing", DeliveryJob.lease_until < utc_now())
                )
            ).all()
            for row in rows:
                row.status, row.token, row.lease_until = "ambiguous", None, None
                row.errors = json.dumps(
                    [*json.loads(row.errors), {"at": utc_now().isoformat(), "error": "lease_expired"}]
                )
            return len(rows)

    async def claim(self) -> DeliveryClaim | None:
        if await bot_pause(self.database) > utc_now():
            return None
        async with self.database.session() as session:
            job = await session.scalar(
                select(DeliveryJob)
                .where(
                    DeliveryJob.status.in_(("pending", "retry_wait")),
                    DeliveryJob.due_at <= utc_now(),
                    DeliveryJob.attempts < self.settings.delivery_max_attempts,
                )
                .order_by(DeliveryJob.id)
                .limit(1)
            )
            if job is None:
                return None
            user, lead = await session.get(User, job.user_id), await session.get(Lead, job.lead_id)
            source = await session.get(SourceChat, lead.source_chat_id)
            if await session.scalar(
                select(LeadDelivery.id).where(LeadDelivery.lead_id == lead.id, LeadDelivery.user_id == user.id)
            ):
                job.status = "completed"
                return None
            if self.settings.dry_run:
                job.status = "dry_run"
                return None
            if not await eligible(session, user, lead, source, self.settings):
                job.status = "suppressed"
                return None
            category = await session.get(Category, lead.category_id)
            try:
                text, markup = card(lead, source, category, self.settings)
            except ValueError:
                job.status, job.errors = "failed", json.dumps([{"error": "card_limit"}])
                return None
            job.status, job.token = "processing", uuid.uuid4().hex
            job.attempts += 1
            job.last_attempt_at = utc_now()
            job.lease_until = utc_now() + timedelta(seconds=self.settings.delivery_lease_seconds)
            return DeliveryClaim(job.id, job.token, user.tg_id, text, markup)

    async def outcome(
        self,
        claim: DeliveryClaim,
        status: str,
        *,
        message_id: int | None = None,
        error: str | None = None,
        delay: float = 0,
        deactivate: bool = False,
    ) -> None:
        async with self.database.session() as session:
            job = await session.get(DeliveryJob, claim.id)
            if job.status != "processing" or job.token != claim.token:
                return
            job.status = (
                "failed" if status == "retry_wait" and job.attempts >= self.settings.delivery_max_attempts else status
            )
            job.token = job.lease_until = None
            job.due_at = utc_now() + timedelta(seconds=delay)
            if error == "TelegramRetryAfter":
                await set_bot_pause(session, delay)
            if error:
                job.errors = json.dumps([*json.loads(job.errors), {"at": utc_now().isoformat(), "error": error}])
            if deactivate:
                (await session.get(User, job.user_id)).is_active = False
            if message_id is not None:
                await Repository(session, self.settings).record_delivery(job.lead_id, job.user_id, message_id)
                job.message_id = message_id

    async def process_one(self) -> bool:
        claim = await self.claim()
        if claim is None:
            return False
        try:
            result = await self.bot.send_message(
                claim.tg_id, claim.text, parse_mode="HTML", reply_markup=claim.markup, disable_web_page_preview=True
            )
        except TelegramRetryAfter as error:
            await self.outcome(claim, "retry_wait", error="TelegramRetryAfter", delay=error.retry_after)
        except TelegramForbiddenError:
            await self.outcome(claim, "failed", error="TelegramForbiddenError", deactivate=True)
        except TelegramBadRequest:
            await self.outcome(claim, "failed", error="TelegramBadRequest")
        except (TelegramNetworkError, OSError, asyncio.TimeoutError):
            await self.outcome(claim, "ambiguous", error="network_outcome_unknown")
        except asyncio.CancelledError:
            # Даже отмена могла произойти после принятия Telegram; повтор запрещён.
            await self.outcome(claim, "ambiguous", error="cancelled_outcome_unknown")
            raise
        except Exception:
            await self.outcome(claim, "ambiguous", error="unknown_outcome")
        else:
            # При сбое SQLite после успешной сети останется processing -> ambiguous.
            await self.outcome(claim, "completed", message_id=result.message_id)
        return True

    async def loop(self) -> None:
        while True:
            await self.recover()
            if not await self.process_one():
                await asyncio.sleep(self.settings.delivery_poll_seconds)
