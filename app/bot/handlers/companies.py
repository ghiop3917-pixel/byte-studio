"""Ручной раздел владельца, без автоматической рассылки компаниям."""

from html import escape

from aiogram.filters.command import CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot import texts
from app.bot.accounts import AccountError, Accounts
from app.bot.handlers.menu import actor
from app.bot.keyboards import CompanyData
from app.companies.service import Companies, csv_bytes


def service(accounts):
    return Companies(accounts.database, accounts.settings)


def render_card(row, settings):
    raw = {key: str(value or texts.COMPANY_NOT_LISTED) for key, value in row.items()}
    text = texts.COMPANY_CARD.format(**{key: escape(value) for key, value in raw.items()})
    for key in ("description", "telegram", "max_links", "address", "name", "phones"):
        while len(text) > settings.telegram_text_limit and len(raw[key]) > 1:
            raw[key] = raw[key][: max(1, len(raw[key]) // 2)]
            text = texts.COMPANY_CARD.format(**{key: escape(value) for key, value in raw.items()})
    if len(text) > settings.telegram_text_limit:
        raise AccountError(texts.UI_ERROR)
    return text


async def home(message: Message, accounts: Accounts, owner: int) -> None:
    companies = service(accounts)
    states = await companies.status(owner)
    lines = []
    for state in states:
        state = {
            **state,
            "status": texts.COMPANY_STATUS_LABELS.get(state["status"], state["status"]),
            "error": texts.COMPANY_ERROR_LABELS.get(state["error"], state["error"]),
        }
        lines.append(texts.COMPANY_SCAN_LINE.format(**{key: escape(str(value)) for key, value in state.items()}))
    text = texts.COMPANIES_HOME.format(
        status="\n".join(lines) or texts.COMPANIES_NOT_SCANNED,
        enabled="да" if accounts.settings.company_monitor_enabled else "нет",
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.COMPANY_SCAN_BUTTON, callback_data=CompanyData(action="scan", after=0).pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.COMPANY_LIST_BUTTON, callback_data=CompanyData(action="list", after=0).pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.COMPANY_EXPORT_BUTTON, callback_data=CompanyData(action="export", after=0).pack()
                )
            ],
        ]
    )
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


async def show(message: Message, accounts: Accounts, owner: int, after: int = 0) -> None:
    rows = await service(accounts).records(owner, after)
    if not rows:
        await message.answer(texts.COMPANIES_EMPTY)
        return
    for row in rows:
        text = render_card(row, accounts.settings)
        links = [[InlineKeyboardButton(text=texts.COMPANY_SOURCE_BUTTON, url=row["source_url"])]]
        # Мессенджеры опубликованы в тексте; номер не превращаем в предполагаемый Telegram/MAX.
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=links),
            disable_web_page_preview=True,
        )
    if len(rows) == accounts.settings.company_page_size:
        await message.answer(
            texts.COMPANY_MORE,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.COMPANY_LIST_BUTTON,
                            callback_data=CompanyData(action="list", after=rows[-1]["id"]).pack(),
                        )
                    ]
                ]
            ),
        )


async def command(message: Message, command: CommandObject, accounts: Accounts) -> None:
    if message.from_user is None or message.chat.type != "private" or message.chat.id != message.from_user.id:
        raise AccountError(texts.ADMIN_ONLY)
    if command.command == "companies_scan":
        await service(accounts).enqueue(message.from_user.id)
        await message.answer(
            texts.COMPANIES_QUEUED_ACTIVE
            if accounts.settings.company_monitor_enabled
            else texts.COMPANIES_QUEUED_DISABLED
        )
    else:
        await home(message, accounts, message.from_user.id)


async def callback(callback: CallbackQuery, callback_data: CompanyData, accounts: Accounts) -> None:
    owner = actor(callback)
    companies = service(accounts)
    await companies.status(owner)
    await callback.answer()
    if callback_data.action == "home":
        await home(callback.message, accounts, owner)
    elif callback_data.action == "scan":
        await companies.enqueue(owner)
        await callback.message.answer(
            texts.COMPANIES_QUEUED_ACTIVE
            if accounts.settings.company_monitor_enabled
            else texts.COMPANIES_QUEUED_DISABLED
        )
    elif callback_data.action == "list":
        await show(callback.message, accounts, owner, callback_data.after)
    elif callback_data.action == "export":
        rows = await companies.records(owner, limit=accounts.settings.company_import_limit)
        await callback.message.answer_document(
            BufferedInputFile(csv_bytes(list(rows)), filename="companies-without-listed-website.csv"),
            caption=texts.COMPANY_EXPORT_NOTE,
        )
    else:
        raise AccountError(texts.OLD_CALLBACK)
