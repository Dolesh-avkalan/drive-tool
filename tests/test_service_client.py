"""Tests for drive_tool.service_client (D047).

These tests exercise the four-method loopback client against *tiny simulated
HTTP services* (``http.server`` on 127.0.0.1) plus controlled transport seams
for pre-I/O validation, connection-per-call and close counting.

A simulated HTTP service is NOT the real Drive service: passing these tests
does not establish real service integration or acceptance. They only pin the
client's own wire behaviour and its local safety properties.
"""

from __future__ import annotations

import http.client
import http.server
import json
import socket
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

from drive_tool.service_client import DriveClient, DriveClientError, ReadReply

TOKEN = "t" * 40  # 40 visible ASCII characters (33..126)


# ---------------------------------------------------------------------------
# Simulated HTTP service
# ---------------------------------------------------------------------------


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    server_version = "FakeDrive/1.0"

    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "raw_headers": list(self.headers.items()),
            "body": body,
        }
        self.server.records.append(record)
        self.server.responder(self, record)

    def log_message(self, *args):  # silence the simulated service
        pass


class _FakeDriveServer:
    """Context manager running a simulated Drive HTTP service on loopback."""

    def __init__(self, responder):
        self.records = []
        handler = type("Handler", (_RecordingHandler,), {})
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self._httpd.records = self.records
        self._httpd.responder = responder
        self.port = self._httpd.server_address[1]
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(
            target=lambda: self._httpd.serve_forever(poll_interval=0.02),
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        return False


def _raw_responder(status, body=b"", headers=(), content_type=None, declared_cl="auto"):
    def responder(handler, record):
        handler.send_response(status)
        if content_type is not None:
            handler.send_header("Content-Type", content_type)
        for key, value in headers:
            handler.send_header(key, value)
        if declared_cl == "auto":
            if body:
                handler.send_header("Content-Length", str(len(body)))
        elif declared_cl is not None:
            handler.send_header("Content-Length", str(declared_cl))
        handler.end_headers()
        if body:
            handler.wfile.write(body)

    return responder


def _json_responder(status, obj, headers=(), declared_cl="auto"):
    body = json.dumps(obj).encode("utf-8")
    return _raw_responder(
        status,
        body,
        headers=headers,
        content_type="application/json",
        declared_cl=declared_cl,
    )


def _ok_responder(result):
    return _json_responder(200, {"ok": True, "result": result})


def _read_responder(data, offset, size, mtime, eof, content_type="application/octet-stream"):
    def responder(handler, record):
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("X-Drive-Offset", str(offset))
        handler.send_header("X-Drive-Observed-Size", str(size))
        handler.send_header("X-Drive-Observed-Mtime-Ns", str(mtime))
        handler.send_header("X-Drive-EOF-Observed", "true" if eof else "false")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    return responder


def _client(port, token=TOKEN, timeout=5.0):
    return DriveClient(port=port, token=token, timeout=timeout)


# ---------------------------------------------------------------------------
# Controlled transport seam (no real sockets)
# ---------------------------------------------------------------------------


class _FakeHeaders:
    def __init__(self, pairs):
        self._pairs = list(pairs)

    def get_all(self, name):
        values = [v for k, v in self._pairs if k.lower() == name.lower()]
        return values or None

    def get(self, name, default=None):
        values = self.get_all(name)
        return values[0] if values else default


class _FakeResponse:
    def __init__(self, status, pairs, body):
        self.status = status
        self.headers = _FakeHeaders(pairs)
        self._body = body
        self._pos = 0
        self.closed = False

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, amt=None):
        if amt is None:  # pragma: no cover - the client never reads unbounded
            raise AssertionError("client must not read the response unbounded")
        chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self, response=None, raise_on_getresponse=None, raise_on_request=None):
        self._response = response
        self._raise_getresponse = raise_on_getresponse
        self._raise_request = raise_on_request
        self.requests = []
        self.closed = False

    def request(self, method, url, body=None, headers=None):
        if self._raise_request is not None:
            raise self._raise_request
        self.requests.append((method, url, body, dict(headers or {})))

    def getresponse(self):
        if self._raise_getresponse is not None:
            raise self._raise_getresponse
        return self._response

    def close(self):
        self.closed = True


class _Seam:
    """Patch a client's ``_open`` seam, recording every connection created."""

    def __init__(self, client, connections):
        self.client = client
        self.connections = connections
        self.calls = 0

    def __enter__(self):
        self._orig = self.client._open

        def _open():
            conn = self.connections[self.calls]
            self.calls += 1
            return conn

        self.client._open = _open
        return self

    def __exit__(self, *exc):
        self.client._open = self._orig
        return False


