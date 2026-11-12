"""Этап 3: сообщения и восстановление, без внешних API и рабочей SQLite."""

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TelegramUser
from pydantic import SecretStr
from sqlalchemy import func, select
from telethon import errors, types, utils

from app.bot.accounts import AccountError, Accounts
from app.bot.handlers.admin import answer_lines
from app.bot.router import create_router
from app.bot.runtime import run_application
from app.config import Settings
from app.db.models import (
    AuthorBlacklist,
    ChatCandidate,
    IngestJob,
    Keyword,
    Lead,
    RawMessage,
    ReaderState,
    SourceChat,
    SourceRuntime,
    User,
    utc_now,
)
from app.db.repo import Repository
from app.db.session import Database
from app.pipeline.ingestion import IncomingMessage, Ingestion
from app.pipeline.prefilter import KeywordRule, prefilter, validate_keyword
from app.services_sources import AdminSources
from app.userbot.client import (
    ReaderAuthorizationError,
    ReaderCooldown,
    ReaderHub,
    ReaderRPC,
    ReadOnlyTelegramClient,
    TelegramReader,
    run_reader,
    snapshot,
)
from app.utils.links import message_link, parse_chat_reference
from app.utils.text import normalize_text, text_hash
from login import login

TEXT = "Ищу исполнителя: нужен сайт для компании, обсудим стоимость и сроки."


def group(number=1234, **kwargs):
    return types.Channel(
        id=number,
        title="Бизнес & проекты",
        photo=types.ChatPhotoEmpty(),
        date=utc_now(),
        megagroup=True,
        broadcast=False,
        username="business_test",
        access_hash=17,
        **kwargs,
    )


def message(number, **kwargs):
    values = dict(
        id=number,
        raw_text=TEXT,
        date=utc_now(),
        from_id=types.PeerUser(5),
        sender=types.User(id=5, first_name="Иван", username="ivan_test"),
        action=None,
        fwd_from=None,
        reply_to=None,
        media=None,
    )
    values.update(kwargs)
    return SimpleNamespace(**values)


class TextTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None)

    def test_normalization_and_hash(self):
        self.assertEqual(normalize_text("САЙТ 🔥 https://t.me/test/3\n CRM"), "сайт crm")
        self.assertEqual(text_hash("Сайт 🔥 https://a.test"), text_hash("  САЙТ  "))
        self.assertNotEqual(text_hash("CRM"), text_hash("сайт"))

    def test_links_and_references(self):
        self.assertEqual(message_link(-10012345, "business", 7), "https://t.me/business/7")
        self.assertEqual(message_link(-10012345, None, 7, 2), "https://t.me/c/12345/2/7")
        self.assertEqual(message_link(-10012345, None, 7, 7), "https://t.me/c/12345/7")
        self.assertIsNone(message_link(-55, None, 7))
        self.assertIsNone(message_link(-10012345, None, 0))
        for value in ("@business", "https://t.me/business/7", "t.me/business"):
            self.assertEqual(parse_chat_reference(value).username, "business")
        self.assertEqual(parse_chat_reference("t.me/+Private_hash").invite_hash, "Private_hash")
        self.assertEqual(parse_chat_reference("t.me/joinchat/Private_hash").invite_hash, "Private_hash")
        for value in ("https://evil.test/t.me/business", "javascript://t.me/business", "@bad/name"):
            with self.assertRaises(ValueError):
                parse_chat_reference(value)

    def test_prefilter_rules_and_unsafe_regex(self):
        rules = (KeywordRule("web_dev", "сайт", "include"), KeywordRule("web_dev", "услуги", "exclude"))
        self.assertTrue(prefilter(TEXT, {}, rules, self.settings).accepted)
        self.assertFalse(prefilter(TEXT + " предлагаю услуги", {}, rules, self.settings).accepted)
        for metadata, reason in (
            ({"service": True}, "service"),
            ({"is_bot": True}, "bot"),
            ({"forwarded": True}, "forwarded"),
        ):
            self.assertEqual(prefilter(TEXT, metadata, rules, self.settings).reason, reason)
        self.assertEqual(prefilter("сайт", {}, rules, self.settings).reason, "short")
        self.assertTrue(
            prefilter(TEXT, {}, (KeywordRule("web_dev", "сайт.*", "include", True),), self.settings).accepted
        )
        for pattern in ("(a+)+", "a|b", "a{3}", r"a\1", "a*a+"):
            with self.assertRaises(ValueError):
                validate_keyword(pattern, True, self.settings)


class IngestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings(
            _env_file=None, database_url="sqlite+aiosqlite:///:memory:", admin_ids=[9], reader_rpc_interval_seconds=0
        )
        self.db = Database(self.settings, read_only=False, testing=True)
        await self.db.create_test_schema()
        self.storage = Ingestion(self.db, self.settings)
        await self.storage.seed_keywords()
        async with self.db.session() as session:
            await Repository(session, self.settings).upsert_source(
                utils.get_peer_id(group()), "Бизнес", username="business_test"
            )
            await Repository(session, self.settings).upsert_source(
                utils.get_peer_id(group(5678)), "Проекты", username="projects_test"
            )
        self.sources = await self.storage.active_sources()
        self.source = self.sources[0]

    async def asyncTearDown(self):
        await self.db.close()

    def incoming(self, number=1, **kwargs):
        return IncomingMessage(self.source.chat_id, number, TEXT, **kwargs)

    async def raw(self, identity):
        async with self.db.session() as session:
            return await session.get(RawMessage, identity)

    async def job(self, identity):
        async with self.db.session() as session:
            return await session.scalar(select(IngestJob).where(IngestJob.raw_id == identity))

    async def test_original_before_filter_and_exact_concurrent_dedup(self):
        incoming = replace(self.incoming(metadata={"is_bot": True}), text="сайт")
        identities = await asyncio.gather(*(self.storage.ingest(self.source.id, incoming) for _ in range(3)))
        self.assertEqual(len(set(identities)), 1)
        raw = await self.raw(identities[0])
        self.assertEqual((raw.original_text, raw.status), ("сайт", "pending"))
        self.assertEqual(json.loads(raw.payload), {"is_bot": True})
        await self.storage.process_one()
        raw = await self.raw(raw.id)
        self.assertEqual((raw.status, raw.reason), ("filtered", "bot"))
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(IngestJob)), 1)
        self.assertEqual(await self.storage.history_state(self.source.id), (None, True))
        await self.storage.ingest(self.source.id, incoming, history=True)
        self.assertEqual((await self.storage.history_state(self.source.id))[0], 1)

    async def test_candidate_waits_without_fake_lead_or_send_and_requeue(self):
        raw_id = await self.storage.ingest(self.source.id, self.incoming(author_tg_id=5))
        await self.storage.process_one()
        raw = await self.raw(raw_id)
        job = await self.job(raw_id)
        self.assertEqual((raw.status, raw.delivery_mode, raw.category_hint), ("candidate", "dry_run", "web_dev"))
        self.assertEqual((job.phase, job.status), ("classifier", "pending"))
        self.assertFalse(await self.storage.process_one())
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(Lead)), 0)
        await self.storage.requeue(raw_id)
        await self.storage.process_one()
        self.assertEqual((await self.raw(raw_id)).status, "candidate")

    async def test_crossposting_window_and_unknown_authors(self):
        now = utc_now()
        examples = (
            self.incoming(1, author_tg_id=5, published_at=now),
            IncomingMessage(self.sources[1].chat_id, 1, TEXT + " https://t.me/example", now + timedelta(hours=1), 5),
            self.incoming(3, author_tg_id=5, published_at=now + timedelta(hours=25)),
            self.incoming(4, author_tg_id=6, published_at=now),
            self.incoming(5, published_at=now),
            self.incoming(6, published_at=now),
        )
        statuses = []
        for incoming in examples:
            source = next(row for row in self.sources if row.chat_id == incoming.chat_id)
            identity = await self.storage.ingest(source.id, incoming)
            await self.storage.process_one()
            statuses.append((await self.raw(identity)).status)
        self.assertEqual(statuses, ["candidate", "duplicate", "candidate", "candidate", "candidate", "candidate"])

    async def test_out_of_order_history_still_deduplicates_old_crossposts(self):
        now = utc_now()
        ids = []
        for number, hours in ((1, 0), (2, -72), (3, -71)):
            identity = await self.storage.ingest(
                self.source.id, self.incoming(number, author_tg_id=5, published_at=now + timedelta(hours=hours))
            )
            await self.storage.process_one()
            ids.append(identity)
        self.assertEqual(
            [(await self.raw(identity)).status for identity in ids], ["candidate", "candidate", "duplicate"]
        )

    async def test_blacklist_and_disabled_source(self):
        identity = await self.storage.ingest(self.source.id, self.incoming(author_tg_id=5))
        async with self.db.session() as session:
            session.add(AuthorBlacklist(author_tg_id=5, reason="ручная проверка"))
        await self.storage.process_one()
        self.assertEqual((await self.raw(identity)).reason, "blacklist")
        identity = await self.storage.ingest(self.source.id, self.incoming(2))
        async with self.db.session() as session:
            (await session.get(SourceChat, self.source.id)).is_active = False
        await self.storage.process_one()
        self.assertEqual((await self.raw(identity)).reason, "source_disabled")
        self.assertIsNone(await self.storage.ingest(self.source.id, self.incoming(3)))

    async def test_expired_lease_and_stale_token_and_retry_limit(self):
        identity = await self.storage.ingest(self.source.id, self.incoming())
        old = await self.storage.claim()
        with self.assertRaises(ValueError):
            await self.storage.requeue(identity)
        await self.storage.recover(utc_now() + timedelta(minutes=3))
        # Возвращаем due_at к настоящему времени после симуляции будущего.
        async with self.db.session() as session:
            (await session.get(IngestJob, old.id)).due_at = utc_now() - timedelta(seconds=1)
        current = await self.storage.claim()
        self.assertNotEqual(old.token, current.token)
        await self.storage.retry(old, OSError())
        self.assertEqual((await self.job(identity)).token, current.token)
        await self.storage.retry(current, OSError())
        paused = await self.job(identity)
        self.assertEqual(paused.status, "retry_wait")
        self.assertGreater(paused.due_at, utc_now())
        async with self.db.session() as session:
            (await session.get(IngestJob, old.id)).due_at = utc_now() - timedelta(seconds=1)
        last = await self.storage.claim()
        await self.storage.retry(last, ValueError())
        self.assertEqual((await self.job(identity)).status, "failed")
        self.assertEqual((await self.raw(identity)).status, "failed")
        self.assertEqual(len(json.loads((await self.job(identity)).errors)), 3)
        self.assertIsNone(await self.storage.claim())

    async def test_prefilter_exception_keeps_original_for_retry(self):
        identity = await self.storage.ingest(self.source.id, self.incoming())
        with patch("app.pipeline.ingestion.prefilter", side_effect=ValueError("test")):
            self.assertTrue(await self.storage.process_one())
        self.assertEqual((await self.raw(identity)).original_text, TEXT)
        self.assertEqual((await self.job(identity)).status, "retry_wait")

    async def test_restart_from_temporary_backup_preserves_cursor_and_queue(self):
        identity = await self.storage.ingest(self.source.id, self.incoming(), history=True)
        claim = await self.storage.claim()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "test.sqlite3"
            # Только тестовая база в памяти: Backup API создаёт временную фикстуру.
            async with self.db.engine.connect() as connection:
                driver = (await connection.get_raw_connection()).driver_connection
                destination = sqlite3.connect(target)
                try:
                    await driver.backup(destination)
                finally:
                    destination.close()
            settings = self.settings.model_copy(update={"database_url": "sqlite+aiosqlite:///" + target.as_posix()})
            restarted = Database(settings, read_only=False)
            try:
                storage = Ingestion(restarted, settings)
                self.assertEqual((await storage.history_state(self.source.id))[0], 1)
                self.assertEqual(await storage.recover(utc_now() + timedelta(minutes=3)), 1)
                async with restarted.session() as session:
                    job = await session.get(IngestJob, claim.id)
                    job.due_at = utc_now() - timedelta(seconds=1)
                self.assertTrue(await storage.process_one())
                self.assertEqual(await storage.ingest(self.source.id, self.incoming()), identity)
                async with restarted.session() as session:
                    self.assertEqual(await session.scalar(select(func.count()).select_from(RawMessage)), 1)
            finally:
                await restarted.close()

    async def test_source_backoff_heartbeat_and_terminal_failure(self):
        await self.storage.source_error(self.source.id, OSError())
        async with self.db.session() as session:
            first = await session.get(SourceRuntime, self.source.id)
            first_due = first.due_at
        await self.storage.source_error(self.source.id, OSError())
        async with self.db.session() as session:
            second = await session.get(SourceRuntime, self.source.id)
            self.assertGreater(second.due_at, first_due)
        for _ in range(self.settings.reader_source_max_attempts):
            await self.storage.source_error(self.source.id, OSError())
        self.assertFalse((await self.storage.history_state(self.source.id))[1])
        await self.storage.heartbeat("reader", "2")
        async with self.db.session() as session:
            self.assertEqual((await session.get(ReaderState, "reader")).detail, "2")
        task = asyncio.create_task(self.storage.processing_loop())
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_floodwait_persists_across_rpc_instances(self):
        rpc = ReaderRPC(self.storage, self.settings)
        action = AsyncMock(side_effect=errors.FloodWaitError(request=None, capture=60))
        with self.assertRaises(errors.FloodWaitError):
            await rpc.call("history", action)
        with self.assertRaises(ReaderCooldown):
            await ReaderRPC(self.storage, self.settings).call("history", action)
        self.assertEqual(action.await_count, 1)
        self.assertEqual(await rpc.call("resolve", AsyncMock(return_value=7)), 7)

    async def test_dynamic_event_cache_and_history_catchup(self):
        client = SimpleNamespace(
            get_entity=AsyncMock(return_value=group()),
            get_messages=AsyncMock(return_value=[message(3), message(1), message(2)]),
        )
        reader = TelegramReader(client, self.storage, self.settings)
        await reader.refresh()
        await reader.on_message(SimpleNamespace(chat_id=55, message=message(99)))
        await reader.on_message(SimpleNamespace(chat_id=self.source.chat_id, message=message(9)))
        self.assertEqual((await self.storage.history_state(self.source.id))[0], None)
        await reader.collect_history(self.source)
        self.assertEqual((await self.storage.history_state(self.source.id))[0], 3)
        client.get_messages.assert_awaited_once()
        client.get_messages.assert_awaited_with(reader.entities[self.source.chat_id], limit=100)

        async def iterate(entity, **kwargs):
            self.assertEqual(kwargs, dict(min_id=3, reverse=True, limit=100))
            yield message(4)
            yield message(9)

        client.iter_messages = iterate
        async with self.db.session() as session:
            (await session.get(SourceRuntime, self.source.id)).due_at = utc_now() - timedelta(seconds=1)
        await reader.collect_history(self.source)
        self.assertEqual((await self.storage.history_state(self.source.id))[0], 9)
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(RawMessage)), 5)
            (await session.get(SourceChat, self.source.id)).is_active = False
        await reader.refresh()
        await reader.on_message(SimpleNamespace(chat_id=self.source.chat_id, message=message(10)))
        self.assertNotIn(self.source.chat_id, reader.sources)

    async def test_one_group_failure_does_not_stop_other_group(self):
        async def resolve(reference):
            if reference == "@business_test":
                raise errors.ChannelPrivateError(request=None)
            return group(5678)

        client = SimpleNamespace(
            get_entity=AsyncMock(side_effect=resolve), get_messages=AsyncMock(return_value=[message(1)])
        )
        reader = TelegramReader(client, self.storage, self.settings)
        await reader.refresh()
        for source in tuple(reader.sources.values()):
            await reader.collect_history(source)
        async with self.db.session() as session:
            self.assertFalse((await session.get(SourceChat, self.source.id)).is_active)
            rows = (await session.scalars(select(RawMessage))).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].chat_id, self.sources[1].chat_id)

    async def test_reassigned_username_does_not_read_wrong_group(self):
        client = SimpleNamespace(get_entity=AsyncMock(return_value=group(9999)), get_messages=AsyncMock())
        reader = TelegramReader(client, self.storage, self.settings)
        await reader.collect_history(self.source)
        client.get_messages.assert_not_awaited()
        async with self.db.session() as session:
            self.assertEqual((await session.get(SourceRuntime, self.source.id)).error, "ValueError")

    async def test_admin_permissions_source_lifecycle_and_keywords(self):
        accounts = Accounts(self.db, self.settings)
        await accounts.register(9, "admin_test")
        await accounts.register(10, "guest_test")
        hub = SimpleNamespace(resolve=AsyncMock(return_value=group()), refresh=AsyncMock())
        admin = AdminSources(self.db, self.settings, hub)
        with self.assertRaises(AccountError):
            await admin.add_chat(10, "@business_test")
        hub.resolve.assert_not_awaited()
        self.assertEqual((await admin.add_chat(9, "@business_test"))[0], "added")
        self.assertEqual(len(await admin.chats(9)), 2)
        identity = await self.storage.ingest(self.source.id, self.incoming())
        await admin.remove_chat(9, self.source.chat_id)
        self.assertEqual((await self.raw(identity)).original_text, TEXT)
        self.assertEqual((await admin.chats(9))[0]["status"], "removed")
        await admin.add_chat(9, "t.me/+private_hash")
        await admin.add_chat(9, "t.me/+private_hash")
        hub.resolve.return_value = group(3333, left=True)
        self.assertEqual((await admin.add_chat(9, "@another_group"))[0], "pending")
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(ChatCandidate)), 2)
        # Ключевые слова не удаляются физически и не включаются повторным сидом.
        rows = await admin.keywords(9, "seo", action="add", kind="include", word="кастомный запрос")
        identity = next(row["id"] for row in rows if row["word"] == "кастомный запрос")
        await admin.keywords(9, "seo", action="remove", keyword_id=identity)
        await self.storage.seed_keywords()
        async with self.db.session() as session:
            self.assertFalse((await session.get(Keyword, identity)).is_active)
        with self.assertRaises(AccountError):
            await admin.keywords(9, "seo", action="add", kind="include", word="(a+)+", regex=True)
        hub.resolve.return_value = types.User(id=44, first_name="Not a group")
        with self.assertRaises(AccountError):
            await admin.add_chat(9, "@another_group")

    async def test_admin_commands_through_actual_dispatcher(self):
        calls = []

        class Transport(BaseSession):
            async def close(self):
                pass

            async def make_request(self, bot, method, timeout=None):
                if not isinstance(method, SendMessage):
                    raise AssertionError(type(method).__name__)
                calls.append(method)
                return Message(
                    message_id=len(calls),
                    date=utc_now(),
                    chat=Chat(id=int(method.chat_id), type="private"),
                    text=method.text,
                )

            async def stream_content(self, *args, **kwargs):
                raise AssertionError("network forbidden")
                yield b""

        hub = SimpleNamespace(resolve=AsyncMock(return_value=group()), refresh=AsyncMock())
        bot = Bot("123456789:TEST", session=Transport())
        dispatcher = Dispatcher(reader_hub=hub)
        dispatcher.include_router(create_router(self.db, self.settings))
        await Accounts(self.db, self.settings).register(9, None)
        sequence = 0

        async def command(text, actor=9):
            nonlocal sequence
            sequence += 1
            message = Message(
                message_id=sequence,
                date=utc_now(),
                chat=Chat(id=actor, type="private"),
                from_user=TelegramUser(id=actor, is_bot=False, first_name="Тест"),
                text=text,
            )
            await dispatcher.feed_update(bot, Update(update_id=sequence, message=message))

        try:
            await command("/add_chat @business_test", actor=10)
            hub.resolve.assert_not_awaited()
            await command("/add_chat @business_test")
            self.assertIn("&amp;", calls[-1].text)
            await command("/chats")
            self.assertIn(str(self.source.chat_id), calls[-1].text)
            await command("/keywords seo add include <особое>")
            self.assertIn("&lt;особое&gt;", calls[-1].text)
            await command(f"/remove_chat {self.source.chat_id}")
            self.assertNotIn(self.source.chat_id, [row.chat_id for row in await self.storage.active_sources()])
            self.assertTrue(all(int(call.chat_id) in (9, 10) for call in calls))
        finally:
            await dispatcher.storage.close()
            await bot.session.close()

    async def test_admin_role_rechecked_after_network(self):
        await Accounts(self.db, self.settings).register(9, "admin_test")

        async def revoked(username):
            async with self.db.session() as session:
                (await session.scalar(select(User).where(User.tg_id == 9))).is_admin = False
            return group()

        hub = SimpleNamespace(resolve=revoked, refresh=AsyncMock())
        with self.assertRaises(AccountError):
            await AdminSources(self.db, self.settings, hub).add_chat(9, "@business_test")
        hub.refresh.assert_not_awaited()


class ReaderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_author_variants_and_topic(self):
        source = SimpleNamespace(chat_id=-1001234)
        for sender, peer, author in (
            (None, None, None),
            (group(), types.PeerChannel(1234), None),
            (types.User(id=5, bot=True, first_name="Бот"), types.PeerUser(5), 5),
        ):
            result = snapshot(source, message(1, sender=sender, from_id=peer))
            self.assertEqual(result.author_tg_id, author)
            if author is None:
                self.assertIsNone(result.author_username)
        result = snapshot(source, message(2, reply_to=SimpleNamespace(forum_topic=True, reply_to_top_id=7)))
        self.assertEqual(result.metadata["thread_id"], 7)
        self.assertEqual(result.author_name, "Иван")
        with self.assertRaises(ValueError):
            snapshot(source, message(3, date=utc_now().replace(tzinfo=None)))

    async def test_userbot_send_methods_and_raw_rpc_are_blocked(self):
        client = object.__new__(ReadOnlyTelegramClient)
        for method in (client.send_message, client.send_file, client.forward_messages):
            with self.assertRaises(PermissionError):
                await method("chat", "text")
        request = type("SendMessageRequest", (), {})()
        with self.assertRaises(PermissionError):
            await client([request])

    async def test_existing_login_does_not_open_or_change_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "existing.session"
            path.write_bytes(b"existing private session")
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch("login.TelegramClient") as factory, patch("login.secure_session") as secure:
                self.assertFalse(await login(path, 0, ""))
                factory.assert_not_called()
                secure.assert_not_called()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    async def test_new_login_uses_separate_interactive_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            client = SimpleNamespace(start=AsyncMock(), disconnect=AsyncMock())
            with patch("login.TelegramClient", return_value=client):
                self.assertTrue(await login(Path(folder) / "new.session", 123, "a" * 32))
            self.assertEqual(client.start.call_args.kwargs["max_attempts"], 1)
            client.disconnect.assert_awaited_once()

    async def test_reader_connect_only_authorized_and_cleanup(self):
        settings = Settings(_env_file=None, telegram_api_id=123, telegram_api_hash=SecretStr("a" * 32))
        client = SimpleNamespace(
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            disconnect=AsyncMock(),
            add_event_handler=Mock(),
            remove_event_handler=Mock(),
        )
        storage = SimpleNamespace(seed_keywords=AsyncMock())
        reader = SimpleNamespace(
            refresh=AsyncMock(), on_message=AsyncMock(), history_loop=AsyncMock(side_effect=asyncio.CancelledError())
        )
        hub = ReaderHub()
        with (
            patch("app.userbot.client.load_session", return_value=object()),
            patch("app.userbot.client.ReadOnlyTelegramClient", return_value=client),
            patch("app.userbot.client.Ingestion", return_value=storage),
            patch("app.userbot.client.TelegramReader", return_value=reader),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await run_reader(Mock(), settings, hub=hub)
            self.assertIsNone(hub.reader)
            builder = client.add_event_handler.call_args.args[1]
            self.assertIsNone(builder.chats)
            client.remove_event_handler.assert_called_once_with(reader.on_message)
            client.disconnect.assert_awaited_once()
            client.is_user_authorized.return_value = False
            storage.seed_keywords.reset_mock()
            with self.assertRaises(ReaderAuthorizationError):
                await run_reader(Mock(), settings)
            storage.seed_keywords.assert_not_awaited()

    async def test_full_runtime_supervises_bot_reader_and_ingestion_together(self):
        settings = Settings(_env_file=None, bot_token="123456789:TEST")
        inspection = Mock(close=AsyncMock())
        database = Mock(close=AsyncMock())
        bot = Mock(session=Mock(close=AsyncMock()))
        dispatcher = Mock(start_polling=AsyncMock(), storage=Mock(close=AsyncMock()))
        reader = AsyncMock()
        ingestion = Mock(processing_loop=AsyncMock())

        async def run_once(services, cfg, stop):
            self.assertEqual(set(services), {"bot", "reader", "ingestion", "growth"})
            for name, factory in services.items():
                if name != "growth":
                    await factory()

        with (
            patch("app.bot.runtime.Database", side_effect=[inspection, database]),
            patch("app.bot.runtime.verify_schema", new=AsyncMock(return_value=True)),
            patch("app.bot.runtime.instance_lock", return_value=nullcontext()),
            patch("app.bot.runtime.Bot", return_value=bot),
            patch("app.bot.runtime.Dispatcher", return_value=dispatcher),
            patch("app.bot.runtime.run_services", new=run_once),
            patch("app.userbot.client.run_reader", new=reader),
            patch("app.pipeline.ingestion.Ingestion", return_value=ingestion),
        ):
            with self.assertRaises(RuntimeError):
                await run_application(settings)
        reader.assert_awaited_once()
        ingestion.processing_loop.assert_awaited_once()
        self.assertIsInstance(reader.call_args.kwargs["hub"], ReaderHub)
        database.close.assert_awaited_once()
        dispatcher.storage.close.assert_awaited_once()
        bot.session.close.assert_awaited_once()

    async def test_html_lists_split_without_breaking_entities(self):
        message = SimpleNamespace(answer=AsyncMock())
        lines = ["&amp;" * 255 for _ in range(20)]
        await answer_lines(message, "<b>Чаты</b>\n{lines}", lines, 4096)
        self.assertGreater(message.answer.await_count, 1)
        for call in message.answer.call_args_list:
            self.assertLessEqual(len(call.args[0]), 4096)
            self.assertNotIn("&amp\n", call.args[0])


if __name__ == "__main__":
    unittest.main()
