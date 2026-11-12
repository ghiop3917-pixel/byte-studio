"""Этап 1: новые сущности только в памяти, диагностика с поддельной сетью."""

import asyncio
import hashlib
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.check import inspect_sqlite, main, run_checks
from app.config import INITIAL_CATEGORIES, Settings, get_settings
from app.db.models import Base, Category, Lead, LeadDelivery, Payment, User, UTCDateTime
from app.db.repo import LeadInput, Repository
from app.db.session import Database
from app.utils.runtime import run_services, supervise
from app.utils.session import load_session


def configuration(**changes):
    changes.setdefault("telegram_session_paths", (Path.cwd() / "test-only.session",))
    return Settings(_env_file=None, database_url="sqlite+aiosqlite:///:memory:", **changes)


class StageOneConfigTests(unittest.TestCase):
    def test_safe_defaults_and_legacy_secret_aliases(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "123:fake", "TELEGRAM_LEADS_API_ID": "123", "TELEGRAM_LEADS_API_HASH": "a" * 32},
            clear=True,
        ):
            cfg = configuration()
        self.assertEqual(cfg.bot_token.get_secret_value(), "123:fake")
        self.assertNotIn("123:fake", repr(cfg))
        self.assertEqual(cfg.telegram_api_id, 123)
        self.assertTrue(cfg.dry_run and cfg.admin_only_delivery)
        self.assertFalse(cfg.auto_join or cfg.llm_enabled or cfg.allow_schema_changes or cfg.auto_delete_leads)
        self.assertTrue(cfg.telegram_session_paths)

    def test_invalid_settings_and_environment_json(self):
        for change in (
            {"admin_ids": [-1]},
            {"telegram_session_paths": []},
            {"subscription_plans": {0: 10}},
            {"default_min_score": 101},
            {"join_pause_min_seconds": 121},
            {"retry_initial_seconds": 301},
            {"service_retry_initial_seconds": 301},
            {"display_tz": "Missing/Timezone"},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                configuration(**change)
        with patch.dict(os.environ, {"ADMIN_IDS": "[9,9,10]", "SUBSCRIPTION_PLANS": '{"7":100}'}, clear=True):
            cfg = configuration()
        self.assertEqual(cfg.admin_ids, (9, 10))
        self.assertEqual(cfg.subscription_plans, {7: 100})

    def test_settings_cache_and_env_file(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("TRIAL_DAYS=5\nADMIN_IDS=[9]\n", encoding="utf-8")
            get_settings.cache_clear()
            first = get_settings(env)
            self.assertIs(first, get_settings(env))
            self.assertEqual(first.trial_days, 5)
            get_settings.cache_clear()

    def test_timezone_adapter_rejects_naive_values(self):
        value = UTCDateTime()
        self.assertIsNone(value.process_bind_param(None, None))
        self.assertIsNone(value.process_result_value(None, None))
        with self.assertRaises(ValueError):
            value.process_bind_param(datetime(2026, 1, 1), None)
        with self.assertRaises(ValueError):
            value.process_result_value("2026-01-01T00:00:00", None)
        dt = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=3)))
        restored = value.process_result_value(value.process_bind_param(dt, None), None)
        self.assertEqual(restored.utcoffset(), timedelta(0))
        self.assertEqual(restored, dt)


class StageOneDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = configuration()
        self.db = Database(self.cfg, read_only=False, testing=True)
        await self.db.create_test_schema()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_users_trial_categories_are_idempotent(self):
        async with self.db.session() as session:
            repo = Repository(session, self.cfg)
            user = await repo.ensure_user(9, "owner", is_admin=True)
            original = user.subscription_until
            self.assertEqual(user.id, (await repo.ensure_user(9)).id)
            self.assertEqual(user.subscription_until, original)
            first = await repo.seed_categories()
            second = await repo.seed_categories()
            self.assertEqual(len(first), len(INITIAL_CATEGORIES))
            self.assertEqual([c.id for c in first], [c.id for c in second])
        async with self.db.session() as session:
            user = await Repository(session, self.cfg).get_user(9)
            self.assertEqual(user.created_at.utcoffset(), timedelta(0))
            self.assertGreater(user.subscription_until, user.created_at)
            self.assertTrue(user.is_admin)

    async def test_sources_leads_and_delivery_dedup(self):
        async with self.db.session() as session:
            repo = Repository(session, self.cfg)
            user = await repo.ensure_user(9)
            category = (await repo.seed_categories())[0]
            source = await repo.upsert_source(-100123, "Тест", username="test")
            self.assertEqual(source.id, (await repo.upsert_source(-100123, "Тест")).id)
            self.assertEqual(await repo.active_source_ids(), frozenset({-100123}))
            data = LeadInput(category.id, "Нужен сайт для магазина", "Сайт", 70, source.id, 1, "a" * 64)
            lead = await repo.save_lead(data)
            self.assertEqual(lead.id, (await repo.save_lead(data)).id)
            self.assertTrue(await repo.record_delivery(lead.id, user.id, 100))
            self.assertFalse(await repo.record_delivery(lead.id, user.id, 101))
            self.assertEqual(await session.scalar(select(func.count()).select_from(Lead)), 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(LeadDelivery)), 1)

    async def test_transaction_rolls_back_nested_repository_writes(self):
        with self.assertRaises(RuntimeError):
            async with self.db.session() as session:
                repo = Repository(session, self.cfg)
                await repo.ensure_user(9)
                await repo.seed_categories()
                raise RuntimeError("rollback")
        async with self.db.session() as session:
            self.assertEqual(await session.scalar(select(func.count()).select_from(User)), 0)
            self.assertEqual(await session.scalar(select(func.count()).select_from(Category)), 0)

    async def test_database_constraints_and_invalid_input(self):
        async with self.db.session() as session:
            repo = Repository(session, self.cfg)
            with self.assertRaises(ValueError):
                await repo.ensure_user(0)
            with self.assertRaises(ValueError):
                await repo.upsert_source(123, "Тест")
            category = (await repo.seed_categories())[0]
            source = await repo.upsert_source(-100123, "Тест")
            valid = LeadInput(category.id, "Нужен сайт", "Сайт", 70, source.id, 1, "a" * 64)
            for change in ({"score": 101}, {"text": ""}, {"message_id": 0}, {"summary": "x" * 121}):
                with self.assertRaises(ValueError):
                    await repo.save_lead(replace(valid, **change))
            with self.assertRaises(IntegrityError):
                async with session.begin_nested():
                    session.add(Payment(user_id=999, amount=1, currency="XTR", provider="stars", payload="test"))
                    await session.flush()

    async def test_postgresql_factory_and_ddl_without_network(self):
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable

        for table in Base.metadata.sorted_tables:
            self.assertIn("CREATE TABLE", str(CreateTable(table).compile(dialect=postgresql.dialect())))
        cfg = configuration()
        cfg.database_url = "postgresql+asyncpg://user@localhost/test"
        fake = Mock(dispose=AsyncMock())
        with (
            patch("app.db.session.create_async_engine", return_value=fake) as create,
            patch("app.db.session.async_sessionmaker"),
        ):
            database = Database(cfg)
            self.assertEqual(
                create.call_args.kwargs["connect_args"], {"server_settings": {"default_transaction_read_only": "on"}}
            )
            await database.close()
        with self.assertRaises(ValueError):
            Database(cfg, testing=True)
        cfg.database_url = "mysql+aiomysql://user@localhost/test"
        with self.assertRaises(ValueError):
            Database(cfg)

    async def test_bootstrap_is_not_allowed_for_working_database(self):
        with self.assertRaises(ValueError):
            Database(self.cfg)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "existing.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE preserved(value)")
            connection.execute("INSERT INTO preserved VALUES (1)")
            connection.commit()
            connection.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            cfg = configuration()
            cfg.database_url = "sqlite+aiosqlite:///" + path.as_posix()
            db = Database(cfg)
            try:
                with self.assertRaises(PermissionError):
                    await db.create_test_schema()
                async with db.engine.connect() as connection:
                    self.assertEqual((await connection.exec_driver_sql("SELECT value FROM preserved")).scalar(), 1)
                self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            finally:
                await db.close()
            cfg.database_url = "sqlite+aiosqlite:///" + str(Path(folder) / "missing.sqlite3")
            with self.assertRaises(FileNotFoundError):
                Database(cfg)
        self.assertTrue(all(name.startswith("lh_") for name in Base.metadata.tables))


class StageOneRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_recovers_without_stopping_other_service(self):
        cfg = configuration(service_retry_initial_seconds=0.001, service_retry_max_seconds=0.002)
        stop = asyncio.Event()
        calls = 0
        other_started = asyncio.Event()
        cancelled = asyncio.Event()

        async def unstable():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ValueError()
            await other_started.wait()
            stop.set()

        async def healthy():
            other_started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        await asyncio.wait_for(run_services({"bad": unstable, "good": healthy}, cfg, stop), 1)
        self.assertEqual(calls, 3)
        self.assertTrue(cancelled.is_set())

    async def test_exhausted_retries_are_bounded(self):
        cfg = configuration(service_retry_initial_seconds=0.001, service_max_restarts=2)
        worker = AsyncMock(side_effect=ValueError())
        await supervise("test", worker, cfg, asyncio.Event())
        self.assertEqual(worker.await_count, 2)
        with self.assertRaises(ValueError):
            await run_services({}, cfg, asyncio.Event())
        stop = asyncio.Event()
        stop.set()
        worker.reset_mock()
        await supervise("stopped", worker, cfg, stop)
        worker.assert_not_awaited()

    async def test_health_checks_default_offline_and_fake_online(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "monitor.sqlite3"
            sqlite3.connect(path).close()
            session_path = Path(folder) / "monitor.session"
            session_path.write_bytes(b"test")
            cfg = configuration(telegram_session_paths=[session_path])
            cfg.database_url = "sqlite+aiosqlite:///" + path.as_posix()
            with patch("httpx.AsyncClient", side_effect=AssertionError("network forbidden")):
                results = await run_checks(cfg)
            self.assertFalse(any(item.status == "FAIL" for item in results))
            self.assertEqual([item.status for item in results if item.name in {"bot", "userbot", "llm"}], ["SKIP"] * 3)
            cfg.check_network = True
            cfg.bot_token = SecretStr("123:fake")
            cfg.telegram_api_id = 1
            cfg.telegram_api_hash = SecretStr("a" * 32)
            cfg.llm_enabled = True
            cfg.llm_model = "test"
            bot = Mock(get_me=AsyncMock(), session=Mock(close=AsyncMock()))
            client = Mock(connect=AsyncMock(), disconnect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True))
            http = Mock(post=AsyncMock(return_value=Mock(raise_for_status=Mock())))
            context = Mock(__aenter__=AsyncMock(return_value=http), __aexit__=AsyncMock())
            with (
                patch("aiogram.Bot", return_value=bot),
                patch("telethon.TelegramClient", return_value=client),
                patch("app.utils.session.load_session"),
                patch("httpx.AsyncClient", return_value=context),
            ):
                results = await run_checks(cfg, online=True)
            self.assertFalse(any(item.status == "FAIL" for item in results), results)
            client.disconnect.assert_awaited_once()
            bot.session.close.assert_awaited_once()
            cfg.database_url = "sqlite+aiosqlite:///missing.sqlite3"
            cfg.bot_token = SecretStr("")
            cfg.telegram_api_id = 0
            cfg.llm_enabled = False
            results = await run_checks(cfg, online=True)
            self.assertEqual({x.name for x in results if x.status == "FAIL"}, {"database", "bot", "userbot"})


class StageOneCommandTests(unittest.TestCase):
    def test_missing_sqlite_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.sqlite3"
            with self.assertRaises(FileNotFoundError):
                inspect_sqlite(path)
            self.assertFalse(path.exists())

    def test_readonly_session_loader(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.session"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE sessions(dc_id,server_address,port,auth_key)")
            db.commit()
            db.close()
            with self.assertRaises(ValueError):
                load_session(path)
            db = sqlite3.connect(path)
            db.execute("INSERT INTO sessions VALUES (2,?,?,?)", ("127.0.0.1", 443, b"x" * 256))
            db.commit()
            db.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(load_session(path).dc_id, 2)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_check_json_and_carcase_main(self):
        from app.check import CheckResult
        from app.main import main as app_main

        with (
            patch("app.check.run_checks", new=AsyncMock(return_value=[CheckResult("test", "OK", "test")])),
            patch("sys.stdout", new_callable=StringIO) as stream,
        ):
            self.assertEqual(main(["--json"]), 0)
            self.assertIn('"status": "OK"', stream.getvalue())
            self.assertEqual(app_main([]), 0)
