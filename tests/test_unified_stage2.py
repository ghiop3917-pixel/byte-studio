"""Этап 2: настоящий Dispatcher, SQLite в памяти и транспорт Telegram без сети."""

import tempfile
import unittest
from contextlib import nullcontext
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TelegramUser
from pydantic import SecretStr
from sqlalchemy import select

from app.bot import texts
from app.bot.accounts import AccountError, Accounts
from app.bot.handlers.menu import GeoInput
from app.bot.keyboards import CategoryData, GeoData, PageData, ScoreData, keyboard
from app.bot.router import create_router
from app.bot.runtime import instance_lock, run_interface, verify_schema
from app.bot.screens import screen
from app.config import Settings
from app.db.models import Lead, LeadDelivery, SourceChat, User, utc_now
from app.db.session import Database


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return TelegramUser(id=bot.id, is_bot=True, first_name="Тест", username="test_unified_bot")
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.calls),
                date=utc_now(),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
                reply_markup=method.reply_markup,
            )
        if isinstance(method, (EditMessageText, AnswerCallbackQuery)):
            return True
        raise AssertionError(type(method).__name__)

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("network forbidden")
        yield b""


class UnifiedInterfaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            telegram_session_paths=[Path("fake.session")],
            admin_ids=[9],
        )
        self.db = Database(self.settings, read_only=False, testing=True)
        await self.db.create_test_schema()
        self.accounts = Accounts(self.db, self.settings)
        self.transport = RecordingSession()
        self.bot = Bot("123456789:TEST", session=self.transport)
        self.dp = Dispatcher()
        self.dp.include_router(create_router(self.db, self.settings))
        self.sequence = 0

    async def asyncTearDown(self):
        await self.dp.storage.close()
        await self.bot.session.close()
        await self.db.close()

    async def message(self, text, actor=9, chat_type="private", name="Тест"):
        self.sequence += 1
        value = Message(
            message_id=self.sequence,
            date=utc_now(),
            chat=Chat(id=actor, type=chat_type),
            from_user=TelegramUser(id=actor, is_bot=False, first_name=name),
            text=text,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.sequence, message=value))

    async def callback(self, data, actor=9, chat_id=None):
        self.sequence += 1
        message = Message(message_id=1, date=utc_now(), chat=Chat(id=chat_id or actor, type="private"), text="menu")
        callback = CallbackQuery(
            id=str(self.sequence),
            from_user=TelegramUser(id=actor, is_bot=False, first_name="Тест"),
            chat_instance="test",
            message=message,
            data=data,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.sequence, callback_query=callback))

    async def test_start_trial_html_escape_and_default_category(self):
        await self.message("/start", name="<b>Владелец</b>")
        response = next(call for call in self.transport.calls if isinstance(call, SendMessage))
        self.assertIn("&lt;b&gt;Владелец&lt;/b&gt;", response.text)
        first = await self.accounts.view(9)
        self.assertEqual(sum(item[3] for item in first.categories), 1)
        await self.message("/start")
        self.assertEqual(first.subscription_until, (await self.accounts.view(9)).subscription_until)
        async with self.db.session() as session:
            self.assertTrue((await session.scalar(select(User).where(User.tg_id == 9))).is_admin)

    async def test_category_replay_is_idempotent_and_isolated(self):
        await self.message("/start")
        await self.message("/start", actor=10)
        view = await self.accounts.view(9)
        category_id = next(item[0] for item in view.categories if item[3])
        data = CategoryData(category_id=category_id, enabled=False).pack()
        await self.callback(data)
        await self.callback(data)
        self.assertEqual(sum(item[3] for item in (await self.accounts.view(9)).categories), 0)
        self.assertEqual(sum(item[3] for item in (await self.accounts.view(10)).categories), 1)
        await self.message("/start")
        self.assertEqual(sum(item[3] for item in (await self.accounts.view(9)).categories), 0)
        self.assertTrue(any(isinstance(call, EditMessageText) for call in self.transport.calls))

    async def test_score_geo_fsm_cancel_and_clear(self):
        await self.message("/start")
        await self.callback(ScoreData(value=70).pack())
        self.assertEqual((await self.accounts.view(9)).min_score, 70)
        await self.callback(GeoData(action="edit").pack())
        await self.message("<b>Россия</b>")
        self.assertEqual((await self.accounts.view(9)).geo_filter, "<b>Россия</b>")
        self.assertIn("&lt;b&gt;Россия&lt;/b&gt;", self.transport.calls[-1].text)
        await self.callback(GeoData(action="clear").pack())
        self.assertIsNone((await self.accounts.view(9)).geo_filter)
        await self.callback(GeoData(action="edit").pack())
        await self.message("/cancel")
        state = self.dp.fsm.get_context(bot=self.bot, chat_id=9, user_id=9)
        self.assertIsNone(await state.get_state())
        await self.callback(GeoData(action="edit").pack())
        await self.message("x" * (self.settings.geo_max_length + 1))
        self.assertEqual(await state.get_state(), GeoInput.value.state)
        self.assertIsNone((await self.accounts.view(9)).geo_filter)

    async def test_callbacks_do_not_allow_cross_account_or_invalid_settings(self):
        await self.message("/start")
        await self.message("/start", actor=10)
        await self.callback(ScoreData(value=90).pack(), actor=9, chat_id=10)
        self.assertEqual((await self.accounts.view(9)).min_score, 50)
        self.assertEqual((await self.accounts.view(10)).min_score, 50)
        await self.callback(ScoreData(value=99).pack())
        self.assertEqual((await self.accounts.view(9)).min_score, 50)
        await self.callback(CategoryData(category_id=999, enabled=True).pack())
        await self.callback(PageData(name="unknown").pack())
        errors = [call for call in self.transport.calls if isinstance(call, AnswerCallbackQuery) and call.show_alert]
        self.assertEqual(len(errors), 4)

    async def test_menu_subscription_about_and_all_callback_lengths(self):
        await self.message("/start")
        view = await self.accounts.view(9)
        for name in ("home", "categories", "settings", "subscription", "about", "stats"):
            await self.callback(PageData(name=name).pack())
            for row in keyboard(name, view, self.settings).inline_keyboard:
                for button in row:
                    self.assertLessEqual(len(button.callback_data.encode()), self.settings.callback_data_max_bytes)
        subscriptions = [
            call.text
            for call in self.transport.calls
            if isinstance(call, EditMessageText) and "Оплата пока" in call.text
        ]
        self.assertTrue(subscriptions)
        self.assertIn("не местонахождение заказчика", screen("about", view, self.settings))
        self.assertFalse(self.settings.payments_enabled)

    async def test_banned_and_group_start_do_not_create_or_change_accounts(self):
        await self.message("/start", chat_type="group")
        with self.assertRaises(AccountError):
            await self.accounts.view(9)
        await self.message("/start")
        async with self.db.session() as session:
            user = await session.scalar(select(User).where(User.tg_id == 9))
            user.is_banned = True
        await self.callback(ScoreData(value=90).pack())
        self.assertEqual(self.transport.calls[-1].text, texts.ACCOUNT_BANNED)
        await self.message("/start")
        self.assertEqual(self.transport.calls[-1].text, texts.ACCOUNT_BANNED)

    async def test_actual_statistics_are_scoped_to_user_and_time(self):
        await self.accounts.register(9, None)
        await self.accounts.register(10, None)
        view = await self.accounts.view(9)
        async with self.db.session() as session:
            users = {u.tg_id: u for u in (await session.scalars(select(User))).all()}
            source = SourceChat(tg_chat_id=-1001, title="Тест")
            session.add(source)
            await session.flush()
            for index, days in enumerate((0, 2, 8), 1):
                lead = Lead(
                    category_id=view.categories[0][0],
                    text="test",
                    summary="test",
                    score=50,
                    source_chat_id=source.id,
                    message_id=index,
                    text_hash=str(index) * 64,
                )
                session.add(lead)
                await session.flush()
                session.add(
                    LeadDelivery(
                        lead_id=lead.id, user_id=users[9].id, message_id=index, sent_at=utc_now() - timedelta(days=days)
                    )
                )
                if index == 1:
                    session.add(LeadDelivery(lead_id=lead.id, user_id=users[10].id, message_id=10))
        view = await self.accounts.view(9)
        self.assertEqual((view.received_day, view.received_week), (1, 2))
        self.assertEqual((await self.accounts.view(10)).received_week, 1)

    async def test_schema_inspection_and_exclusive_instance_lock(self):
        self.assertTrue(await verify_schema(self.db))
        from app.db.models import Base

        incomplete = Mock()
        incomplete.get_table_names.return_value = list(Base.metadata.tables)
        incomplete.get_columns.return_value = [{"name": "id"}]
        with patch("app.bot.runtime.inspect", return_value=incomplete):
            self.assertFalse(await verify_schema(self.db))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bot.lock"
            with instance_lock(path):
                with self.assertRaises(RuntimeError):
                    with instance_lock(path):
                        raise AssertionError("duplicate instance")
            with instance_lock(path):
                pass

    async def test_launch_refuses_missing_schema_before_writes_or_bot(self):
        self.settings.bot_token = SecretStr("123456789:TEST")
        inspection = Mock(close=AsyncMock())
        with (
            patch("app.bot.runtime.Database", return_value=inspection) as factory,
            patch("app.bot.runtime.verify_schema", new=AsyncMock(return_value=False)),
            patch("app.bot.runtime.Bot") as bot,
        ):
            with self.assertRaisesRegex(RuntimeError, "lh_"):
                await run_interface(self.settings)
            self.assertEqual(factory.call_count, 1)
            bot.assert_not_called()
            inspection.close.assert_awaited_once()

    async def test_launch_cleanup_with_fake_polling(self):
        self.settings.bot_token = SecretStr("123456789:TEST")
        inspection = Mock(close=AsyncMock())
        database = Mock(close=AsyncMock())
        bot = Mock(session=Mock(close=AsyncMock()))
        dispatcher = Mock(start_polling=AsyncMock(), storage=Mock(close=AsyncMock()))

        async def run_once(services, settings, stop):
            await services["bot"]()

        with (
            patch("app.bot.runtime.Database", side_effect=[inspection, database]),
            patch("app.bot.runtime.verify_schema", new=AsyncMock(return_value=True)),
            patch("app.bot.runtime.instance_lock", return_value=nullcontext()),
            patch("app.bot.runtime.Bot", return_value=bot),
            patch("app.bot.runtime.Dispatcher", return_value=dispatcher),
            patch("app.bot.runtime.run_services", new=run_once),
        ):
            with self.assertRaisesRegex(RuntimeError, "перезапусков"):
                await run_interface(self.settings)
        database.close.assert_awaited_once()
        bot.session.close.assert_awaited_once()
        dispatcher.storage.close.assert_awaited_once()


class UnifiedCommandTests(unittest.TestCase):
    def test_bot_entry_reports_failure_without_network(self):
        from app.main import main

        cfg = Settings(_env_file=None, telegram_session_paths=[Path("fake.session")])
        with (
            patch("app.config.get_settings", return_value=cfg),
            patch("app.bot.runtime.run_interface", new=AsyncMock(side_effect=RuntimeError(texts.SCHEMA_NOT_READY))),
            patch("sys.stdout", new_callable=StringIO) as output,
        ):
            self.assertEqual(main(["--bot"]), 1)
            self.assertIn("lh_", output.getvalue())
