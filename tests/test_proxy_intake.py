import unittest

from crm_api import client_ip, trusted_proxies
from reliability import migrate, open_database
from web_intake import RateLimited, submit


class ProxyIntakeTests(unittest.TestCase):
    def test_direct_client(self):
        self.assertEqual(client_ip("198.51.100.1", None), "198.51.100.1")

    def test_trusted_chain_uses_rightmost_untrusted(self):
        networks = trusted_proxies("127.0.0.1,10.0.0.0/8")
        self.assertEqual(client_ip("127.0.0.1", "203.0.113.7,198.51.100.2,10.0.0.2", networks), "198.51.100.2")
        self.assertEqual(client_ip("127.0.0.1", "bad", networks), "127.0.0.1")

    def test_untrusted_cannot_spoof(self):
        self.assertEqual(client_ip("198.51.100.1", "203.0.113.7", trusted_proxies("127.0.0.1")), "198.51.100.1")

    def test_rejections_consume_limit(self):
        db = open_database(":memory:")
        self.addCleanup(db.close)
        migrate(db)
        for _ in range(5):
            with self.assertRaises(ValueError):
                submit(db, 9, {}, "same-client")
        with self.assertRaises(RateLimited):
            submit(
                db,
                9,
                {
                    "contact": "@test",
                    "description": "Нужен сайт для магазина",
                    "consent": True,
                    "request_id": "valid-request-000001",
                },
                "same-client",
            )
        self.assertEqual(
            db.execute("SELECT count(*) FROM web_intake_attempts WHERE outcome='rejected'").fetchone()[0], 6
        )
        self.assertEqual(db.execute("SELECT count(*) FROM leads").fetchone()[0], 0)


class ProxyHTTPTests(unittest.TestCase):
    def test_trusted_proxy_has_independent_limits_and_untrusted_header_is_ignored(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path

        from http_fakes import HTTPConnection, ThreadingHTTPServer

        from crm_api import CRMApp, Handler

        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "monitor.sqlite3"
            db = open_database(database)
            migrate(db)
            db.close()
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self.addCleanup(server.server_close)
            server.app = CRMApp(database, 9, "x" * 40, "127.0.0.1")
            connection = HTTPConnection("127.0.0.1", server.server_port)
            for number in range(7):
                ip = "198.51.100.1" if number < 6 else "198.51.100.2"
                data = {
                    "contact": "@test",
                    "description": "Нужен сайт для магазина",
                    "consent": True,
                    "request_id": f"proxy-request-{number:06d}",
                }
                connection.request(
                    "POST",
                    "/public/intake",
                    json.dumps(data),
                    {
                        "Content-Type": "application/json",
                        "X-Forwarded-For": ip,
                        "Origin": f"http://127.0.0.1:{server.server_port}",
                    },
                )
                self.assertEqual(connection.getresponse().status, 429 if number == 5 else 200)
            connection.client_address = ("203.0.113.9", 1234)
            connection.request(
                "POST",
                "/public/intake",
                "{}",
                {
                    "Content-Type": "application/json",
                    "X-Forwarded-For": "198.51.100.1",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                },
            )
            self.assertEqual(connection.getresponse().status, 400)
            db = open_database(database)
            try:
                expected = hashlib.sha256(b"byte-intake:9:203.0.113.9").hexdigest()
                self.assertEqual(
                    db.execute("SELECT outcome FROM web_intake_attempts WHERE client_hash=?", (expected,)).fetchone()[
                        0
                    ],
                    "rejected",
                )
            finally:
                db.close()

    def test_invalid_request_id_is_recorded_without_partial_lead(self):
        db = open_database(":memory:")
        self.addCleanup(db.close)
        migrate(db)
        with self.assertRaises(ValueError):
            submit(db, 9, {"request_id": []}, "test")
        self.assertEqual(db.execute("SELECT outcome FROM web_intake_attempts").fetchone()[0], "rejected")
