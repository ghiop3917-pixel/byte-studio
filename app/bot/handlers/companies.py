"""Выбор города/сферы владельцем и отдельная выдача каждого поиска."""

from html import escape

from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot import texts
from app.bot.accounts import AccountError, Accounts
from app.bot.handlers.menu import actor
from app.bot.keyboards import CompanyData
from app.companies.parsers import search_value
from app.companies.service import Companies, csv_bytes


class CompanySearch(StatesGroup):
    city = State()
    niche = State()


def service(accounts, company_service=None):
    return company_service or Companies(accounts.database, accounts.settings)


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


def owner(message):
    if message.from_user is None or message.chat.type != "private" or message.chat.id != message.from_user.id:
        raise AccountError(texts.ADMIN_ONLY)
    return message.from_user.id


async def home(message, accounts, owner_id, state, company_service=None):
    companies = service(accounts, company_service)
    data = await state.get_data()
    city, niche = data.get("company_city"), data.get("company_niche")
    states = await companies.status(owner_id, city, niche)
    if not city or not niche:
        states = ()
    lines = []
    for item in states:
        item = {
            **item,
            "status": texts.COMPANY_STATUS_LABELS.get(item["status"], item["status"]),
            "error": texts.COMPANY_ERROR_LABELS.get(item["error"], item["error"]),
        }
        lines.append(texts.COMPANY_SCAN_LINE.format(**{key: escape(str(value)) for key, value in item.items()}))
    scope = (
        texts.COMPANY_SELECTED.format(city=escape(city), niche=escape(niche))
        if city and niche
        else texts.COMPANY_NO_SELECTION
    )
    text = texts.COMPANIES_HOME.format(
        scope=scope,
        status="\n".join(lines) or texts.COMPANIES_NOT_SCANNED,
        enabled="да" if accounts.settings.company_monitor_enabled else "нет",
    )
    buttons = [
        [InlineKeyboardButton(text=texts.COMPANY_SCAN_BUTTON, callback_data=CompanyData(action="scan", after=0).pack())]
    ]
    if city and niche:
        buttons.extend(
            [
                [InlineKeyboardButton(text=label, callback_data=CompanyData(action=action, after=0).pack())]
                for label, action in (
                    (texts.COMPANY_LIST_BUTTON, "list"),
                    (texts.COMPANY_REFRESH_BUTTON, "refresh"),
                    (texts.COMPANY_EXPORT_BUTTON, "export"),
                )
            ]
        )
    for item in await companies.recent(owner_id):
        buttons.append(
            [
                InlineKeyboardButton(
                    text=(item["city"] + " · " + item["niche"])[:60],
                    callback_data=CompanyData(action="recent", after=item["id"]).pack(),
                )
            ]
        )
    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def begin(message, accounts, owner_id, state, company_service=None):
    await service(accounts, company_service).status(owner_id)
    await state.clear()
    await state.set_state(CompanySearch.city)
    await message.answer(texts.COMPANY_CITY_PROMPT)


async def show(message, accounts, owner_id, state, after=0, company_service=None):
    data = await state.get_data()
    city, niche = data.get("company_city"), data.get("company_niche")
    if not city or not niche:
        await begin(message, accounts, owner_id, state, company_service)
        return
    rows = await service(accounts, company_service).records(owner_id, after, city=city, niche=niche)
    if not rows:
        await message.answer(texts.COMPANIES_EMPTY)
        return
    for row in rows:
        await message.answer(
            render_card(row, accounts.settings),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=texts.COMPANY_SOURCE_BUTTON, url=row["source_url"])]]
            ),
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


