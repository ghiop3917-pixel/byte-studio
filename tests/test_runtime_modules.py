"""Проверки Telegram-ядра без внешней сети и рабочей базы."""

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from telethon import errors, functions, types

import connect
import core_admin
import expand_sources as expand
import monitor
import pair_recipient
import source_growth as growth
import telegram_runtime as runtime
from bot import TelegramError
from reliability import acquire_lock, heartbeat, ingest, migrate, open_database, process_one


def group(name="testgroup", left=False):
    return types.Channel(
        id=1234,
        title="Тест",
        photo=types.ChatPhotoEmpty(),
        date=datetime.now(timezone.utc),
        megagroup=True,
        broadcast=False,
        username=name,
        left=left,
        access_hash=123,
    )


def message(number=1, text="Нужен сайт для магазина", author=10):
    return NS(
        id=number,
        raw_text=text,
        date=datetime.now(timezone.utc),
        media=None,
        from_id=types.PeerUser(author),
        sender_id=author,
        sender=NS(first_name="Иван", last_name="Тест", username="testuser", bot=False),
    )


class FakeClient:
    def __init__(self, messages=None):
        self.messages = messages or []
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.start = AsyncMock()
        self.get_me = AsyncMock(return_value=NS(id=9))
        self.is_user_authorized = AsyncMock(return_value=True)
        self.get_entity = AsyncMock(return_value=group())
        self.get_messages = AsyncMock(side_effect=lambda *a, **k: self.messages)
        self.get_input_entity = AsyncMock(return_value=types.InputPeerChannel(1234, 123))
        self.calls = []
        self.search_groups = []
        self.dialogs = []

    def is_connected(self):
        return True

    async def __call__(self, request):
        self.calls.append(request)
        if isinstance(request, functions.contacts.SearchRequest):
            return NS(chats=self.search_groups)
        if isinstance(request, functions.account.GetNotifySettingsRequest):
            return NS(mute_until=datetime.now(timezone.utc) + timedelta(days=1))
        if isinstance(request, functions.messages.GetPeerDialogsRequest):
            return NS(dialogs=[NS(folder_id=1)])
        return True

    async def iter_messages(self, *args, **kwargs):
        for item in self.messages:
            if item.id > kwargs.get("min_id", 0):
                yield item

    async def iter_dialogs(self, **kwargs):
        for item in self.dialogs:
            yield NS(entity=item)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = open_database(":memory:")
        migrate(self.db)

    def tearDown(self):
        self.db.close()

    async def test_collect_restart_dedup_and_distinct_chats(self):
        client = FakeClient([message(1), message(2)])
        await runtime.collect_group(client, self.db, group(), 9)
        client.messages.append(message(3))
        await runtime.collect_group(client, self.db, group(), 9)
        self.assertEqual(self.db.execute("SELECT count(*) FROM raw_messages").fetchone()[0], 3)
        other = group()
        other.id = 999
        await runtime.collect_group(client, self.db, other, 9)
        self.assertEqual(self.db.execute("SELECT count(*) FROM raw_messages").fetchone()[0], 6)

    async def test_source_backoff_and_method_cooldowns(self):
        self.db.execute("INSERT INTO source_health(username) VALUES ('test')")
        with patch("telegram_runtime.time.time", return_value=100):
            for delay in (30, 60, 120, 240, 480):
                runtime.source_error(self.db, "test", ValueError())
                self.assertEqual(self.db.execute("SELECT due_at FROM source_health").fetchone()[0], 100 + delay)
            self.assertEqual(self.db.execute("SELECT status FROM source_health").fetchone()[0], "failed")
            runtime.source_error(
                self.db, "test", errors.FloodWaitError(functions.contacts.ResolveUsernameRequest("x"), 30)
            )
            runtime.source_error(self.db, "test", errors.FloodWaitError(None, 50))
        self.assertEqual(runtime.cooldown(self.db, "resolve"), 131)
        self.assertEqual(runtime.cooldown(self.db, "history"), 151)

    async def test_source_loop_respects_history_and_resolve_pause(self):
        client = FakeClient()
        for method in ("history", "resolve"):
            heartbeat(self.db, "cooldown:" + method, str(time.time() + 100))
            with (
                patch("telegram_runtime.asyncio.sleep", side_effect=asyncio.CancelledError),
                self.assertRaises(asyncio.CancelledError),
            ):
                await runtime.source_loop(client, self.db, lambda: ("test",), client.get_entity, lambda: 9, {})
            client.get_entity.assert_not_awaited()
            self.db.execute("DELETE FROM core_heartbeat")

    async def test_processing_heartbeat(self):
        ingest(self.db, runtime.telegram_message(group(), message()), 9)
        with (
            patch("telegram_runtime.asyncio.sleep", side_effect=asyncio.CancelledError),
            self.assertRaises(asyncio.CancelledError),
        ):
            await runtime.processing_loop(self.db, lambda _: ("dev",))
        self.assertIsNotNone(self.db.execute("SELECT * FROM core_heartbeat WHERE component='processing'").fetchone())

    async def test_delivery_retry_timeout_success_and_filter(self):
        from crm import CRM

        lead = CRM(self.db, 9).create({"title": "Тест доставки"})["id"]
        for result, state in (
            (TelegramError(429, 60), "retry_wait"),
            (TimeoutError(), "ambiguous"),
            ({"message_id": 44}, "completed"),
        ):
            self.db.execute("DELETE FROM notification_queue")
            self.db.execute(
                "INSERT INTO notification_queue(lead_id,recipient,payload) VALUES (?,9,?)",
                (
                    lead,
                    json.dumps({"chat_id": 9, "text": "Тест"}),
                ),
            )
            sender = Mock()
            if isinstance(result, Exception):
                sender.send.side_effect = result
            else:
                sender.send.return_value = result
            with (
                patch("delivery_filter.delivery_decision", return_value=(True, "ok")),
                patch("telegram_runtime.asyncio.sleep", side_effect=asyncio.CancelledError),
                self.assertRaises(asyncio.CancelledError),
            ):
                await runtime.notification_loop(self.db, sender, lambda _: ("dev",))
            row = self.db.execute("SELECT * FROM notification_queue").fetchone()
            self.assertEqual(row["status"], state)
            if state == "ambiguous":
                from reliability import claim

                self.assertIsNone(claim(self.db, "notification_queue"))
        self.db.execute("DELETE FROM notification_queue")
        self.db.execute("INSERT INTO notification_queue(lead_id,recipient,payload) VALUES (?,9,'{}')", (lead,))
        with (
            patch("delivery_filter.delivery_decision", side_effect=ValueError),
            patch("telegram_runtime.asyncio.sleep", side_effect=asyncio.CancelledError),
            self.assertRaises(asyncio.CancelledError),
        ):
            await runtime.notification_loop(self.db, Mock(), lambda _: ("dev",))
        self.assertEqual(self.db.execute("SELECT status FROM notification_queue").fetchone()[0], "retry_wait")

    async def test_watch_stop_heartbeat_and_failed_worker(self):
        token = acquire_lock(self.db, "monitor")

        async def stop(_):
            heartbeat(self.db, "stop_request")

        with patch("telegram_runtime.asyncio.sleep", side_effect=stop):
            await runtime.watch(self.db, token, {}, [])
        self.assertIsNotNone(self.db.execute("SELECT * FROM core_heartbeat WHERE component='monitor'").fetchone())
        self.db.execute("DELETE FROM core_heartbeat WHERE component='stop_request'")
        task = Mock()
        task.done.return_value = True
        task.exception.return_value = ValueError()
        with self.assertRaises(RuntimeError):
            await runtime.watch(self.db, token, {}, [task])

    async def test_run_cancels_workers_and_releases_lock(self):
        async def worker(*args):
            await asyncio.Future()

        with ExitStack() as stack:
            for name in ("source_loop", "processing_loop", "notification_loop", "maintenance_loop"):
                stack.enter_context(patch("telegram_runtime." + name, side_effect=worker))
            stack.enter_context(patch("telegram_runtime.watch", new=AsyncMock()))
            await runtime.run(
                FakeClient(), self.db, Mock(), lambda: (), AsyncMock(), lambda: 9, lambda _: ("dev",), worker
            )
        self.assertEqual(self.db.execute("SELECT lease_until FROM core_locks WHERE name='monitor'").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT detail FROM core_heartbeat WHERE component='monitor'").fetchone()[0], "stopped"
        )

    async def test_maintenance_success_and_errors_are_isolated(self):
        with (
            patch("maintenance.run_tick", return_value=None),
            patch("telegram_runtime.asyncio.sleep", side_effect=asyncio.CancelledError),
            self.assertRaises(asyncio.CancelledError),
        ):
            await runtime.maintenance_loop(self.db, lambda: 9)
        with (
            patch("maintenance.run_tick", side_effect=OSError),
            patch("telegram_runtime.asyncio.sleep", side_effect=asyncio.CancelledError),
            self.assertRaises(asyncio.CancelledError),
        ):
            await runtime.maintenance_loop(self.db, lambda: 9)
        self.assertEqual(
            self.db.execute("SELECT detail FROM core_heartbeat WHERE component='maintenance'").fetchone()[0],
            "attention",
        )


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_read_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.session"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE sessions(dc_id,server_address,port,auth_key)")
            db.commit()
            db.close()
            with self.assertRaises(SystemExit):
                monitor.open_session(path)
            db = sqlite3.connect(path)
            db.execute("INSERT INTO sessions VALUES (2,?,?,?)", ("127.0.0.1", 443, b"x" * 256))
            db.commit()
            db.close()
            self.assertEqual(monitor.open_session(path).dc_id, 2)

    async def test_configuration_and_resolution(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(monitor, "RECIPIENT_FILE", Path(folder) / "recipient.json"),
            patch.object(monitor, "EXTRA_SOURCES", Path(folder) / "sources.json"),
        ):
            self.assertEqual(monitor.recipient_id(9), 9)
            monitor.RECIPIENT_FILE.write_text("12")
            self.assertEqual(monitor.recipient_id(9), 12)
            monitor.RECIPIENT_FILE.write_text("true")
            with self.assertRaises(ValueError):
                monitor.recipient_id(9)
            monitor.EXTRA_SOURCES.write_text(json.dumps(["extra", "extra", "tclubnetwork"]))
            self.assertEqual(monitor.source_names().count("extra"), 1)
            self.assertNotIn("tclubnetwork", monitor.source_names())
        client = FakeClient()
        self.assertEqual((await monitor.resolve_group(client, "test")).username, "testgroup")
        client.get_entity.return_value = group(left=True)
        with self.assertRaises(ValueError):
            await monitor.resolve_group(client, "test")

    async def test_monitor_checks_and_normal_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath = Path(folder) / "monitor.sqlite3"
            for check in (True, False):
                client = FakeClient()
                with (
                    patch.dict(
                        os.environ,
                        {
                            "TELEGRAM_LEADS_API_ID": "123",
                            "TELEGRAM_LEADS_API_HASH": "a" * 32,
                            "TELEGRAM_BOT_TOKEN": "123:fake",
                        },
                    ),
                    patch.object(monitor, "TelegramClient", return_value=client),
                    patch.object(monitor, "open_session"),
                    patch.object(monitor, "MONITOR_DB", dbpath),
                    patch.object(monitor, "source_names", return_value=("ok", "bad")),
                    patch.object(monitor, "resolve_group", side_effect=[group(), ValueError()]),
                    patch("telegram_runtime.run", new=AsyncMock()) as run,
                    patch.object(monitor, "Telegram"),
                ):
                    await monitor.monitor(NS(session=str(Path(folder) / "test.session"), check=check))
                    self.assertEqual(run.await_count, 0 if check else 1)
                client.disconnect.assert_awaited_once()

    async def test_invalid_credentials_and_unauthorized_session(self):
        with (
            patch.dict(os.environ, {"TELEGRAM_LEADS_API_ID": "bad", "TELEGRAM_LEADS_API_HASH": "x"}),
            self.assertRaises(SystemExit),
        ):
            await monitor.monitor(NS())
        client = FakeClient()
        client.is_user_authorized.return_value = False
        with (
            patch.dict(os.environ, {"TELEGRAM_LEADS_API_ID": "123", "TELEGRAM_LEADS_API_HASH": "a" * 32}),
            patch.object(monitor, "TelegramClient", return_value=client),
            patch.object(monitor, "open_session"),
            self.assertRaises(SystemExit),
        ):
            await monitor.monitor(NS(session="test.session", check=False))
        client.disconnect.assert_awaited_once()