def _exploding_open(client):
    def _open():
        raise AssertionError("network must not be used")

    client._open = _open


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor(unittest.TestCase):
    def test_valid_constructor_and_repr_is_redacted(self):
        client = _client(8080)
        self.assertNotIn(TOKEN, repr(client))
        self.assertIn("<redacted>", repr(client))
        self.assertNotIn(TOKEN, str(client))

    def test_port_type_errors(self):
        for bad in (True, False, "8080", 8080.0, None):
            with self.assertRaises(TypeError):
                DriveClient(port=bad, token=TOKEN)

    def test_port_range_errors(self):
        for bad in (0, -1, 65536, 100000):
            with self.assertRaises(ValueError):
                DriveClient(port=bad, token=TOKEN)

    def test_token_type_error(self):
        for bad in (b"x" * 40, 12345, None):
            with self.assertRaises(TypeError):
                DriveClient(port=8080, token=bad)

    def test_token_length_errors(self):
        for bad in ("t" * 31, "t" * 257, ""):
            with self.assertRaises(ValueError):
                DriveClient(port=8080, token=bad)

    def test_token_character_errors(self):
        for bad in ("t" * 31 + " ", "t" * 31 + "\x00", "t" * 31 + "\x7f", "t" * 31 + "\u00e9"):
            with self.assertRaises(ValueError):
                DriveClient(port=8080, token=bad)

    def test_timeout_type_errors(self):
        for bad in (True, "5", None):
            with self.assertRaises(TypeError):
                DriveClient(port=8080, token=TOKEN, timeout=bad)

    def test_timeout_range_errors(self):
        for bad in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                DriveClient(port=8080, token=TOKEN, timeout=bad)

    def test_timeout_accepts_positive_int(self):
        DriveClient(port=8080, token=TOKEN, timeout=3)

    def test_constructor_performs_no_network(self):
        original = http.client.HTTPConnection

        def _boom(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("constructor must not open a connection")

        http.client.HTTPConnection = _boom
        try:
            DriveClient(port=8080, token=TOKEN)
        finally:
            http.client.HTTPConnection = original


# ---------------------------------------------------------------------------
# Validation happens before any network
# ---------------------------------------------------------------------------


class TestValidationBeforeNetwork(unittest.TestCase):
    def setUp(self):
        self.client = _client(1)
        _exploding_open(self.client)

    def _assert_raises(self, exc_type, fn, *args, **kwargs):
        with self.assertRaises(exc_type):
            fn(*args, **kwargs)

    def test_list_validation(self):
        c = self.client
        self._assert_raises(TypeError, c.list, 123)
        self._assert_raises(ValueError, c.list, "")
        self._assert_raises(ValueError, c.list, "a" * 65)
        self._assert_raises(ValueError, c.list, "bad id")
        self._assert_raises(ValueError, c.list, "src", "")
        self._assert_raises(ValueError, c.list, "src", "a/../b")
        self._assert_raises(ValueError, c.list, "src", "/abs")
        self._assert_raises(ValueError, c.list, "src", "a\\b")
        self._assert_raises(ValueError, c.list, "src", "a\x00b")
        self._assert_raises(ValueError, c.list, "src", "a//b")
        self._assert_raises(TypeError, c.list, "src", limit=True)
        self._assert_raises(ValueError, c.list, "src", limit=0)
        self._assert_raises(ValueError, c.list, "src", limit=101)
        self._assert_raises(TypeError, c.list, "src", offset=True)
        self._assert_raises(ValueError, c.list, "src", offset=-1)
        self._assert_raises(TypeError, c.list, "src", name_contains=5)
        self._assert_raises(ValueError, c.list, "src", name_contains="x" * 257)

    def test_find_validation(self):
        c = self.client
        self._assert_raises(TypeError, c.find, "src")  # name_contains required
        self._assert_raises(TypeError, c.find, "src", name_contains=None)
        self._assert_raises(ValueError, c.find, "src", name_contains="")
        self._assert_raises(ValueError, c.find, "src", name_contains="x" * 257)
        self._assert_raises(TypeError, c.find, "src", name_contains="a", limit=True)
        self._assert_raises(ValueError, c.find, "src", name_contains="a", limit=101)
        self._assert_raises(TypeError, c.find, "src", name_contains="a", max_entries=True)
        self._assert_raises(ValueError, c.find, "src", name_contains="a", max_entries=0)
        self._assert_raises(ValueError, c.find, "src", name_contains="a", max_entries=100001)
        self._assert_raises(TypeError, c.find, "src", name_contains="a", max_depth=True)
        self._assert_raises(ValueError, c.find, "src", name_contains="a", max_depth=-1)
        self._assert_raises(ValueError, c.find, "src", name_contains="a", max_depth=65)

    def test_read_validation(self):
        c = self.client
        self._assert_raises(TypeError, c.read, "src")  # file_id required
        self._assert_raises(ValueError, c.read, "src", "root")  # nonroot required
        self._assert_raises(TypeError, c.read, "src", "p/f", offset=True)
        self._assert_raises(ValueError, c.read, "src", "p/f", offset=-1)
        self._assert_raises(TypeError, c.read, "src", "p/f", length=True)
        self._assert_raises(ValueError, c.read, "src", "p/f", length=0)
        self._assert_raises(ValueError, c.read, "src", "p/f", length=1048577)
        self._assert_raises(ValueError, c.read, "src", "p/f", offset=2**63 - 1, length=1)

    def test_save_validation(self):
        c = self.client
        self._assert_raises(TypeError, c.save, "src")  # file_id required
        self._assert_raises(ValueError, c.save, "src", "root", b"x")
        self._assert_raises(TypeError, c.save, "src", "p/f", "not-bytes")
        self._assert_raises(TypeError, c.save, "src", "p/f", bytearray(b"x"))
        self._assert_raises(TypeError, c.save, "src", "p/f", None)
        self._assert_raises(ValueError, c.save, "src", "p/f", b"x" * 1048577)
        self._assert_raises(TypeError, c.save, "src", "p/f", b"x", overwrite=1)
        self._assert_raises(TypeError, c.save, "src", "p/f", b"x", create_parents=1)


# ---------------------------------------------------------------------------
# Wire behaviour against the simulated service
# ---------------------------------------------------------------------------


class TestListWire(unittest.TestCase):
    def test_list_request_and_reply(self):
        result = {"items": [{"name": "a"}], "next_offset": None}
        with _FakeDriveServer(_ok_responder(result)) as server:
            reply = _client(server.port).list("src")
            self.assertEqual(reply, result)
            self.assertEqual(len(server.records), 1)
            record = server.records[0]
            self.assertEqual(record["method"], "POST")
            self.assertEqual(record["path"], "/v1/list")
            headers = record["headers"]
            self.assertEqual(headers["content-type"], "application/json")
            self.assertEqual(headers["authorization"], "Bearer " + TOKEN)
            self.assertEqual(headers["connection"], "close")
            self.assertEqual(
                headers["content-length"], str(len(record["body"]))
            )
            self.assertEqual(
                record["body"],
                b'{"source_id":"src","file_id":"root","limit":20,"offset":0}',
            )

    def test_list_name_contains_omitted_when_none(self):
        with _FakeDriveServer(_ok_responder({})) as server:
            _client(server.port).list("src", name_contains=None)
            self.assertNotIn(b"name_contains", server.records[0]["body"])

    def test_list_name_contains_present_and_utf8(self):
        with _FakeDriveServer(_ok_responder({})) as server:
            _client(server.port).list("src", limit=5, offset=2, name_contains="caf\u00e9")
            body = server.records[0]["body"]
            self.assertEqual(
                body,
                '{"source_id":"src","file_id":"root","limit":5,"offset":2,"name_contains":"caf\u00e9"}'.encode(
                    "utf-8"
                ),
            )


class TestFindWire(unittest.TestCase):
    def test_find_request_and_reply(self):
        result = {
            "items": [],
            "truncated": False,
            "reason": None,
            "inspected": 0,
            "skipped": 0,
        }
        with _FakeDriveServer(_ok_responder(result)) as server:
            reply = _client(server.port).find("src", name_contains="log")
            self.assertEqual(reply, result)
            record = server.records[0]
            self.assertEqual(record["path"], "/v1/find")
            self.assertEqual(record["headers"]["content-type"], "application/json")
            self.assertEqual(
                record["body"],
                b'{"source_id":"src","file_id":"root","name_contains":"log",'
                b'"limit":20,"max_entries":2000,"max_depth":4}',
            )

    def test_find_custom_bounds(self):
        with _FakeDriveServer(_ok_responder({})) as server:
            _client(server.port).find(
                "src", "p/dir", name_contains="x", limit=3, max_entries=10, max_depth=0
            )
            self.assertEqual(
                server.records[0]["body"],
                b'{"source_id":"src","file_id":"p/dir","name_contains":"x",'
                b'"limit":3,"max_entries":10,"max_depth":0}',
            )


class TestReadWire(unittest.TestCase):
    def test_read_request_and_reply(self):
        with _FakeDriveServer(_read_responder(b"hello", 0, 5, 111, True)) as server:
            reply = _client(server.port).read("src", "p/f", offset=0, length=10)
            self.assertIsInstance(reply, ReadReply)
            self.assertEqual(reply.data, b"hello")
            self.assertEqual(reply.offset, 0)
            self.assertEqual(reply.observed_size, 5)
            self.assertEqual(reply.observed_mtime_ns, 111)
            self.assertIs(reply.eof_observed, True)
            record = server.records[0]
            self.assertEqual(record["path"], "/v1/read")
            self.assertEqual(record["headers"]["content-type"], "application/json")
            self.assertEqual(
                record["body"],
                b'{"source_id":"src","file_id":"p/f","offset":0,"length":10}',
            )

    def test_read_exact_fill_is_not_eof(self):
        with _FakeDriveServer(_read_responder(b"abcd", 0, 99, 1, False)) as server:
            reply = _client(server.port).read("src", "p/f", offset=0, length=4)
            self.assertEqual(reply.data, b"abcd")
            self.assertIs(reply.eof_observed, False)

    def test_read_stale_size_under_growth_is_allowed(self):
        # observed_size may legitimately be smaller than offset + len(data).
        with _FakeDriveServer(_read_responder(b"abcd", 10, 12, 1, False)) as server:
            reply = _client(server.port).read("src", "p/f", offset=10, length=4)
            self.assertEqual(reply.observed_size, 12)
            self.assertEqual(len(reply.data), 4)

    def test_read_reply_is_frozen(self):
        reply = ReadReply(b"x", 0, 1, 1, True)
        with self.assertRaises(Exception):
            reply.data = b"y"  # type: ignore[misc]

    def test_read_wrong_content_type_fails_closed(self):
        responder = _read_responder(b"x", 0, 1, 1, True, content_type="text/plain")
        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).read("src", "p/f", offset=0, length=1)
            self.assertEqual(ctx.exception.code, "unavailable")

    def test_read_missing_header_fails_closed(self):
        def responder(handler, record):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("X-Drive-Offset", "0")
            # X-Drive-Observed-Size / mtime / EOF all missing
            handler.send_header("Content-Length", "1")
            handler.end_headers()
            handler.wfile.write(b"x")

        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=1)

    def test_read_duplicate_header_fails_closed(self):
        def responder(handler, record):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("X-Drive-Offset", "0")
            handler.send_header("X-Drive-Offset", "0")
            handler.send_header("X-Drive-Observed-Size", "1")
            handler.send_header("X-Drive-Observed-Mtime-Ns", "1")
            handler.send_header("X-Drive-EOF-Observed", "true")
            handler.send_header("Content-Length", "1")
            handler.end_headers()
            handler.wfile.write(b"x")

        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=1)

    def test_read_invalid_decimal_header_fails_closed(self):
        def responder(handler, record):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("X-Drive-Offset", "0")
            handler.send_header("X-Drive-Observed-Size", "-5")
            handler.send_header("X-Drive-Observed-Mtime-Ns", "1")
            handler.send_header("X-Drive-EOF-Observed", "true")
            handler.send_header("Content-Length", "1")
            handler.end_headers()
            handler.wfile.write(b"x")

        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=1)

    def test_read_invalid_eof_token_fails_closed(self):
        def responder(handler, record):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("X-Drive-Offset", "0")
            handler.send_header("X-Drive-Observed-Size", "1")
            handler.send_header("X-Drive-Observed-Mtime-Ns", "1")
            handler.send_header("X-Drive-EOF-Observed", "yes")
            handler.send_header("Content-Length", "1")
            handler.end_headers()
            handler.wfile.write(b"x")

        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=1)

    def test_read_offset_mismatch_fails_closed(self):
        with _FakeDriveServer(_read_responder(b"x", 5, 10, 1, True)) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=1)

    def test_read_short_window_without_eof_fails_closed(self):
        with _FakeDriveServer(_read_responder(b"x", 0, 10, 1, False)) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=5)

    def test_read_exact_fill_with_eof_is_protocol_invalid(self):
        with _FakeDriveServer(_read_responder(b"xxxxx", 0, 5, 1, True)) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=5)


