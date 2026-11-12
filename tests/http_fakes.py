"""Транспорт HTTP в памяти: настоящий Handler без сокетов."""

from email.message import Message
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

_SERVERS = {}


class ThreadingHTTPServer:
    def __init__(self, address, handler):
        self.server_port = 19000 + len(_SERVERS)
        self.handler = handler
        _SERVERS[self.server_port] = self

    def serve_forever(self):
        pass

    def shutdown(self):
        pass

    def server_close(self):
        _SERVERS.pop(self.server_port, None)


class HTTPConnection:
    def __init__(self, host, port, timeout=None):
        self.server = _SERVERS[port]
        self.client_address = ("127.0.0.1", 1234)

    def request(self, method, path, body=None, headers=None):
        handler = self.server.handler.__new__(self.server.handler)
        handler.server = self.server
        handler.path = path
        handler.client_address = self.client_address
        handler.connection = Mock()
        handler.headers = Message()
        raw = body.encode() if isinstance(body, str) else body or b""
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        handler.headers["Content-Length"] = str(len(raw))
        handler.rfile = BytesIO(raw)
        handler.wfile = BytesIO()
        response_headers = {}
        status = []
        handler.send_response = status.append
        handler.send_header = response_headers.__setitem__
        handler.end_headers = lambda: None
        handler.handle_request(method)
        self.response = SimpleNamespace(status=status[0], read=handler.wfile.getvalue, getheader=response_headers.get)

    def getresponse(self):
        return self.response

    def close(self):
        pass