class CommandTests(unittest.TestCase):
    def test_admin_stop_reprocess_retry_and_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "monitor.sqlite3"
            db = open_database(path)
            migrate(db)
            raw = ingest(db, runtime.telegram_message(group(), message()), 9)
            process_one(db, lambda _: ("dev",))
            db.execute("UPDATE notification_queue SET status='ambiguous'")
            notification = db.execute("SELECT id FROM notification_queue").fetchone()[0]
            db.execute("INSERT INTO source_health(username,status) VALUES ('test','failed')")
            db.close()
            with (
                patch.object(core_admin, "MONITOR_DB", path),
                patch(
                    "sys.argv",
                    [
                        "core_admin",
                        "--stop",
                        "--reprocess",
                        str(raw),
                        "--retry-notification",
                        str(notification),
                        "--retry-source",
                        "@test",
                    ],
                ),
                patch("builtins.print"),
            ):
                core_admin.main()
            db = open_database(path)
            self.assertEqual(db.execute("SELECT status FROM processing_queue").fetchone()[0], "pending")
            self.assertEqual(db.execute("SELECT status FROM source_health").fetchone()[0], "pending")
            self.assertIsNotNone(db.execute("SELECT * FROM core_heartbeat WHERE component='stop_request'").fetchone())
            db.close()

    def test_pair_filters_updates_and_saves_only_verified_private_chat(self):
        telegram = Mock()
        telegram.call.side_effect = [
            {"username": "fake"},
            TelegramError(429, 1),
            [
                {"update_id": 1, "message": {"chat": {"id": 8, "type": "group"}, "text": "/start code"}},
                {"update_id": 2, "message": {"chat": {"id": 8, "type": "private"}, "text": "bad"}},
                {"update_id": 3, "message": {"chat": {"id": 9, "type": "private"}, "text": "/start code"}},
            ],
        ]
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake"}),
            patch.object(pair_recipient, "Telegram", return_value=telegram),
            patch.object(pair_recipient.secrets, "token_urlsafe", return_value="code"),
            patch.object(pair_recipient, "RECIPIENT_FILE", Path(folder) / "recipient.json"),
            patch.object(pair_recipient.time, "sleep"),
            patch("builtins.print"),
        ):
            pair_recipient.pair()
            self.assertEqual(json.loads(pair_recipient.RECIPIENT_FILE.read_text()), 9)
        telegram.send.assert_called_once()

    def test_pair_expired_and_fatal_error(self):
        for fatal in (False, True):
            telegram = Mock()
            telegram.call.side_effect = [{"username": "fake"}, TelegramError(401)] if fatal else [{"username": "fake"}]
            ticks = [0, 0] if fatal else [0, 901]
            with (
                patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fake"}),
                patch.object(pair_recipient, "Telegram", return_value=telegram),
                patch.object(pair_recipient.time, "monotonic", side_effect=ticks),
                patch("builtins.print"),
            ):
                if fatal:
                    with self.assertRaises(TelegramError):
                        pair_recipient.pair()
                else:
                    pair_recipient.pair()
            telegram.send.assert_not_called()


class ConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_success_error_and_permissions(self):
        for fail in (False, True):
            client = FakeClient()
            if fail:
                client.start.side_effect = ValueError()
            with (
                tempfile.TemporaryDirectory() as folder,
                patch.dict(os.environ, {"TELEGRAM_LEADS_API_ID": "123", "TELEGRAM_LEADS_API_HASH": "a" * 32}),
                patch.object(connect, "session_path", return_value=Path(folder) / "private/test.session"),
                patch.object(connect, "TelegramClient", return_value=client),
                patch("builtins.print"),
            ):
                if fail:
                    with self.assertRaises(SystemExit):
                        await connect.connect()
                else:
                    await connect.connect()
                    self.assertEqual(client.start.call_args.kwargs["max_attempts"], 1)
                client.disconnect.assert_awaited_once()

    async def test_invalid_credentials_do_not_create_client(self):
        with (
            patch.dict(os.environ, {"TELEGRAM_LEADS_API_ID": "bad", "TELEGRAM_LEADS_API_HASH": "bad"}),
            patch.object(connect, "TelegramClient") as factory,
            self.assertRaises(SystemExit),
        ):
            await connect.connect()
        factory.assert_not_called()


class ExpansionTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidates_cached_and_search_unique_public_groups(self):
        client = FakeClient()
        client.search_groups = [group("cached"), group("new"), NS()]
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(expand, "EXTRA_SOURCES", Path(folder) / "sources.json"),
            patch.object(expand, "CITIES", ()),
            patch("builtins.print"),
        ):
            (Path(folder) / "business-candidates.json").write_text(json.dumps({"cached": "Тест"}))
            result = [name async for name in expand.candidates(client)]
        self.assertEqual(result, ["cached", "new"])

    async def test_active_selection_and_archive(self):
        client = FakeClient([message(n, "Нужен сайт для магазина " + str(n), n % 5 + 1) for n in range(12)])
        self.assertIsNotNone(await expand.active_group(client, "test"))
        client.messages = []
        self.assertIsNone(await expand.active_group(client, "test"))
        client.get_entity.return_value = NS()
        self.assertFalse(await expand.active_group(client, "test"))
        self.assertTrue(await expand.add_group(client, group(left=True)))
        self.assertTrue(await growth.mute_archive_join(client, group(left=True)))
        kinds = [type(request).__name__ for request in client.calls]
        self.assertLess(kinds.index("UpdateNotifySettingsRequest", 5), kinds.index("JoinChannelRequest", 5))

    async def test_legacy_expansion_main_adds_and_stops(self):
        client = FakeClient()

        async def names(_):
            yield "existing"
            yield "new"

        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            path = Path(folder) / "sources.json"
            path.write_text("[]")
            stack.enter_context(
                patch.dict(os.environ, {"TELEGRAM_LEADS_API_ID": "123", "TELEGRAM_LEADS_API_HASH": "a" * 32})
            )
            stack.enter_context(patch.object(expand, "TelegramClient", return_value=client))
            stack.enter_context(patch.object(expand, "open_session"))
            stack.enter_context(patch.object(expand, "EXTRA_SOURCES", path))
            stack.enter_context(patch.object(expand, "candidates", names))
            stack.enter_context(
                patch.object(expand, "source_names", side_effect=lambda: ("existing", *json.loads(path.read_text())))
            )
            stack.enter_context(patch.object(expand, "active_group", new=AsyncMock(return_value=group("new"))))
            stack.enter_context(patch.object(expand, "add_group", new=AsyncMock(return_value=True)))
            stack.enter_context(patch("builtins.print"))
            await expand.main("test.session")
            self.assertEqual(json.loads(path.read_text()), ["new"])
        client.disconnect.assert_awaited_once()

    async def test_legacy_unauthorized_and_flood_failure(self):
        for unauthorized in (True, False):
            client = FakeClient()
            client.is_user_authorized.return_value = not unauthorized

            async def names(_):
                yield "new"

            with (
                patch.dict(os.environ, {"TELEGRAM_LEADS_API_ID": "123", "TELEGRAM_LEADS_API_HASH": "a" * 32}),
                patch.object(expand, "TelegramClient", return_value=client),
                patch.object(expand, "open_session"),
                patch.object(expand, "source_names", return_value=()),
                patch.object(expand, "candidates", names),
                patch.object(expand, "active_group", new=AsyncMock(side_effect=errors.FloodWaitError(None, 10))),
                patch("builtins.print"),
            ):
                with self.assertRaises(SystemExit if unauthorized else errors.FloodWaitError):
                    await expand.main()
            client.disconnect.assert_awaited_once()

    async def test_growth_durable_progress_selection_and_pause(self):
        for outcome in ("accepted", "rejected", "error", "cooldown", "limit", "unauthorized"):
            client = FakeClient([message(n, author=n % 5 + 1) for n in range(12)])
            client.is_user_authorized.return_value = outcome != "unauthorized"
            if outcome == "error":
                client.get_entity.side_effect = ValueError()
            if outcome == "cooldown":
                client.get_entity.side_effect = growth.SharedCooldown()
            if outcome == "limit":
                client.get_entity.side_effect = errors.ChannelsTooMuchError(None)
            with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                root = Path(folder)
                dbpath = root / "monitor.sqlite3"
                db = open_database(dbpath)
                migrate(db)
                db.close()
                state = root / "state.json"
                state.write_text(json.dumps({"checked": {}, "query_index": 999, "added": []}))
                candidates = root / "candidates.json"
                candidates.write_text(json.dumps({"new": "Тест"}))
                sources = root / "sources.json"
                sources.write_text("[]")
                for name, value in (
                    ("STATE", state),
                    ("CANDIDATES", candidates),
                    ("EXTRA_SOURCES", sources),
                    ("MONITOR_DB", dbpath),
                ):
                    stack.enter_context(patch.object(growth, name, value))
                stack.enter_context(
                    patch.dict(os.environ, {"TELEGRAM_LEADS_API_ID": "123", "TELEGRAM_LEADS_API_HASH": "a" * 32})
                )
                stack.enter_context(patch.object(growth, "GrowthClient", return_value=client))
                client.configure_growth = Mock()
                stack.enter_context(patch.object(growth, "open_session"))
                stack.enter_context(
                    patch.object(
                        growth,
                        "source_names",
                        side_effect=lambda sources=sources: tuple(json.loads(sources.read_text())),
                    )
                )
                stack.enter_context(patch.object(growth, "recipient_id", return_value=9))
                stack.enter_context(patch.object(growth, "saved_directions", return_value=("dev",)))
                stack.enter_context(patch.object(growth, "mute_archive_join", new=AsyncMock(return_value=True)))
                if outcome == "rejected":
                    stack.enter_context(
                        patch.object(
                            growth,
                            "assess_messages",
                            return_value={"accepted": False, "active": False, "authors": 0, "lead_examples": 0},
                        )
                    )
                stack.enter_context(patch("builtins.print"))
                await growth.main("test.session")
                saved = json.loads(state.read_text())
                if outcome == "accepted":
                    self.assertEqual(saved["checked"]["new"]["status"], "added")
                    self.assertEqual(json.loads(sources.read_text()), ["testgroup"])
                elif outcome == "error":
                    self.assertEqual(saved["checked"]["new"]["status"], "unavailable")
                elif outcome == "limit":
                    self.assertEqual(saved["status"], "telegram_limit")
                elif outcome == "unauthorized":
                    self.assertEqual(saved["status"], "failed")
                client.disconnect.assert_awaited_once()

    async def test_growth_rpc_cooldown_and_flood_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            db = open_database(":memory:")
            migrate(db)
            self.addCleanup(db.close)
            token = acquire_lock(db, "source_growth")
            client = growth.GrowthClient.__new__(growth.GrowthClient)
            state = {}
            client.configure_growth(db, token, state)
            with (
                patch.object(growth, "STATE", Path(folder) / "state.json"),
                patch.object(growth.TelegramClient, "__call__", new=AsyncMock(return_value="ok")) as rpc,
                patch("source_growth.asyncio.sleep", new=AsyncMock()),
            ):
                self.assertEqual(await client(functions.contacts.SearchRequest("test", 1)), "ok")
                rpc.side_effect = errors.FloodWaitError(None, 20)
                with self.assertRaises(errors.FloodWaitError):
                    await client(functions.contacts.ResolveUsernameRequest("test"))
                self.assertGreater(runtime.cooldown(db, "resolve"), time.time() + 19)
                with self.assertRaises(growth.SharedCooldown):
                    await client(functions.contacts.SearchRequest("test", 1))
                self.assertEqual(rpc.await_count, 2)