class TestSaveWire(unittest.TestCase):
    def test_save_request_and_reply(self):
        result = {"ref": "p/f", "bytes_written": 3, "parents_may_have_been_created": True}
        with _FakeDriveServer(_ok_responder(result)) as server:
            reply = _client(server.port).save("src", "p/f", b"abc")
            self.assertEqual(reply, result)
            record = server.records[0]
            self.assertEqual(
                record["path"],
                "/v1/save?source_id=src&file_id=p%2Ff&overwrite=false&create_parents=true",
            )
            self.assertEqual(record["headers"]["content-type"], "application/octet-stream")
            self.assertEqual(record["headers"]["authorization"], "Bearer " + TOKEN)
            self.assertEqual(record["headers"]["content-length"], "3")
            self.assertEqual(record["body"], b"abc")

    def test_save_flag_encoding(self):
        with _FakeDriveServer(_ok_responder({})) as server:
            _client(server.port).save(
                "src", "p/f", b"", overwrite=True, create_parents=False
            )
            path = server.records[0]["path"]
            query = parse_qs(urlsplit(path).query)
            self.assertEqual(query["overwrite"], ["true"])
            self.assertEqual(query["create_parents"], ["false"])

    def test_save_query_quoting_round_trips(self):
        with _FakeDriveServer(_ok_responder({})) as server:
            _client(server.port).save("src", "p/a b/c+d", b"x")
            path = server.records[0]["path"]
            self.assertTrue(path.startswith("/v1/save?"))
            query = parse_qs(urlsplit(path).query)
            self.assertEqual(query["source_id"], ["src"])
            self.assertEqual(query["file_id"], ["p/a b/c+d"])

    def test_save_never_puts_credentials_in_query(self):
        with _FakeDriveServer(_ok_responder({})) as server:
            _client(server.port).save("src", "p/f", b"x")
            path = server.records[0]["path"]
            self.assertNotIn(TOKEN, path)


