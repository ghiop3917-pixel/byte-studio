"""Callback карточек: контакт — URL, отклик — только черновик владельцу карточки."""

import logging

from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.accounts import AccountError, Accounts
from app.bot.keyboards import LeadActionData
from app.pipeline.classifier import LLM
from app.services_leads import LeadActions


async def action(
    callback: CallbackQuery, callback_data: LeadActionData, accounts: Accounts, llm: LLM | None = None
) -> None:
    if (
        not isinstance(callback.message, Message)
        or callback.message.chat.type != "private"
        or callback.message.chat.id != callback.from_user.id
    ):
        raise AccountError(texts.LEAD_ACCESS_DENIED)
    service = LeadActions(accounts.database, accounts.settings, llm)
    if callback_data.action == "draft":
        # Проверяем доступ до подтверждения callback и любого обращения к LLM.
        async with accounts.database.session() as session:
            await service.access(session, callback.from_user.id, callback_data.lead_id, subscribed=True)
        await callback.answer(texts.DRAFT_GENERATING)
        try:
            draft = await service.draft(callback.from_user.id, callback_data.lead_id)
        except AccountError as error:
            await callback.message.answer(str(error), parse_mode=None)
        except Exception as error:
            logging.error("draft: %s", type(error).__name__)
            await callback.message.answer(texts.DRAFT_FAILED, parse_mode=None)
        else:
            await callback.message.answer(texts.DRAFT_RESULT.format(text=draft), parse_mode=None)
    else:
        await service.feedback(callback.from_user.id, callback_data.lead_id, callback_data.action)
        await callback.answer(texts.FEEDBACK_SAVED)
        if callback_data.action == "spam" and callback.message.reply_markup:
            rows = [
                [
                    button.model_copy(update={"text": texts.BTN_SPAM_MARKED})
                    if button.callback_data == callback_data.pack()
                    else button
                    for button in row
                ]
                for row in callback.message.reply_markup.inline_keyboard
            ]
            markup = callback.message.reply_markup.model_copy(update={"inline_keyboard": rows})
            if markup != callback.message.reply_markup:
                await callback.message.edit_reply_markup(reply_markup=markup)
