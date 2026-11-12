import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bot_menu import dispatch
from crm import CRM, NotFound
from crm_api import CRMApp
from lead_analysis import analyze
from quality import Quality
from reliability import ingest, migrate, open_database, process_one, reprocess
from sales_drafts import make_draft


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.db = open_database(":memory:")
        migrate(self.db)
        self.message = {
            "chat": {"id": -10022, "type": "supergroup", "title": "Тест", "username": "testgroup"},
            "message_id": 1,
            "text": "Нужен сайт для кафе",
            "from": {"id": 10, "username": "testclient"},
        }

    def tearDown(self):
        self.db.close()

    def collect(self, content):
        self.message["text"] = content
        raw_id = ingest(self.db, self.message, 9)
        process_one(self.db, lambda _: ("dev",))
        return raw_id

    def test_curated_examples_intents_and_delivery_decisions(self):
        dataset = json.loads((Path(__file__).parent / "fixtures/lead_examples.json").read_text(encoding="utf-8"))
        for case in dataset:
            with self.subTest(text=case["text"]):
                result = analyze(case["text"])
                self.assertEqual(result["intent"], case["intent"])
                self.assertEqual(result["bucket"], case["bucket"])

    def test_score_is_explained_and_budget_is_not_invented(self):
        result = analyze("Срочно нужен сайт с каталогом. Бюджет 50000 рублей.", author={"username": "testclient"})
        self.assertEqual(result["score"], sum(row["points"] for row in result["reasons"]))
        self.assertTrue(result["features"]["has_budget"])
        self.assertTrue(result["features"]["urgent"])
        self.assertLessEqual(result["score"], 100)
        self.collect("Нужен сайт для кафе")
        card = CRM(self.db, 9).card(1)
        self.assertIsNone(card["confirmed_budget"])
        self.assertIsNone(card["recommended_price"])
        self.assertEqual(card["score_origin"], "rules")
        self.assertEqual(card["analyses"][0]["engine"], "rules")

    def test_uncertain_messages_are_saved_without_notifications(self):
        raw_id = self.collect("Мы до сих пор обрабатываем заявки вручную")
        self.assertEqual(self.db.execute("SELECT count(*) FROM leads").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notification_queue").fetchone()[0], 0)
        item = Quality(self.db, 9).review(raw_id)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["analysis"]["intent"], "early_need")

    def test_review_approval_creates_one_lead_through_existing_queue(self):
        raw_id = self.collect("Мы до сих пор обрабатываем заявки вручную")
        quality = Quality(self.db, 9)
        quality.decide(raw_id, "approved")
        process_one(self.db, lambda _: ("dev",))
        quality.decide(raw_id, "approved")
        reprocess(self.db, raw_id)
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(self.db.execute("SELECT count(*) FROM leads").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notification_queue").fetchone()[0], 1)

    def test_review_rejection_retains_original_and_does_not_create_lead(self):
        raw_id = self.collect("Мы до сих пор обрабатываем заявки вручную")
        Quality(self.db, 9).decide(raw_id, "rejected", "Пока нет задачи")
        reprocess(self.db, raw_id)
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(self.db.execute("SELECT count(*) FROM leads").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM raw_messages").fetchone()[0], 1)
        with self.assertRaises(NotFound):
            Quality(self.db, 10).review(raw_id)

    def test_manual_score_is_not_overwritten_during_reanalysis(self):
        raw_id = self.collect("Нужен сайт для кафе")
        CRM(self.db, 9).update(1, {"score": 88})
        reprocess(self.db, raw_id)
        process_one(self.db, lambda _: ("dev",))
        card = CRM(self.db, 9).card(1)
        self.assertEqual(card["score"], 88)
        self.assertEqual(card["score_origin"], "manual")

    def test_cache_keeps_one_analysis_for_same_version_and_preferences(self):
        raw_id = self.collect("Нужен сайт для кафе")
        reprocess(self.db, raw_id)
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(self.db.execute("SELECT count(*) FROM message_analyses").fetchone()[0], 1)
        reprocess(self.db, raw_id)
        process_one(self.db, lambda _: ())
        self.assertEqual(self.db.execute("SELECT count(*) FROM message_analyses").fetchone()[0], 2)

    def test_directions_are_respected(self):
        result = analyze("Нужен дизайн логотипа", ("dev",))
        self.assertEqual(result["bucket"], "ignored")
        self.assertEqual(analyze("Нужен дизайн логотипа", ("design",))["bucket"], "lead")
        self.assertEqual(analyze("Нужен сайт", ())["bucket"], "ignored")

    def test_drafts_use_language_task_and_do_not_repeat_provided_information(self):
        draft = make_draft(
            "Нужен лендинг. Есть референс https://example.com. Срок до 1 декабря. Бюджет 50000 рублей.",
            ["Сайты и магазины"],
            "ru",
        )
        self.assertIn("Нужен лендинг", draft)
        self.assertNotIn("Какой бюджет", draft)
        self.assertNotIn("К какой дате", draft)
        self.assertNotIn("Есть пример", draft)
        english = make_draft("Need a Telegram bot for booking. Budget $500.", ["Боты"], "en")
        self.assertTrue(english.startswith("Hi!"))
        self.assertNotIn("Здравствуйте", english)

    def test_quality_report_does_not_claim_measured_accuracy_without_labels(self):
        report = Quality(self.db, 9).report()
        self.assertIsNone(report["observed_approval_rate"])
        self.assertIsNone(report["recall"])
        self.assertFalse(report["automatic_learning"])
        self.assertEqual(report["ai_requests"], 0)

    def test_uncertain_owner_decision_is_recorded_once(self):
        raw_id = self.collect("Мы до сих пор обрабатываем заявки вручную")
        service = Quality(self.db, 9)
        service.decide(raw_id, "needs_review")
        service.decide(raw_id, "needs_review")
        self.assertEqual(self.db.execute("SELECT count(*) FROM review_events").fetchone()[0], 1)

    def test_missing_analysis_batch_is_bounded_and_owner_scoped(self):
        ingest(self.db, self.message, 9)
        self.assertEqual(Quality(self.db, 10).reanalyze_missing()["queued"], 0)
        self.assertEqual(Quality(self.db, 9).reanalyze_missing(limit=1)["queued"], 1)
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(Quality(self.db, 9).reanalyze_missing()["queued"], 0)

    def test_migration_preserves_existing_manual_score_and_legacy_fields(self):
        db = open_database(":memory:")
        try:
            with patch("analysis_migrations.migrate_analysis"):
                migrate(db)
            card = CRM(db, 9).create({"title": "Прежняя карточка"})
            CRM(db, 9).update(card["id"], {"score": 78})
            migrate(db)
            row = db.execute("SELECT * FROM leads").fetchone()
            self.assertEqual(row["title"], "Прежняя карточка")
            self.assertEqual(row["score"], 78)
            self.assertEqual(row["score_origin"], "manual")
        finally:
            db.close()


class QualityAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "quality.sqlite3"
        self.db = open_database(self.path)
        migrate(self.db)
        self.message = {
            "chat": {"id": -10033, "type": "supergroup", "title": "Тест"},
            "message_id": 1,
            "text": "Нужен сайт для кафе",
            "from": {"username": "testclient"},
        }
        self.auth = "Bearer " + "x" * 40
        self.api = CRMApp(self.path, 9, "x" * 40)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_feedback_buttons_are_owner_scoped_and_idempotent(self):
        ingest(self.db, self.message, 9)
        process_one(self.db, lambda _: ("dev",))
        telegram = Mock()
        callback = {
            "id": "callback_one",
            "from": {"id": 9},
            "data": "fb:approved:1",
            "message": {"chat": {"id": 9}, "message_id": 10},
        }
        with patch("bot_menu.MONITOR_DB", self.path):
            dispatch(telegram, {"callback_query": callback}, 9, 27)
            dispatch(telegram, {"callback_query": callback}, 9, 27)
            callback["from"]["id"] = 10
            dispatch(telegram, {"callback_query": callback}, 9, 27)
        self.assertEqual(self.db.execute("SELECT count(*) FROM lead_feedback").fetchone()[0], 1)
        self.assertEqual(Quality(self.db, 9).report()["observed_approval_rate"], 1)

    def test_review_routes_approval_and_quality(self):
        self.message["text"] = "Мы до сих пор обрабатываем заявки вручную"
        raw_id = ingest(self.db, self.message, 9)
        process_one(self.db, lambda _: ("dev",))
        _, data = self.api.request("GET", "/api/reviews", self.auth)
        self.assertEqual(json.loads(data)["total"], 1)
        self.api.request(
            "POST", f"/api/reviews/{raw_id}/decision", self.auth, {"decision": "approved"}, "review_request_00001"
        )
        process_one(self.db, lambda _: ("dev",))
        self.assertEqual(self.db.execute("SELECT count(*) FROM leads").fetchone()[0], 1)
        _, report = self.api.request("GET", "/api/quality", self.auth)
        self.assertEqual(json.loads(report)["reviews"]["approved"], 1)

    def test_feedback_uncertain_opens_review_without_deleting_lead(self):
        ingest(self.db, self.message, 9)
        process_one(self.db, lambda _: ("dev",))
        with patch("bot_menu.MONITOR_DB", self.path):
            callback = {
                "id": "callback_two",
                "from": {"id": 9},
                "data": "fb:needs_review:1",
                "message": {"chat": {"id": 9}, "message_id": 10},
            }
            dispatch(Mock(), {"callback_query": callback}, 9, 27)
        self.assertEqual(Quality(self.db, 9).reviews()["total"], 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM leads").fetchone()[0], 1)

    def test_api_feedback_is_consistent_with_bot_review_flow(self):
        ingest(self.db, self.message, 9)
        process_one(self.db, lambda _: ("dev",))
        self.api.request(
            "POST",
            "/api/leads/1/feedback",
            self.auth,
            {"decision": "needs_review", "reason": "Проверить"},
            "feedback_request_001",
        )
        self.assertEqual(Quality(self.db, 9).reviews()["total"], 1)
        self.api.request("POST", "/api/leads/1/feedback", self.auth, {"decision": "approved"}, "feedback_request_002")
        self.assertEqual(Quality(self.db, 9).reviews()["total"], 0)
        self.assertEqual(Quality(self.db, 9).report()["observed_approval_rate"], 1)

    def test_review_menu_escapes_text_and_displays_score(self):
        from bot_menu import show

        self.message["text"] = "Какую платформу выбрать для сайта <test>?"
        raw_id = ingest(self.db, self.message, 9)
        process_one(self.db, lambda _: ("dev",))
        telegram = Mock()
        with patch("bot_menu.MONITOR_DB", self.path):
            show(telegram, 9, f"review:{raw_id}", 27)
        content = telegram.call.call_args.kwargs["text"]
        self.assertIn("&lt;test&gt;", content)
        self.assertIn("Оценка правил", content)


if __name__ == "__main__":
    unittest.main()