# ---------------------------------------------------------------------------
# Error envelopes and redaction
# ---------------------------------------------------------------------------


class TestErrorEnvelopes(unittest.TestCase):
    def _error(self, status, code, uncertain, retry_safe, **extra):
        error = {"code": code, "outcome_uncertain": uncertain, "retry_safe": retry_safe}
        error.update(extra)
        return _json_responder(status, {"ok": False, "error": error})

    def test_known_codes_recognized(self):
        codes = [
            "invalid",
            "denied",
            "not_found",
            "exists",
            "too_large",
            "busy",
            "unavailable",
            "timeout",
        ]
        for code in codes:
            with _FakeDriveServer(self._error(400, code, False, True)) as server:
                with self.assertRaises(DriveClientError) as ctx:
                    _client(server.port).list("src")
                self.assertEqual(ctx.exception.code, code)
                self.assertIs(ctx.exception.outcome_uncertain, False)
                self.assertIs(ctx.exception.retry_safe, True)

    def test_uncertainty_flags_honored(self):
        with _FakeDriveServer(self._error(503, "busy", True, False)) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).list("src")
            self.assertEqual(ctx.exception.code, "busy")
            self.assertIs(ctx.exception.outcome_uncertain, True)
            self.assertIs(ctx.exception.retry_safe, False)

    def test_unknown_code_maps_to_unavailable(self):
        with _FakeDriveServer(self._error(400, "weird", False, True)) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).list("src")
            self.assertEqual(ctx.exception.code, "unavailable")

    def test_malformed_json_maps_to_unavailable(self):
        with _FakeDriveServer(_raw_responder(500, b"not json", content_type="text/plain")) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).list("src")
            self.assertEqual(ctx.exception.code, "unavailable")

    def test_ok_true_on_non_200_maps_to_unavailable(self):
        with _FakeDriveServer(_json_responder(500, {"ok": True, "result": {}})) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).list("src")
            self.assertEqual(ctx.exception.code, "unavailable")

    def test_missing_flags_use_read_only_defaults(self):
        with _FakeDriveServer(_json_responder(400, {"ok": False, "error": {"code": "invalid"}})) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).list("src")
            self.assertEqual(ctx.exception.code, "invalid")
            self.assertIs(ctx.exception.outcome_uncertain, False)
            self.assertIs(ctx.exception.retry_safe, True)

    def test_missing_flags_use_write_defaults_for_save(self):
        with _FakeDriveServer(_json_responder(400, {"ok": False, "error": {"code": "invalid"}})) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).save("src", "p/f", b"x")
            self.assertEqual(ctx.exception.code, "invalid")
            self.assertIs(ctx.exception.outcome_uncertain, True)
            self.assertIs(ctx.exception.retry_safe, False)

    def test_error_is_redacted_even_if_server_echoes_token(self):
        responder = self._error(
            403,
            "denied",
            False,
            True,
            message="token %s rejected at root /secret/root" % TOKEN,
        )
        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).list("src")
            text = str(ctx.exception) + repr(ctx.exception)
            self.assertNotIn(TOKEN, text)
            self.assertNotIn("root", text)
            self.assertNotIn("secret", text)


