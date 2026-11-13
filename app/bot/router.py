"""Router aiogram 3 и безопасная обработка ошибок пользовательских действий."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot import texts
from app.bot.accounts import AccountError, Accounts
from app.bot.handlers import admin, companies, discovery, leads, menu, start
from app.bot.keyboards import (
    CandidateData,
    CandidatesPageData,
    CategoryData,
    CompanyData,
    GeoData,
    LeadActionData,
    PageData,
    ScoreData,
)
from app.config import Settings
from app.db.session import Database


class ContextMiddleware(BaseMiddleware):
    def __init__(self, accounts: Accounts) -> None:
        self.accounts = accounts

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["accounts"] = self.accounts
        try:
            return await handler(event, data)
        except Exception as error:
            logging.error(texts.UI_ERROR_LOG, type(error).__name__)
            answer = str(error) if isinstance(error, AccountError) else texts.UI_ERROR
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer(answer, show_alert=True)
                elif isinstance(event, Message):
                    await event.answer(answer, parse_mode=None)
            except Exception as secondary:
                logging.error(texts.UI_SECONDARY_LOG, type(secondary).__name__)
            return None


def create_router(database: Database, settings: Settings) -> Router:
    router = Router(name="unified_interface")
    middleware = ContextMiddleware(Accounts(database, settings))
    router.message.middleware(middleware)
    router.callback_query.middleware(middleware)
    router.message.register(start.start, CommandStart())
    router.message.register(start.menu, Command("menu", "cancel"))
    router.callback_query.register(menu.navigate, PageData.filter())
    router.callback_query.register(menu.category, CategoryData.filter())
    router.callback_query.register(menu.score, ScoreData.filter())
    router.callback_query.register(menu.geo, GeoData.filter())
    router.message.register(admin.add_chat, Command("add_chat"))
    router.message.register(admin.remove_chat, Command("remove_chat"))
    router.message.register(admin.chats, Command("chats"))
    router.message.register(admin.keywords, Command("keywords"))
    router.message.register(discovery.discover, Command("discover"))
    router.message.register(discovery.candidates, Command("candidates"))
    router.message.register(discovery.verify, Command("candidate_check"))
    router.message.register(discovery.resume, Command("join_resume"))
    router.message.register(discovery.geo, Command("chat_geo"))
    router.message.register(discovery.review, Command("source_review"))
    router.callback_query.register(discovery.decision, CandidateData.filter())
    router.callback_query.register(discovery.page, CandidatesPageData.filter())
    router.callback_query.register(leads.action, LeadActionData.filter())
    router.message.register(companies.command, Command("companies", "companies_scan"))
    router.callback_query.register(companies.callback, CompanyData.filter())
    router.message.register(companies.city_input, companies.CompanySearch.city)
    router.message.register(companies.niche_input, companies.CompanySearch.niche)
    router.message.register(menu.save_geo, menu.GeoInput.value)
    return router
