"""Ручная модерация кандидатов. Отправки только администратору по его запросу."""

import json
from html import escape

from aiogram.filters.command import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.bot import texts
from app.bot.accounts import AccountError, Accounts
from app.bot.handlers.admin import answer_lines, service
from app.bot.keyboards import CandidateData, CandidatesPageData
from app.db.models import JoinAttempt, ReaderState, SourceChat, utc_now
from app.discovery import Growth
from app.userbot.client import ReaderHub


def control(message: Message, accounts: Accounts, hub: ReaderHub | None) -> Growth:
    service(message, accounts, hub)
    return Growth(accounts.database, accounts.settings, hub or ReaderHub())


async def discover(
    message: Message, command: CommandObject, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    growth = control(message, accounts, reader_hub)
    if not command.args:
        raise AccountError(texts.DISCOVER_USAGE)
    await growth.request_discovery(message.from_user.id, command.args.strip())
    await message.answer(texts.DISCOVER_QUEUED)


async def show_candidates(message: Message, accounts: Accounts, hub: ReaderHub | None, actor: int, after: int) -> None:
    if message.chat.type != "private" or message.chat.id != actor:
        raise AccountError(texts.ADMIN_ONLY)
    growth = Growth(accounts.database, accounts.settings, hub or ReaderHub())
    rows = await growth.candidates(actor, after)
    async with accounts.database.session() as session:
        # Проверка роли повторяется перед чтением служебного состояния.
        from app.services_sources import AdminSources

        await AdminSources(accounts.database, accounts.settings)._admin(session, actor)
        stopped = await session.get(ReaderState, "join_stopped")
        midnight = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        attempts = await session.scalar(
            select(func.count()).select_from(JoinAttempt).where(JoinAttempt.started_at >= midnight)
        )
        status = texts.GROWTH_STATUS.format(
            dry_run=accounts.settings.dry_run,
            auto_join=accounts.settings.auto_join,
            stopped=stopped.detail if stopped and stopped.detail else "—",
            attempts=attempts,
        )
    await message.answer(status, parse_mode=None)
    if not rows:
        await message.answer(texts.CANDIDATES_EMPTY)
        return
    for row in rows:
        buttons = None
        if row["status"] in {"pending", "approved"}:
            buttons = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.BTN_CONNECT,
                            callback_data=CandidateData(candidate_id=row["id"], approve=True).pack(),
                        ),
                        InlineKeyboardButton(
                            text=texts.BTN_REJECT,
                            callback_data=CandidateData(candidate_id=row["id"], approve=False).pack(),
                        ),
                    ]
                ]
            )
        values = {key: escape(str(value)) if value is not None else texts.UNKNOWN_VALUE for key, value in row.items()}
        await message.answer(texts.CANDIDATE_CARD.format(**values), parse_mode="HTML", reply_markup=buttons)
    if len(rows) == accounts.settings.candidate_page_size:
        await message.answer(
            texts.CANDIDATE_PAGE.format(after=rows[-1]["id"]),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.BTN_NEXT_CANDIDATES,
                            callback_data=CandidatesPageData(after=rows[-1]["id"]).pack(),
                        )
                    ]
                ]
            ),
        )


async def candidates(
    message: Message, command: CommandObject, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    try:
        after = int(command.args or 0)
        if after < 0:
            raise ValueError()
    except ValueError:
        raise AccountError(texts.CANDIDATES_USAGE) from None
    await show_candidates(message, accounts, reader_hub, message.from_user.id, after)


def callback_actor(callback: CallbackQuery) -> int:
    if (
        not isinstance(callback.message, Message)
        or callback.message.chat.type != "private"
        or callback.message.chat.id != callback.from_user.id
    ):
        raise AccountError(texts.ADMIN_ONLY)
    return callback.from_user.id


async def decision(
    callback: CallbackQuery, callback_data: CandidateData, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    actor = callback_actor(callback)
    # callback.message.from_user — бот, поэтому сервис вызывается с проверенным actor напрямую.
    growth = Growth(accounts.database, accounts.settings, reader_hub or ReaderHub())
    status = await growth.decide(actor, callback_data.candidate_id, callback_data.approve)
    await callback.answer(texts.CANDIDATE_DECIDED.format(status=status), show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)


async def page(
    callback: CallbackQuery, callback_data: CandidatesPageData, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    actor = callback_actor(callback)
    await show_candidates(callback.message, accounts, reader_hub, actor, callback_data.after)
    await callback.answer()


async def resume(message: Message, accounts: Accounts, reader_hub: ReaderHub | None = None) -> None:
    admin = service(message, accounts, reader_hub)
    async with accounts.database.session() as session:
        await admin._admin(session, message.from_user.id)
        stopped = await session.get(ReaderState, "join_stopped")
        if stopped:
            stopped.detail, stopped.updated_at = "", utc_now()
    await message.answer(texts.JOIN_RESUMED)


async def geo(
    message: Message, command: CommandObject, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    admin = service(message, accounts, reader_hub)
    parts = (command.args or "").split(maxsplit=1)
    try:
        identity = int(parts[0])
        value = parts[1].strip()
        if not value or len(value) > accounts.settings.geo_max_length:
            raise ValueError()
    except (ValueError, IndexError):
        raise AccountError(texts.GEO_USAGE) from None
    async with accounts.database.session() as session:
        await admin._admin(session, message.from_user.id)
        source = await session.get(SourceChat, identity)
        if source is None:
            raise AccountError(texts.SOURCE_MISSING)
        source.geo = value
    await message.answer(texts.GEO_SAVED)


async def review(message: Message, accounts: Accounts, reader_hub: ReaderHub | None = None) -> None:
    admin = service(message, accounts, reader_hub)
    await admin.require_admin(message.from_user.id)
    growth = Growth(accounts.database, accounts.settings, reader_hub or ReaderHub())
    await growth.review_sources()
    async with accounts.database.session() as session:
        await admin._admin(session, message.from_user.id)
        state = await session.get(ReaderState, "source_review")
        report = json.loads(state.detail) if state else {"no_leads": []}
        lines = [texts.REVIEW_LINE.format(id=row["id"], title=escape(row["title"])) for row in report["no_leads"]]
    await answer_lines(message, texts.REVIEW_HEADER, lines, accounts.settings.telegram_text_limit)
    await message.answer(texts.REVIEW_LIMITATION)


async def verify(
    message: Message, command: CommandObject, accounts: Accounts, reader_hub: ReaderHub | None = None
) -> None:
    growth = control(message, accounts, reader_hub)
    try:
        identity = int(command.args or "")
    except ValueError:
        raise AccountError(texts.CANDIDATE_CHECK_USAGE) from None
    status = await growth.verify_candidate(message.from_user.id, identity)
    await message.answer(texts.CANDIDATE_CHECK_RESULT.format(status=status))