# ---------------------------------------------------------------------------
# Bounded replies
# ---------------------------------------------------------------------------


class TestBoundedReplies(unittest.TestCase):
    def test_json_success_body_over_cap_fails(self):
        big = b"x" * 70000
        with _FakeDriveServer(_raw_responder(200, big, content_type="application/json", declared_cl=None)) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).list("src")

    def test_declared_content_length_over_cap_fails_before_read(self):
        responder = _raw_responder(
            200, b"", content_type="application/json", declared_cl=70000
        )
        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).list("src")

    def test_error_body_over_cap_fails(self):
        big = b"x" * 70000
        with _FakeDriveServer(_raw_responder(500, big, content_type="application/json", declared_cl=None)) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).list("src")

    def test_read_over_requested_length_fails(self):
        with _FakeDriveServer(_read_responder(b"xxxxx", 0, 5, 1, True)) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=4)

    def test_read_declared_length_over_request_fails_before_read(self):
        def responder(handler, record):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("X-Drive-Offset", "0")
            handler.send_header("X-Drive-Observed-Size", "5")
            handler.send_header("X-Drive-Observed-Mtime-Ns", "1")
            handler.send_header("X-Drive-EOF-Observed", "false")
            handler.send_header("Content-Length", "5")
            handler.end_headers()
            handler.wfile.write(b"x")

        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError):
                _client(server.port).read("src", "p/f", offset=0, length=4)

    def test_save_success_body_over_cap_is_uncertain(self):
        big = b"x" * 70000
        with _FakeDriveServer(_raw_responder(200, big, content_type="application/json", declared_cl=None)) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).save("src", "p/f", b"x")
            self.assertIs(ctx.exception.outcome_uncertain, True)
            self.assertIs(ctx.exception.retry_safe, False)


# ---------------------------------------------------------------------------
# Transport failures, timeouts, no retries
# ---------------------------------------------------------------------------


