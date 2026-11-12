"""Меню и настройки. Callback никогда не задаёт ID владельца записи."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.accounts import AccountError, Accounts, AccountView
from app.bot.keyboards import CategoryData, GeoData, PageData, ScoreData, keyboard
from app.bot.screens import screen


class GeoInput(StatesGroup):
    value = State()


def actor(callback: CallbackQuery) -> int:
    message = callback.message
    if not isinstance(message, Message) or message.chat.type != "private" or message.chat.id != callback.from_user.id:
        raise AccountError(texts.OLD_CALLBACK)
    return callback.from_user.id


async def edit(callback: CallbackQuery, page: str, view: AccountView, accounts: Accounts) -> None:
    try:
        await callback.message.edit_text(
            screen(page, view, accounts.settings),
            reply_markup=keyboard(page, view, accounts.settings),
            parse_mode="HTML",
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in error.message.casefold():
            raise
    await callback.answer()


async def navigate(callback: CallbackQuery, callback_data: PageData, state: FSMContext, accounts: Accounts) -> None:
    tg_id = actor(callback)
    await state.clear()
    view = await accounts.view(tg_id)
    await edit(callback, callback_data.name, view, accounts)


async def category(callback: CallbackQuery, callback_data: CategoryData, state: FSMContext, accounts: Accounts) -> None:
    await state.clear()
    view = await accounts.set_category(actor(callback), callback_data.category_id, callback_data.enabled)
    await edit(callback, "categories", view, accounts)


async def score(callback: CallbackQuery, callback_data: ScoreData, state: FSMContext, accounts: Accounts) -> None:
    await state.clear()
    view = await accounts.set_score(actor(callback), callback_data.value)
    await edit(callback, "settings", view, accounts)


async def geo(callback: CallbackQuery, callback_data: GeoData, state: FSMContext, accounts: Accounts) -> None:
    tg_id = actor(callback)
    await accounts.view(tg_id)
    if callback_data.action == "edit":
        await state.set_state(GeoInput.value)
        await callback.message.edit_text(texts.GEO_PROMPT.format(limit=accounts.settings.geo_max_length))
        await callback.answer()
    elif callback_data.action == "clear":
        await state.clear()
        view = await accounts.set_geo(tg_id, None)
        await edit(callback, "settings", view, accounts)
    else:
        raise AccountError(texts.OLD_CALLBACK)


async def save_geo(message: Message, state: FSMContext, accounts: Accounts) -> None:
    if message.from_user is None or message.chat.type != "private" or message.chat.id != message.from_user.id:
        return
    if not message.text or message.text.startswith("/"):
        raise AccountError(texts.GEO_INVALID.format(limit=accounts.settings.geo_max_length))
    view = await accounts.set_geo(message.from_user.id, message.text)
    await state.clear()
    await message.answer(
        screen("settings", view, accounts.settings),
        reply_markup=keyboard("settings", view, accounts.settings),
        parse_mode="HTML",
    )
