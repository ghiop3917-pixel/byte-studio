"""/start и /menu: регистрация без продления уже выданного trial."""

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot import texts
from app.bot.accounts import Accounts
from app.bot.keyboards import keyboard
from app.bot.screens import screen


async def start(message: Message, state: FSMContext, accounts: Accounts) -> None:
    await state.clear()
    if message.from_user is None or message.chat.type != "private" or message.chat.id != message.from_user.id:
        await message.answer(texts.PRIVATE_ONLY)
        return
    view = await accounts.register(message.from_user.id, message.from_user.username)
    await message.answer(
        screen("welcome", view, accounts.settings, name=message.from_user.full_name),
        reply_markup=keyboard("home", view, accounts.settings),
        parse_mode="HTML",
    )


async def menu(message: Message, state: FSMContext, accounts: Accounts) -> None:
    await state.clear()
    if message.from_user is None or message.chat.type != "private" or message.chat.id != message.from_user.id:
        await message.answer(texts.PRIVATE_ONLY)
        return
    view = await accounts.view(message.from_user.id)
    await message.answer(
        screen("home", view, accounts.settings),
        reply_markup=keyboard("home", view, accounts.settings),
        parse_mode="HTML",
    )
