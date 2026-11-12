"""Проверка существующего лимита Content-Length без сокетов."""

import unittest
from unittest.mock import Mock

from http_fakes import HTTPConnection, ThreadingHTTPServer

from crm_api import Handler


class RequestSizeTests(unittest.TestCase):
    def setUp(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        server.app = Mock()
        server.app.request.return_value = ("application/json", b"{}")
        self.app = server.app
        self.connection = HTTPConnection("127.0.0.1", server.server_port)

    def test_boundary_body_is_allowed_for_post_and_patch(self):
        for method in ("POST", "PATCH"):
            with self.subTest(method=method):
                self.connection.request(
                    method, "/api/leads", "{}" + " " * (262144 - 2), {"Content-Type": "application/json"}
                )
                self.assertEqual(self.connection.getresponse().status, 200)
        self.assertEqual(self.app.request.call_count, 2)

    def test_oversize_body_is_rejected_before_api_for_post_and_patch(self):
        for method in ("POST", "PATCH"):
            with self.subTest(method=method):
                self.connection.request(
                    method, "/api/leads", "{}" + " " * (262145 - 2), {"Content-Type": "application/json"}
                )
                self.assertEqual(self.connection.getresponse().status, 400)
        self.app.request.assert_not_called()

    def test_invalid_and_negative_content_length_are_rejected(self):
        for length in ("-1", "not-a-number"):
            with self.subTest(length=length):
                self.connection.request(
                    "POST", "/api/leads", "{}", {"Content-Type": "application/json", "Content-Length": length}
                )
                self.assertEqual(self.connection.getresponse().status, 400)
        self.app.request.assert_not_called()
