import csv
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from openpyxl import load_workbook

from crm import CRM, Conflict, NotFound, STATUSES
from crm_api import CRMApp, Handler, Unauthorized
from crm_export import export
from reliability import ingest, migrate, open_database, process_one


class CRMTests(unittest.TestCase):
    def setUp(self):
        self.db = open_database(":memory:")
        migrate(self.db)
        self.crm = CRM(self.db, 9)

    def tearDown(self):
        self.db.close()

    def lead(self, title="Нужен сайт для кафе", **extra):
        return self.crm.create({"title": title, **extra})

    def test_card_contains_nullable_analysis_fields(self):
        lead = self.lead(original_text="Полный текст")
        for key in ("score", "confirmed_budget", "estimated_budget_min", "recommended_price", "minimum_price", "desired_price"):
            self.assertIsNone(lead[key])
        self.assertEqual(lead["original_text"], "Полный текст")
        self.assertEqual(lead["status"], "new")
        self.assertEqual(lead["drafts"], [])

    def test_telegram_pipeline_fills_crm_and_keeps_outbox(self):
        message = {
            "chat": {"id": -10022, "type": "supergroup", "title": "Чат", "username": "testgroup"},
            "message_id": 11, "text": "Нужен сайт для кафе", "date": "2026-11-11T10:00:00+00:00",
            "from": {"id": 45, "name": "Заказчик", "username": "testclient"},
        }
        ingest(self.db, message, 9)
        process_one(self.db, lambda _: ("dev",))
        lead = self.crm.card(1)
        self.assertEqual(lead["client"]["name"], "Заказчик")
        self.assertEqual(lead["client"]["contact"], "https://t.me/testclient")
        self.assertEqual(lead["original_text"], message["text"])
        self.assertEqual(lead["sources"][0]["published_at"], message["date"])
        self.assertIn("Сайты и магазины", lead["categories"])
        self.assertEqual(lead["conversations"][0]["direction"], "incoming")
        self.assertEqual(lead["drafts"][0]["status"], "draft")
        self.assertEqual(self.db.execute("SELECT count(*) FROM notification_queue").fetchone()[0], 1)
        self.assertEqual(self.crm.statistics()["replies"], 0)

    def test_status_changes_have_history_and_are_idempotent(self):
        lead = self.lead()
        for status in STATUSES:
            self.crm.change_status(lead["id"], status)
        before = len(self.crm.related("activities", lead["id"]))
        self.crm.change_status(lead["id"], "archived")
        self.assertEqual(len(self.crm.related("activities", lead["id"])), before)
        self.assertEqual(self.crm.card(lead["id"])["status"], "archived")
        with self.assertRaises(ValueError):
            self.crm.change_status(lead["id"], "unknown")

    def test_manual_import_and_telegram_share_exact_message_identity(self):
        lead = self.lead(platform="telegram", source_key="-10022", source_name="Чат", external_message_id="11")
        message = {
            "chat": {"id": -10022, "type": "supergroup", "title": "Чат", "username": "testgroup"},
            "message_id": 11, "text": "Нужен сайт для кафе",
            "from": {"id": 45, "name": "Заказчик", "username": "testclient"},
        }
        ingest(self.db, message, 9)
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(self.crm.list_leads()["total"], 1)
        self.assertEqual(self.crm.card(lead["id"])["original_text"], message["text"])
        self.assertEqual(len(self.crm.card(lead["id"])["sources"]), 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notification_queue").fetchone()[0], 0)

    def test_client_identity_is_scoped_to_platform_and_owner(self):
        first = self.crm.create_client({"name": "Иван", "platform": "telegram", "public_id": "123"})
        same = self.crm.create_client({"name": "Другое имя", "platform": "telegram", "public_id": "123"})
        other = self.crm.create_client({"name": "Иван", "platform": "manual", "public_id": "123"})
        self.assertEqual(first["id"], same["id"])
        self.assertEqual(same["name"], "Иван")
        self.assertNotEqual(first["id"], other["id"])
        one = self.lead(client_id=first["id"])
        two = self.lead(client_id=first["id"])
        self.assertEqual(len(self.crm.client(first["id"])["projects"]), 2)
        with self.assertRaises(NotFound):
            CRM(self.db, 10).card(one["id"])
        with self.assertRaises(NotFound):
            CRM(self.db, 10).client(first["id"])

    def test_possible_duplicates_do_not_merge(self):
        a = self.lead(original_text="Нужен сайт для интернет-магазина с каталогом товаров")
        b = self.lead(original_text="Нужен сайт для интернет-магазина с каталогом товаров")
        matches = self.crm.possible_duplicates(a["id"])
        self.assertTrue(any(row["lead_id"] == b["id"] for row in matches))
        self.assertIsNone(self.crm.lead(b["id"])["merged_into"])

    def test_exact_duplicates_respect_source_and_platform(self):
        data = {"title": "Задача", "platform": "telegram", "source_key": "group1", "external_message_id": "42"}
        first = self.crm.create(data)
        again = self.crm.create(data)
        other_source = self.crm.create({**data, "source_key": "group2"})
        other_platform = self.crm.create({**data, "platform": "manual"})
        self.assertEqual(first["id"], again["id"])
        self.assertNotEqual(first["id"], other_source["id"])
        self.assertNotEqual(first["id"], other_platform["id"])

    def test_manual_sources_share_one_project(self):
        lead = self.lead()
        source = {"platform": "telegram", "source_key": "group1", "source_name": "Чат", "external_message_id": "42"}
        one = self.crm.add_source(lead["id"], source)
        self.assertEqual(self.crm.add_source(lead["id"], source), one)
        self.assertEqual(len(self.crm.card(lead["id"])["sources"]), 2)
        another = self.lead()
        with self.assertRaises(Conflict):
            self.crm.add_source(another["id"], source)

    def test_merge_and_split_keep_all_sources_notes_and_history(self):
        first = self.lead(title="Первый")
        second = self.lead(title="Второй")
        self.crm.add_note(second["id"], "Важная заметка")
        self.crm.add_conversation(second["id"], "Ответ клиента", "incoming")
        merge = self.crm.merge(first["id"], second["id"])
        merged = self.crm.card(first["id"])
        self.assertEqual(len(merged["sources"]), 2)
        self.assertEqual(merged["notes"][0]["text"], "Важная заметка")
        self.assertEqual(self.crm.list_leads()["total"], 1)
        self.crm.split(merge["merge_id"])
        self.assertEqual(self.crm.list_leads()["total"], 2)
        self.assertEqual(self.crm.card(second["id"])["notes"][0]["text"], "Важная заметка")
        self.assertTrue(any(row["kind"] == "restored_from_merge" for row in self.crm.card(second["id"])["activities"]))

    def test_nested_merges_restore_original_groups(self):
        a, b, c = self.lead(), self.lead(), self.lead()
        first = self.crm.merge(b["id"], c["id"])
        second = self.crm.merge(a["id"], b["id"])
        with self.assertRaises(Conflict):
            self.crm.split(first["merge_id"])
        self.crm.split(second["merge_id"])
        self.assertEqual(self.crm.lead(c["id"])["merged_into"], b["id"])
        self.crm.split(first["merge_id"])
        self.assertEqual(self.crm.list_leads()["total"], 3)

    def test_foreign_owner_cannot_merge_or_modify_records(self):
        own = self.lead()
        other = CRM(self.db, 10).create({"title": "Чужой"})
        with self.assertRaises(NotFound):
            self.crm.merge(own["id"], other["id"])
        self.assertEqual(self.crm.list_leads()["total"], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM lead_merges").fetchone()[0], 0)

    def test_draft_is_not_outgoing_until_manual_confirmation(self):
        lead = self.lead()
        draft = self.crm.add_draft(lead["id"], "Предложение")
        self.assertEqual(self.crm.statistics()["outgoing_messages"], 0)
        with self.assertRaises(Conflict):
            self.crm.mark_draft_sent(draft)
        self.crm.update_draft(draft, {"status": "approved"})
        message_id = self.crm.mark_draft_sent(draft)
        self.assertEqual(self.crm.mark_draft_sent(draft), message_id)
        self.assertEqual(self.crm.statistics()["outgoing_messages"], 1)
        with self.assertRaises(Conflict):
            self.crm.update_draft(draft, {"text": "Перезапись"})

    def test_reminders_notes_and_feedback_persist(self):
        lead = self.lead()
        reminder = self.crm.add_reminder(lead["id"], "Уточнить ТЗ", "2026-12-01T12:00:00+03:00")
        self.crm.add_note(lead["id"], "Заметка")
        self.crm.add_feedback(lead["id"], "approved", "Подходит")
        self.assertEqual(len(self.crm.reminders("2026-12-02T00:00:00Z")), 1)
        self.crm.update_reminder(reminder, {"status": "completed"})
        self.assertEqual(self.crm.reminders(), [])
        self.assertEqual(self.crm.card(lead["id"])["lead_feedback"][0]["reason"], "Подходит")

    def test_financial_values_are_real_and_currencies_are_separate(self):
        lead = self.lead()
        self.crm.update(lead["id"], {"confirmed_budget": "100000.10", "currency": "rub"})
        deal = self.crm.add_deal(lead["id"], {"title": "Сайт"})
        self.assertEqual(self.crm.statistics()["actual_revenue"], {})
        self.crm.update_deal(deal, {"status": "won", "actual_revenue": "30000.10", "currency": "RUB"})
        self.crm.add_deal(lead["id"], {"title": "Поддержка", "status": "won", "actual_revenue": "10.20", "currency": "USD"})
        self.assertEqual(self.crm.statistics()["actual_revenue"], {"RUB": "30000.10", "USD": "10.20"})
        self.assertEqual(self.crm.statistics()["won_deals"], 2)
        with self.assertRaises(ValueError):
            self.crm.update(lead["id"], {"estimated_budget_min": "100", "estimated_budget_max": "50"})
        with self.assertRaises(ValueError):
            self.crm.update(lead["id"], {"recommended_price": -1})
        with self.assertRaises(ValueError):
            self.crm.update(lead["id"], {"score": 101})

    def test_search_filters_and_stage_conversion(self):
        lead = self.lead(title="Магазин", platform="manual", categories=["Сайты"])
        self.crm.change_status(lead["id"], "negotiation")
        self.assertEqual(self.crm.list_leads(search="МАГАЗИН", category="Сайты", status="negotiation")["total"], 1)
        stats = self.crm.statistics()
        self.assertEqual(stats["stage_conversion"]["negotiation"], 1)
        self.assertEqual(stats["stage_reached"]["new"], 1)

    def test_retention_is_disabled_and_active_deals_are_protected(self):
        lead = self.lead()
        self.db.execute("UPDATE leads SET discovered_at=?", (time.time() - 40 * 86400,))
        report = self.crm.retention_candidates()
        self.assertFalse(report["deletion_enabled"])
        self.assertFalse(report["items"][0]["protected"])
        self.crm.add_deal(lead["id"], {"title": "Открытая сделка"})
        self.assertTrue(self.crm.retention_candidates()["items"][0]["protected"])
        self.assertEqual(self.db.execute("SELECT count(*) FROM leads").fetchone()[0], 1)

    def test_merged_categories_and_active_stages_remain_visible(self):
        first = self.lead()
        second = self.lead(platform="manual", categories=["Дизайн"])
        self.crm.change_status(second["id"], "negotiation")
        self.crm.merge(first["id"], second["id"])
        self.assertEqual(self.crm.list_leads(category="Дизайн")["total"], 1)
        self.db.execute("UPDATE leads SET discovered_at=?", (time.time() - 40 * 86400,))
        self.assertTrue(self.crm.retention_candidates()["items"][0]["protected"])

    def test_csv_and_xlsx_are_readable_and_formula_safe(self):
        self.lead(title="=HYPERLINK(test)", original_text="Сайт для кафе")
        csv_rows = list(csv.reader(StringIO(export(self.crm, "csv").decode("utf-8-sig"))))
        self.assertEqual(csv_rows[1][1], "'=HYPERLINK(test)")
        workbook = load_workbook(BytesIO(export(self.crm, "xlsx")), data_only=False)
        try:
            cell = workbook.active["B2"]
            self.assertEqual(cell.value, "=HYPERLINK(test)")
            self.assertEqual(cell.data_type, "s")
            self.assertEqual(workbook.active["F2"].value, "Сайт для кафе")
        finally:
            workbook.close()

    def test_legacy_migration_keeps_ids_fields_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            db = open_database(path)
            with patch("crm_migrations.migrate_crm"):
                migrate(db)
            db.execute("INSERT INTO leads VALUES (7,9,'Старый лид','Чат','https://t.me/test/1',1)")
            db.execute("INSERT INTO cursors VALUES (-10012,99)")
            migrate(db)
            before = dict(db.execute("SELECT * FROM leads WHERE id=7").fetchone())
            migrate(db)
            after = dict(db.execute("SELECT * FROM leads WHERE id=7").fetchone())
            self.assertEqual(before, after)
            self.assertEqual(after["title"], "Старый лид")
            self.assertEqual(after["saved"], 1)
            self.assertIsNone(after["original_text"])
            self.assertIsNone(after["discovered_at"])
            self.assertEqual(db.execute("SELECT message_id FROM cursors").fetchone()[0], 99)
            self.assertTrue(list((path.parent / "backups").glob("before-crm-*.sqlite3")))
            db.close()

    def test_restart_keeps_crm_and_queue_relations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "restart.sqlite3"
            db = open_database(path)
            migrate(db)
            service = CRM(db, 9)
            lead = service.create({"title": "Проект"})
            service.add_note(lead["id"], "Сохранено")
            db.close()
            db = open_database(path)
            migrate(db)
            self.assertEqual(CRM(db, 9).card(lead["id"])["notes"][0]["text"], "Сохранено")
            db.close()

    def test_company_links_are_validated(self):
        company = self.crm.create_client({"name": "Компания", "kind": "company"})
        person = self.crm.create_client({"name": "Контакт", "company_id": company["id"]})
        self.assertEqual(person["company_id"], company["id"])
        with self.assertRaises(Conflict):
            self.crm.update_client(company["id"], {"company_id": company["id"]})

    def test_failed_migration_rolls_back_schema_and_preserves_rows(self):
        db = open_database(":memory:")
        try:
            with patch("crm_migrations.migrate_crm"):
                migrate(db)
            db.execute("INSERT INTO leads VALUES (7,9,'Прежний','Источник',NULL,1)")
            with patch("crm.hydrate_legacy", side_effect=ValueError("test")):
                with self.assertRaises(ValueError):
                    migrate(db)
            self.assertEqual(db.execute("SELECT title FROM leads").fetchone()[0], "Прежний")
            self.assertNotIn("status", [row["name"] for row in db.execute("PRAGMA table_info(leads)")])
            self.assertIsNone(db.execute("SELECT 1 FROM core_migrations WHERE version=2").fetchone())
            migrate(db)
            self.assertEqual(db.execute("SELECT saved FROM leads").fetchone()[0], 1)
        finally:
            db.close()


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "api.sqlite3"
        db = open_database(self.path)
        migrate(db)
        db.close()
        self.token = "a" * 40
        self.app = CRMApp(self.path, 9, self.token)
        self.auth = "Bearer " + self.token

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, data=None, key="request_key_00000001"):
        content_type, body = self.app.request(method, path, self.auth, data, key)
        return json.loads(body) if "application/json" in content_type else body

    def test_auth_and_owner_are_enforced(self):
        with self.assertRaises(Unauthorized):
            self.app.request("GET", "/api/leads", "Bearer wrong")
        lead = self.request("POST", "/api/leads", {"title": "Проект"})
        foreign = CRMApp(self.path, 10, self.token)
        with self.assertRaises(NotFound):
            foreign.request("GET", f"/api/leads/{lead['id']}", self.auth)

    def test_idempotency_and_status_history(self):
        lead = self.request("POST", "/api/leads", {"title": "Проект"})
        again = self.request("POST", "/api/leads", {"title": "Проект"})
        self.assertEqual(lead, again)
        with self.assertRaises(Conflict):
            self.request("POST", "/api/leads", {"title": "Другой"})
        self.request("POST", f"/api/leads/{lead['id']}/status", {"status": "verified"}, "request_key_00000002")
        history = self.request("GET", f"/api/leads/{lead['id']}/history")
        self.assertEqual(history[-1]["kind"], "status_changed")
        self.assertEqual(self.request("GET", "/api/leads")["total"], 1)

    def test_concurrent_repeated_requests_create_one_lead(self):
        def create():
            return self.app.request("POST", "/api/leads", self.auth,
                                    {"title": "Один проект"}, "concurrent_key_001")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.request("GET", "/api/leads")["total"], 1)

    def test_api_drafts_reminders_exports_statistics(self):
        lead = self.request("POST", "/api/leads", {"title": "Проект"})
        draft = self.request("POST", f"/api/leads/{lead['id']}/drafts", {"text": "Добрый день"}, "request_key_00000002")
        self.request("PATCH", f"/api/drafts/{draft['draft_id']}", {"status": "approved"}, "request_key_00000003")
        self.request("POST", f"/api/drafts/{draft['draft_id']}/sent", {"confirmed": True}, "request_key_00000004")
        reminder = self.request("POST", f"/api/leads/{lead['id']}/reminders",
                                {"text": "Напомнить", "due_at": "2026-12-01T00:00:00Z"}, "request_key_00000005")
        self.request("PATCH", f"/api/reminders/{reminder['reminder_id']}",
                     {"status": "completed"}, "request_key_00000006")
        self.assertEqual(self.request("GET", "/api/stats")["outgoing_messages"], 1)
        self.assertTrue(self.request("GET", "/api/export.csv").startswith(b"\xef\xbb\xbf"))
        self.assertTrue(self.request("GET", "/api/export.xlsx").startswith(b"PK"))

    def test_failed_request_rolls_back_receipt_and_data(self):
        with self.assertRaises(ValueError):
            self.request("POST", "/api/leads", {"title": ""})
        lead = self.request("POST", "/api/leads", {"title": "Исправлено"})
        self.assertEqual(lead["title"], "Исправлено")

    def test_real_loopback_http_auth_and_json(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = self.app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", "/api/leads")
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            connection.request("POST", "/api/leads", body=json.dumps({"title": "HTTP-проект"}),
                               headers={"Authorization": self.auth, "Content-Type": "application/json",
                                        "Idempotency-Key": "http_request_00001"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["title"], "HTTP-проект")
            connection.request("GET", "/api/leads", headers={"Authorization": self.auth})
            response = connection.getresponse()
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertEqual(json.loads(response.read())["total"], 1)
            connection.request("GET", "/api/leads",
                               headers={"Authorization": self.auth, "Origin": "https://untrusted.example"})
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
