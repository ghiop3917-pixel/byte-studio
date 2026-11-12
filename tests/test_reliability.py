import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from bot import TelegramError, classify
from reliability import (
    acquire_lock, claim, finish_notification, ingest, migrate, open_database,
    process_one, recover, reprocess, retry, retry_notification,
)
from telegram_runtime import source_error
from telegram_runtime import source_loop
from telethon import errors


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.db = open_database(":memory:")
        migrate(self.db)
        self.message = {
            "chat": {"id": -100123, "type": "supergroup", "title": "Тест", "username": "testgroup"},
            "message_id": 1, "text": "Нужен сайт для кафе",
            "date": "2026-11-11T10:00:00+00:00",
            "from": {"id": 3, "name": "Тест", "username": "testauthor"},
        }

    def tearDown(self):
        self.db.close()

    def count(self, table):
        return self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def lead(self):
        raw_id = ingest(self.db, self.message, 5)
        self.assertTrue(process_one(self.db, lambda _: ("dev",)))
        return raw_id

    def test_ingest_retains_rejected_original_and_cursor(self):
        self.message["text"] = "Пока просто обсуждаем будущую идею"
        ingest(self.db, self.message, 5)
        ingest(self.db, self.message, 5)
        self.assertEqual(self.count("raw_messages"), 1)
        self.assertEqual(self.count("processing_queue"), 1)
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(self.count("leads"), 0)
        raw = self.db.execute("SELECT * FROM raw_messages").fetchone()
        self.assertEqual(raw["original_text"], self.message["text"])
        self.assertEqual(raw["author_id"], 3)
        self.assertEqual(raw["status"], "completed")
        self.assertEqual(self.db.execute("SELECT message_id FROM cursors").fetchone()[0], 1)

    def test_reprocessing_never_duplicates_lead_or_outbox(self):
        raw_id = self.lead()
        reprocess(self.db, raw_id)
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(self.count("leads"), 1)
        self.assertEqual(self.count("notification_queue"), 1)

    def test_processing_recovers_expired_lease(self):
        ingest(self.db, self.message, 5)
        job = claim(self.db, "processing_queue")
        self.assertIsNone(claim(self.db, "processing_queue"))
        self.db.execute("UPDATE processing_queue SET lease_until=0")
        recover(self.db)
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(self.count("leads"), 1)

    def test_exception_retries_are_bounded(self):
        ingest(self.db, self.message, 5)
        for _ in range(5):
            job = claim(self.db, "processing_queue")
            retry(self.db, "processing_queue", job, "test")
            self.db.execute("UPDATE processing_queue SET due_at=0")
        self.assertIsNone(claim(self.db, "processing_queue"))
        self.assertEqual(self.db.execute("SELECT status FROM processing_queue").fetchone()[0], "failed")

    def test_atomic_processing_rolls_back_on_classifier_error(self):
        ingest(self.db, self.message, 5)
        process_one(self.db, Mock(side_effect=ValueError("test")))
        self.assertEqual(self.count("leads"), 0)
        self.assertEqual(self.count("notification_queue"), 0)
        self.assertEqual(self.db.execute("SELECT status FROM processing_queue").fetchone()[0], "retry_wait")

    def test_successful_notification_has_message_id(self):
        self.lead()
        job = claim(self.db, "notification_queue")
        self.assertFalse(self.db.in_transaction)
        finish_notification(self.db, job, {"message_id": 123})
        row = self.db.execute("SELECT * FROM notification_queue").fetchone()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["telegram_message_id"], 123)
        self.assertIsNone(claim(self.db, "notification_queue"))

    def test_timeout_never_automatically_resends(self):
        self.lead()
        job = claim(self.db, "notification_queue")
        finish_notification(self.db, job, error=TelegramError(0))
        self.assertEqual(self.db.execute("SELECT status FROM notification_queue").fetchone()[0], "ambiguous")
        self.assertIsNone(claim(self.db, "notification_queue"))
        retry_notification(self.db, job["id"])
        self.assertIsNotNone(claim(self.db, "notification_queue"))

    def test_crash_during_send_becomes_ambiguous(self):
        self.lead()
        claim(self.db, "notification_queue")
        self.db.execute("UPDATE notification_queue SET lease_until=0")
        recover(self.db)
        self.assertEqual(self.db.execute("SELECT status FROM notification_queue").fetchone()[0], "ambiguous")

    def test_explicit_429_retries_later(self):
        self.lead()
        job = claim(self.db, "notification_queue")
        finish_notification(self.db, job, error=TelegramError(429, 60))
        row = self.db.execute("SELECT * FROM notification_queue").fetchone()
        self.assertEqual(row["status"], "retry_wait")
        self.assertGreaterEqual(row["due_at"], time.time() + 59)

    def test_failure_of_one_source_does_not_change_another(self):
        self.db.execute("INSERT INTO source_health(username) VALUES ('bad'),('good')")
        source_error(self.db, "bad", ValueError())
        self.assertEqual(self.db.execute("SELECT status FROM source_health WHERE username='good'").fetchone()[0], "pending")
        self.assertEqual(self.db.execute("SELECT status FROM source_health WHERE username='bad'").fetchone()[0], "retry_wait")

    def test_two_connections_claim_each_job_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db.sqlite3"
            first = open_database(path)
            migrate(first)
            ingest(first, self.message, 5)
            second = open_database(path)
            try:
                self.assertIsNotNone(claim(first, "processing_queue"))
                self.assertIsNone(claim(second, "processing_queue"))
                acquire_lock(first, "monitor")
                with self.assertRaises(RuntimeError):
                    acquire_lock(second, "monitor")
            finally:
                first.close()
                second.close()

    def test_queue_survives_closing_and_reopening_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "restart.sqlite3"
            db = open_database(path)
            migrate(db)
            ingest(db, self.message, 5)
            db.close()
            db = open_database(path)
            try:
                process_one(db, lambda _: ("dev",))
                self.assertEqual(db.execute("SELECT count(*) FROM notification_queue").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT original_text FROM raw_messages").fetchone()[0],
                                 self.message["text"])
            finally:
                db.close()

    def test_floodwait_sets_due_time_and_counts_attempts(self):
        self.db.execute("INSERT INTO source_health(username) VALUES ('busy')")
        source_error(self.db, "busy", errors.FloodWaitError(request=None, capture=75))
        row = self.db.execute("SELECT * FROM source_health").fetchone()
        self.assertEqual(row["attempts"], 1)
        self.assertGreater(row["due_at"], time.time() + 74)

    def test_source_loop_continues_after_unavailable_group(self):
        async def resolve(client, name):
            if name == "bad":
                raise ValueError("not available")
            return object()

        sleeps = 0
        async def stop_after_cycle(delay):
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 3:
                raise asyncio.CancelledError()

        async def scenario():
            client = Mock()
            client.is_connected.return_value = True
            with patch("telegram_runtime.collect_group", new=AsyncMock(return_value=12)) as collect:
                with patch("telegram_runtime.asyncio.sleep", new=stop_after_cycle):
                    with self.assertRaises(asyncio.CancelledError):
                        await source_loop(client, self.db, lambda: ("bad", "good"), resolve, lambda: 5, {})
                collect.assert_awaited_once()

        asyncio.run(scenario())
        self.assertEqual(self.db.execute("SELECT status FROM source_health WHERE username='good'").fetchone()[0],
                         "healthy")

    def test_migration_keeps_legacy_data_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.sqlite3"
            legacy = sqlite3.connect(path)
            legacy.execute("CREATE TABLE cursors(chat_id INTEGER PRIMARY KEY,message_id INTEGER NOT NULL)")
            legacy.execute("INSERT INTO cursors VALUES (10,42)")
            legacy.commit()
            legacy.close()
            db = open_database(path)
            try:
                migrate(db)
                migrate(db)
                self.assertEqual(db.execute("SELECT message_id FROM cursors").fetchone()[0], 42)
                self.assertEqual(len(list((path.parent / "backups").glob("*.sqlite3"))), 1)
            finally:
                db.close()

    def test_required_classifier_cases(self):
        accepted = [
            "Need a website for my bakery", "Looking for a developer to build a Telegram bot",
            "Нужно мобильное приложение", "Нужна десктопная программа",
            "Мы до сих пор обрабатываем заявки вручную",
            "Нужен сайт. Предлагаю обсудить стоимость", "Сайт перестал справляться с количеством заказов",
            "We still handle leads manually", "Посоветуйте, кто может сделать удобную систему записи",
        ]
        for text in accepted:
            with self.subTest(text=text):
                self.assertTrue(classify(text))
        for text in ("Нужен сайт. Уже нашли исполнителя", "Need a website. Already found a developer",
                     "Делаю сайты", "Нужен разработчик в штат"):
            self.assertFalse(classify(text))

    def test_existing_bot_commands_still_dispatch(self):
        from bot_menu import dispatch
        api = Mock()
        for command in ("/start", "/menu", "/status", "/directions"):
            dispatch(api, {"message": {"chat": {"id": 5, "type": "private"}, "text": command}}, 5, 27)
            self.assertEqual(api.call.call_args.args[0], "sendMessage")
        before = api.call.call_count
        dispatch(api, {"message": {"chat": {"id": 6, "type": "private"}, "text": "/menu"}}, 5, 27)
        self.assertEqual(api.call.call_count, before)


if __name__ == "__main__":
    unittest.main()
