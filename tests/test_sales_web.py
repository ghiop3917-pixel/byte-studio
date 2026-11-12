import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from http_fakes import HTTPConnection, ThreadingHTTPServer

from crm import CRM, NotFound
from crm_api import CRMApp, Handler
from reliability import migrate, open_database
from sales import prepare


class SalesTests(unittest.TestCase):
    def setUp(self):
        self.db = open_database(":memory:")
        migrate(self.db)
        self.crm = CRM(self.db, 9)
        self.lead = self.crm.create({"title": "Сайт для кафе"})["id"]

    def tearDown(self):
        self.db.close()

    def test_proposal_uses_only_supplied_price_and_timeline(self):
        result = prepare(
            self.crm,
            self.lead,
            {"price": "45000", "currency": "RUB", "timeline": "После согласования", "deliverables": "Лендинг и форма"},
        )
        draft = self.crm.draft(result["draft_id"])
        self.assertIn("45000 RUB", draft["text"])
        self.assertIn("Лендинг и форма", draft["text"])
        self.assertEqual(draft["status"], "draft")
        self.assertFalse(result["sent"])
        self.assertEqual(self.crm.related("conversations", self.lead), [])

    def test_empty_financial_fields_not_invented(self):
        result = prepare(self.crm, self.lead, {})
        draft = self.crm.draft(result["draft_id"])
        self.assertNotIn("Стоимость:", draft["text"])
        self.assertNotIn("Сроки:", draft["text"])
        self.assertIsNone(self.crm.lead(self.lead)["confirmed_budget"])

    def test_invalid_reminder_rolls_back_entire_operation(self):
        with self.assertRaises(ValueError):
            prepare(self.crm, self.lead, {"kind": "followup", "due_at": "bad"})
        self.assertEqual(self.crm.related("drafts", self.lead), [])
        self.assertEqual(self.crm.related("activities", self.lead)[-1]["kind"], "lead_registered")

    def test_other_owner_cannot_prepare_sales(self):
        with self.assertRaises(NotFound):
            prepare(CRM(self.db, 10), self.lead, {})

    def test_reminder_and_followup_are_saved_together(self):
        result = prepare(self.crm, self.lead, {"kind": "followup", "due_at": "2027-01-01T12:00:00+03:00"})
        self.assertIsNotNone(result["reminder_id"])
        self.assertIn("ещё актуален", self.crm.draft(result["draft_id"])["text"])

    def test_invalid_prices_and_document_type(self):
        for data in ({"price": "-1", "currency": "RUB"}, {"price": "1"}, {"kind": "send"}):
            with self.assertRaises(ValueError):
                prepare(self.crm, self.lead, data)
        self.assertEqual(self.crm.related("drafts", self.lead), [])


class WebHTTPTests(unittest.TestCase):
    def test_sales_api_idempotency_and_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            db = open_database(path)
            migrate(db)
            lead = CRM(db, 9).create({"title": "Сайт"})["id"]
            db.close()
            app = CRMApp(path, 9, "x" * 40)
            auth = "Bearer " + app.token
            target = f"/api/leads/{lead}/sales"
            first = app.request("POST", target, auth, {"kind": "proposal"}, "sales-test-request-1")
            self.assertEqual(first, app.request("POST", target, auth, {"kind": "proposal"}, "sales-test-request-1"))
            db = open_database(path)
            self.assertEqual(db.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)
            db.close()
            result = json.loads(app.request("GET", "/api/runtime", auth)[1])
            self.assertEqual(result["sources"], [])
            self.assertEqual(result["notifications"], {})

    def test_static_allowlist_and_origin_protection(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = Mock()
        server.app.request.return_value = ("application/json", b"{}")
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        conn = HTTPConnection("127.0.0.1", server.server_port)
        try:
            for path in ("/", "/studio.css", "/studio.js", "/crm", "/crm/app.css", "/crm/app.js"):
                conn.request("GET", path)
                response = conn.getresponse()
                self.assertEqual(response.status, 200, path)
                self.assertTrue(response.getheader("Content-Security-Policy"))
                self.assertTrue(response.read())
            conn.request(
                "POST",
                "/api/leads",
                body="{}",
                headers={"Origin": f"http://127.0.0.1:{server.server_port}", "Content-Type": "application/json"},
            )
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            conn.request("GET", "/api/leads", headers={"Origin": "https://evil.example"})
            response = conn.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            self.assertNotIn("monitor.sqlite3", Path("web/studio/index.html").read_text(encoding="utf-8"))
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