class TestTransport(unittest.TestCase):
    def test_connection_refused_is_unavailable(self):
        # Port 1 on loopback is not served; connecting fails immediately.
        client = _client(1, timeout=0.5)
        with self.assertRaises(DriveClientError) as ctx:
            client.list("src")
        self.assertEqual(ctx.exception.code, "unavailable")
        self.assertIs(ctx.exception.outcome_uncertain, False)
        self.assertIs(ctx.exception.retry_safe, True)

    def test_timeout_maps_to_timeout_code(self):
        resp_conn = _FakeConnection(raise_on_getresponse=socket.timeout("slow"))
        client = _client(1)
        with _Seam(client, [resp_conn]):
            with self.assertRaises(DriveClientError) as ctx:
                client.list("src")
            self.assertEqual(ctx.exception.code, "timeout")
            self.assertIs(ctx.exception.outcome_uncertain, False)
            self.assertIs(ctx.exception.retry_safe, True)
            self.assertTrue(resp_conn.closed)

    def test_save_transport_failure_is_uncertain_and_not_retry_safe(self):
        conn = _FakeConnection(raise_on_request=OSError("broken pipe"))
        client = _client(1)
        with _Seam(client, [conn]):
            with self.assertRaises(DriveClientError) as ctx:
                client.save("src", "p/f", b"x")
            self.assertEqual(ctx.exception.code, "unavailable")
            self.assertIs(ctx.exception.outcome_uncertain, True)
            self.assertIs(ctx.exception.retry_safe, False)
            self.assertTrue(conn.closed)

    def test_no_automatic_retry_on_read_only(self):
        conns = [_FakeConnection(raise_on_getresponse=OSError("reset"))]
        client = _client(1)
        with _Seam(client, conns) as seam:
            with self.assertRaises(DriveClientError):
                client.list("src")
            self.assertEqual(seam.calls, 1)

    def test_no_automatic_retry_on_save(self):
        conns = [_FakeConnection(raise_on_request=OSError("reset"))]
        client = _client(1)
        with _Seam(client, conns) as seam:
            with self.assertRaises(DriveClientError):
                client.save("src", "p/f", b"x")
            self.assertEqual(seam.calls, 1)


# ---------------------------------------------------------------------------
# Close per call and connection isolation
# ---------------------------------------------------------------------------


class TestCloseAndIsolation(unittest.TestCase):
    def test_connection_and_response_closed_on_success(self):
        resp = _FakeResponse(200, [("Content-Length", "21")], b'{"ok":true,"result":{}}')
        conn = _FakeConnection(resp)
        client = _client(1)
        with _Seam(client, [conn]):
            client.list("src")
        self.assertTrue(conn.closed)
        self.assertTrue(resp.closed)

    def test_connection_and_response_closed_on_error(self):
        resp = _FakeResponse(400, [], b'{"ok":false,"error":{"code":"invalid"}}')
        conn = _FakeConnection(resp)
        client = _client(1)
        with _Seam(client, [conn]):
            with self.assertRaises(DriveClientError):
                client.list("src")
        self.assertTrue(conn.closed)
        self.assertTrue(resp.closed)

    def test_open_returns_a_new_connection_each_time(self):
        client = _client(1)
        first = client._open()
        second = client._open()
        try:
            self.assertIsNot(first, second)
        finally:
            first.close()
            second.close()

    def test_two_calls_on_one_client_use_two_connections(self):
        with _FakeDriveServer(_ok_responder({})) as server:
            client = _client(server.port)
            client.list("src")
            client.list("src")
            self.assertEqual(len(server.records), 2)

    def test_new_connection_per_call_no_reuse(self):
        r1 = _FakeResponse(200, [], b'{"ok":true,"result":{"n":1}}')
        r2 = _FakeResponse(200, [], b'{"ok":true,"result":{"n":2}}')
        c1, c2 = _FakeConnection(r1), _FakeConnection(r2)
        client = _client(1)
        with _Seam(client, [c1, c2]) as seam:
            self.assertEqual(client.list("src"), {"n": 1})
            self.assertEqual(client.list("src"), {"n": 2})
            self.assertEqual(seam.calls, 2)
        self.assertIsNot(c1, c2)
        self.assertTrue(c1.closed)
        self.assertTrue(c2.closed)

    def test_concurrent_calls_are_independent(self):
        def responder(handler, record):
            payload = json.loads(record["body"].decode("utf-8"))
            result = {"items": [payload.get("name_contains")], "next_offset": None}
            body = json.dumps({"ok": True, "result": result}).encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        with _FakeDriveServer(responder) as server:
            client = _client(server.port)
            results = {}
            errors = []

            def worker(i):
                try:
                    results[i] = client.list("src", name_contains="name-%d" % i)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 8)
            for i in range(8):
                self.assertEqual(results[i]["items"], ["name-%d" % i])
            self.assertEqual(len(server.records), 8)


# ---------------------------------------------------------------------------
# Redirects
# ---------------------------------------------------------------------------


class TestRedirects(unittest.TestCase):
    def test_redirect_is_not_followed(self):
        responder = _raw_responder(
            301,
            b"",
            headers=[("Location", "http://127.0.0.1:9/elsewhere")],
            declared_cl=None,
        )
        with _FakeDriveServer(responder) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port).list("src")
            self.assertEqual(ctx.exception.code, "unavailable")
            self.assertEqual(len(server.records), 1)



# ---------------------------------------------------------------------------
# Response-body transport errors (D053)
#
# D051 primary finding: a transport failure while the response *body* is read
# (stall / truncation / reset / malformed chunk) escaped as a raw stdlib
# exception instead of a safe DriveClientError. These regressions pin the fix.
# ---------------------------------------------------------------------------


class _RaisingResponse:
    """A fake response whose body ``read`` raises a configured exception."""

    def __init__(self, exc, status=200, pairs=()):
        self.status = status
        self.headers = _FakeHeaders(pairs)
        self._exc = exc
        self.closed = False
        self.read_calls = 0

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, amt=None):
        self.read_calls += 1
        raise self._exc

    def close(self):
        self.closed = True