async def launch(message, accounts, owner_id, state, company_service=None, *, refresh=False):
    data = await state.get_data()
    city, niche = data.get("company_city"), data.get("company_niche")
    if not city or not niche:
        await begin(message, accounts, owner_id, state, company_service)
        return
    companies = service(accounts, company_service)
    await companies.enqueue(owner_id, city, niche, refresh=refresh)
    await state.set_state(None)
    await message.answer(
        texts.COMPANY_STARTED.format(city=city, niche=niche)
        if accounts.settings.company_monitor_enabled
        else texts.COMPANIES_QUEUED_DISABLED,
        parse_mode=None,
    )
    cached = await companies.records(owner_id, city=city, niche=niche)
    if not cached and accounts.settings.company_monitor_enabled and companies.running:
        cached = await companies.wait_for_results(
            owner_id, city, niche, accounts.settings.company_first_results_wait_seconds
        )
    if cached:
        await message.answer(texts.COMPANY_CACHED)
        await show(message, accounts, owner_id, state, company_service=company_service)
    await home(message, accounts, owner_id, state, company_service)


async def command(
    message: Message, command: CommandObject, accounts: Accounts, state: FSMContext, company_service=None
):
    owner_id = owner(message)
    if command.command == "companies_scan":
        if command.args and "|" in command.args:
            try:
                city, niche = command.args.split("|", 1)
                city, niche = search_value(city, 64), search_value(niche, 100)
            except ValueError:
                raise AccountError(texts.COMPANIES_BAD_SCOPE) from None
            await service(accounts, company_service).status(owner_id)
            await state.update_data(company_city=city, company_niche=niche)
            await launch(message, accounts, owner_id, state, company_service)
        else:
            await begin(message, accounts, owner_id, state, company_service)
    else:
        await home(message, accounts, owner_id, state, company_service)


async def city_input(message: Message, accounts: Accounts, state: FSMContext, company_service=None):
    await service(accounts, company_service).status(owner(message))
    try:
        city = search_value(message.text or "", 64)
    except ValueError:
        raise AccountError(texts.COMPANY_CITY_PROMPT) from None
    await state.update_data(company_city=city)
    await state.set_state(CompanySearch.niche)
    await message.answer(texts.COMPANY_NICHE_PROMPT)


async def niche_input(message: Message, accounts: Accounts, state: FSMContext, company_service=None):
    owner_id = owner(message)
    await service(accounts, company_service).status(owner_id)
    try:
        niche = search_value(message.text or "", 100)
    except ValueError:
        raise AccountError(texts.COMPANY_NICHE_PROMPT) from None
    await state.update_data(company_niche=niche)
    await launch(message, accounts, owner_id, state, company_service)


async def callback(
    callback: CallbackQuery, callback_data: CompanyData, accounts: Accounts, state: FSMContext, company_service=None
):
    owner_id = actor(callback)
    companies = service(accounts, company_service)
    await companies.status(owner_id)
    await callback.answer()
    if callback_data.action == "home":
        await home(callback.message, accounts, owner_id, state, company_service)
    elif callback_data.action == "scan":
        await begin(callback.message, accounts, owner_id, state, company_service)
    elif callback_data.action == "refresh":
        await launch(callback.message, accounts, owner_id, state, company_service, refresh=True)
    elif callback_data.action == "recent":
        city, niche = await companies.scope(owner_id, callback_data.after)
        await state.update_data(company_city=city, company_niche=niche)
        await state.set_state(None)
        await home(callback.message, accounts, owner_id, state, company_service)
        await show(callback.message, accounts, owner_id, state, company_service=company_service)
    elif callback_data.action == "list":
        await show(callback.message, accounts, owner_id, state, callback_data.after, company_service)
    elif callback_data.action == "export":
        data = await state.get_data()
        city, niche = data.get("company_city"), data.get("company_niche")
        if not city or not niche:
            await begin(callback.message, accounts, owner_id, state, company_service)
            return
        rows = await companies.records(owner_id, limit=accounts.settings.company_import_limit, city=city, niche=niche)
        await callback.message.answer_document(
            BufferedInputFile(csv_bytes(list(rows)), filename="companies-without-listed-website.csv"),
            caption=texts.COMPANY_EXPORT_NOTE,
        )
    else:
        raise AccountError(texts.OLD_CALLBACK)