class _RawSocketServer:
    """One-shot raw loopback server that writes a fixed byte sequence."""

    def __init__(self, raw, stall=False):
        self._raw = raw
        self._stall = stall
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]

    def __enter__(self):
        def run():
            import time

            try:
                conn, _ = self._srv.accept()
                conn.settimeout(2.0)
                try:
                    buf = b""
                    while b"\r\n\r\n" not in buf:
                        data = conn.recv(4096)
                        if not data:
                            break
                        buf += data
                except OSError:
                    pass
                conn.sendall(self._raw)
                if self._stall:
                    time.sleep(1.5)
                conn.close()
            except OSError:
                pass
            finally:
                self._srv.close()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._thread.join(timeout=3)
        return False


class TestResponseBodyTransportErrors(unittest.TestCase):
    """A mid-body transport failure must map to a safe DriveClientError."""

    def _seam_client(self, resp):
        conn = _FakeConnection(resp)
        client = _client(1)
        return client, conn, resp, _Seam(client, [conn])

    def test_read_body_timeout_is_safe_timeout(self):
        resp = _RaisingResponse(socket.timeout("stalled"))
        client, conn, resp, seam = self._seam_client(resp)
        with seam:
            with self.assertRaises(DriveClientError) as ctx:
                client.read("src", "p/a.bin", offset=0, length=8)
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertIs(ctx.exception.outcome_uncertain, False)
        self.assertIs(ctx.exception.retry_safe, True)
        self.assertTrue(resp.closed)
        self.assertTrue(conn.closed)
        self.assertEqual(seam.calls, 1)

    def test_save_body_timeout_is_uncertain_not_retry_safe(self):
        resp = _RaisingResponse(socket.timeout("stalled"))
        client, conn, resp, seam = self._seam_client(resp)
        with seam:
            with self.assertRaises(DriveClientError) as ctx:
                client.save("src", "p/a.bin", b"x")
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertIs(ctx.exception.outcome_uncertain, True)
        self.assertIs(ctx.exception.retry_safe, False)
        self.assertTrue(resp.closed)
        self.assertTrue(conn.closed)

    def test_read_body_incomplete_read_is_unavailable(self):
        resp = _RaisingResponse(http.client.IncompleteRead(b"ab", 10))
        client, conn, resp, seam = self._seam_client(resp)
        with seam:
            with self.assertRaises(DriveClientError) as ctx:
                client.read("src", "p/a.bin", offset=0, length=8)
        self.assertEqual(ctx.exception.code, "unavailable")
        self.assertIs(ctx.exception.outcome_uncertain, False)
        self.assertIs(ctx.exception.retry_safe, True)

    def test_read_body_malformed_chunk_is_unavailable(self):
        resp = _RaisingResponse(http.client.HTTPException("bad chunk"))
        client, conn, resp, seam = self._seam_client(resp)
        with seam:
            with self.assertRaises(DriveClientError) as ctx:
                client.read("src", "p/a.bin", offset=0, length=8)
        self.assertEqual(ctx.exception.code, "unavailable")

    def test_save_body_connection_reset_is_uncertain(self):
        resp = _RaisingResponse(ConnectionResetError("reset"))
        client, conn, resp, seam = self._seam_client(resp)
        with seam:
            with self.assertRaises(DriveClientError) as ctx:
                client.save("src", "p/a.bin", b"x")
        self.assertEqual(ctx.exception.code, "unavailable")
        self.assertIs(ctx.exception.outcome_uncertain, True)
        self.assertIs(ctx.exception.retry_safe, False)

    def test_error_path_body_timeout_maps_to_timeout(self):
        resp = _RaisingResponse(socket.timeout("stalled"), status=500)
        client, conn, resp, seam = self._seam_client(resp)
        with seam:
            with self.assertRaises(DriveClientError) as ctx:
                client.list("src")
        self.assertEqual(ctx.exception.code, "timeout")

    def test_body_error_is_redacted(self):
        resp = _RaisingResponse(
            ConnectionResetError("Bearer " + TOKEN + " /srv/secret")
        )
        client, conn, resp, seam = self._seam_client(resp)
        with seam:
            with self.assertRaises(DriveClientError) as ctx:
                client.list("src")
        text = str(ctx.exception) + repr(ctx.exception)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("/srv/secret", text)

    def test_no_retry_on_body_transport_error(self):
        resp = _RaisingResponse(socket.timeout("stalled"))
        client, conn, resp, seam = self._seam_client(resp)
        with seam:
            with self.assertRaises(DriveClientError):
                client.read("src", "p/a.bin", offset=0, length=8)
        self.assertEqual(seam.calls, 1)
        self.assertEqual(resp.read_calls, 1)


class TestResponseBodyTransportErrorsFakeServer(unittest.TestCase):
    """Real tiny raw-socket servers: stalled and malformed/truncated chunked."""

    _READ_PREFIX = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
        b"X-Drive-Offset: 0\r\nX-Drive-Observed-Size: 3\r\n"
        b"X-Drive-Observed-Mtime-Ns: 1\r\nX-Drive-EOF-Observed: true\r\n"
    )

    def test_stalled_body_read_is_timeout(self):
        raw = self._READ_PREFIX + b"Content-Length: 100\r\n\r\n"
        with _RawSocketServer(raw, stall=True) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port, timeout=0.4).read(
                    "src", "p/a.bin", offset=0, length=262144
                )
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertIs(ctx.exception.outcome_uncertain, False)
        self.assertIs(ctx.exception.retry_safe, True)

    def test_malformed_chunk_body_is_unavailable(self):
        raw = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\nZZZ\r\n"
        )
        with _RawSocketServer(raw) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port, timeout=2.0).read(
                    "src", "p/a.bin", offset=0, length=8
                )
        self.assertEqual(ctx.exception.code, "unavailable")
        self.assertIs(ctx.exception.retry_safe, True)

    def test_truncated_chunked_body_is_unavailable(self):
        # Declares a 5-byte chunk but sends only 3 bytes, then closes.
        raw = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n5\r\nabc"
        )
        with _RawSocketServer(raw) as server:
            with self.assertRaises(DriveClientError) as ctx:
                _client(server.port, timeout=2.0).read(
                    "src", "p/a.bin", offset=0, length=8
                )
        self.assertEqual(ctx.exception.code, "unavailable")



# ---------------------------------------------------------------------------
# Canonical file_id prefix (D055)
#
# Director adjudication: every non-root file_id MUST start with "p/" and carry a
# non-empty canonical relative POSIX suffix; root is allowed only for list/find.
# The id is sent on the wire unchanged (the prefix is never stripped).
# ---------------------------------------------------------------------------


class TestCanonicalFileIdPrefix(unittest.TestCase):
    def setUp(self):
        # Rejections must happen before any socket is opened.
        self.client = _client(1)
        _exploding_open(self.client)

    def _reject(self, fn, *args, **kwargs):
        with self.assertRaises(ValueError):
            fn(*args, **kwargs)

    def test_nonroot_ids_without_prefix_rejected_before_socket(self):
        c = self.client
        bad = [
            "a.txt",       # no prefix at all
            "docs/x",      # no prefix
            "p",           # prefix is exactly "p/"; bare "p" is not enough
            "p/",          # empty suffix
            "p//x",        # empty segment after prefix
            "p/../x",      # dotdot segment
            "p/./x",       # dot segment
            "/abs",        # absolute
            "p/a\\b",      # backslash
            "p/a\x00b",    # NUL
        ]
        for fid in bad:
            self._reject(c.read, "src", fid)
            self._reject(c.save, "src", fid, b"x")
            self._reject(c.list, "src", fid)
            self._reject(c.find, "src", fid, name_contains="x")

    def test_root_still_rejected_for_read_and_save(self):
        self._reject(self.client.read, "src", "root")
        self._reject(self.client.save, "src", "root", b"x")

    def test_canonical_ids_accepted_and_sent_unchanged(self):
        good = [
            "p/a.txt",
            "p/docs/x",
            "p/a b.txt",       # encoded space in suffix
            "p/a+b.txt",       # plus in suffix
            "p/sub/dir/f.txt",
        ]
        for fid in good:
            client = _client(1)
            resp = _FakeResponse(
                200, [("Content-Type", "application/json")], b'{"ok":true,"result":{}}'
            )
            conn = _FakeConnection(resp)
            with _Seam(client, [conn]) as seam:
                client.list("src", fid)
            self.assertEqual(seam.calls, 1)
            method, url, body, headers = conn.requests[0]
            self.assertEqual(json.loads(body.decode("utf-8"))["file_id"], fid)

    def test_canonical_read_and_save_ids_sent_unchanged(self):
        # read: prefix preserved verbatim in the JSON control body
        client = _client(1)
        resp = _FakeResponse(
            200,
            [
                ("Content-Type", "application/octet-stream"),
                ("X-Drive-Offset", "0"),
                ("X-Drive-Observed-Size", "2"),
                ("X-Drive-Observed-Mtime-Ns", "1"),
                ("X-Drive-EOF-Observed", "true"),
            ],
            b"ab",
        )
        conn = _FakeConnection(resp)
        with _Seam(client, [conn]):
            client.read("src", "p/a b.txt", offset=0, length=4)
        self.assertEqual(
            json.loads(conn.requests[0][2].decode("utf-8"))["file_id"], "p/a b.txt"
        )

        # save: prefix preserved in the urlencoded query
        client = _client(1)
        resp = _FakeResponse(
            200, [("Content-Type", "application/json")], b'{"ok":true,"result":{}}'
        )
        conn = _FakeConnection(resp)
        with _Seam(client, [conn]):
            client.save("src", "p/a b+c.txt", b"x")
        query = parse_qs(urlsplit(conn.requests[0][1]).query)
        self.assertEqual(query["file_id"], ["p/a b+c.txt"])

    def test_root_accepted_for_list_and_find(self):
        for op in ("list", "find"):
            client = _client(1)
            resp = _FakeResponse(
                200, [("Content-Type", "application/json")], b'{"ok":true,"result":{}}'
            )
            conn = _FakeConnection(resp)
            with _Seam(client, [conn]):
                if op == "list":
                    client.list("src", "root")
                else:
                    client.find("src", "root", name_contains="x")
            self.assertEqual(len(conn.requests), 1)
            self.assertEqual(
                json.loads(conn.requests[0][2].decode("utf-8"))["file_id"], "root"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
