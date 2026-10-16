"""D067 conformance & security tests for the bounded loopback HTTP adapter.
Scope (D067): the ONLY deliverable is ``tests/test_http_adapter.py``. It tests
the frozen public API of ``drive_tool.http_adapter.DriveHTTPAdapter`` against
the accepted loopback client (``drive_tool.service_client.DriveClient``), the
accepted backend (``drive_tool.file_service.LocalFileService``) and synthetic
POSIX roots, using raw loopback sockets for framing cases.
The production adapter (``drive_tool/http_adapter.py``) is assigned separately
(D068). When it is absent this module imports cleanly, adds no failures to the
committed suite, and reports every adapter-dependent test as SKIPPED -- the
expected D067 dependency state. Adapter integration results are honestly
BLOCKED until the production file is supplied; no passing adapter run is
fabricated here.
Only the PUBLIC adapter surface is used (constructor, ``host``, ``port``,
``socket_timeout``, ``service``, ``close``, ``repr``) and the documented
``__all__`` constants. Ordering is proven with a real-service instrumented seam
(``InstrumentedService(LocalFileService)``) that alters no accepted behaviour.
Every wait is finite and every adapter/service is closed in teardown so a
blocked non-daemon worker cannot wedge the suite. The fixed 600-call smoke and
10,000-call six-agent soak helpers carry the D064 thresholds and FAIL CLOSED
(raise) without the production adapter.
No filesystem I/O outside the module's own temporary synthetic roots, no
non-loopback socket, no install, no real root or credential.
"""
from __future__ import annotations
import hashlib
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from drive_tool.file_service import LocalFileService, ServiceError
from drive_tool.service_client import DriveClient, DriveClientError, ReadReply
from drive_tool.service_policy import AgentPolicy, SourceGrant
from drive_tool.storage import ROOT_ID
_SKIP_REASON = ("production drive_tool.http_adapter (D068) is absent; D067 "
                "adapter integration is BLOCKED pending the production module")
try:
    from drive_tool.http_adapter import (  # type: ignore
        ACCEPT_POLL_INTERVAL_SECONDS, BUSY_POST_DRAIN_BUDGET_SECONDS,
        BUSY_PRE_READ_BUDGET_SECONDS, BUSY_REJECT_BUDGET_SECONDS,
        BUSY_WRITE_RESERVE_SECONDS, DEFAULT_DRAIN_TIMEOUT_SECONDS,
        DEFAULT_SOCKET_TIMEOUT_SECONDS,
        DriveHTTPAdapter, HOST, LISTEN_BACKLOG, MAX_BUSY_READ_BYTES,
        MAX_DRAIN_BYTES, MAX_HEADER_BLOCK_BYTES, MAX_HEADER_COUNT,
        MAX_IN_FLIGHT_OPERATIONS, MAX_JSON_BODY_BYTES, MAX_READ_BODY_BYTES,
        MAX_REQUEST_LINE_BYTES, MAX_SAVE_BODY_BYTES,
        POST_RESPONSE_DRAIN_SECONDS)
    _ADAPTER_AVAILABLE = True
    _ADAPTER_IMPORT_ERROR = None
except Exception as _exc:  # noqa: BLE001
    DriveHTTPAdapter = None  # type: ignore
    _ADAPTER_AVAILABLE = False
    _ADAPTER_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
    HOST = "127.0.0.1"
    LISTEN_BACKLOG = 16
    MAX_IN_FLIGHT_OPERATIONS = 6
    MAX_REQUEST_LINE_BYTES = 8192
    MAX_HEADER_BLOCK_BYTES = 16384
    MAX_HEADER_COUNT = 64
    MAX_JSON_BODY_BYTES = 65536
    MAX_SAVE_BODY_BYTES = 1048576
    MAX_READ_BODY_BYTES = 1048576
    DEFAULT_SOCKET_TIMEOUT_SECONDS = 5.0
    DEFAULT_DRAIN_TIMEOUT_SECONDS = 5.0
    ACCEPT_POLL_INTERVAL_SECONDS = 0.05
    BUSY_REJECT_BUDGET_SECONDS = 0.050
    BUSY_PRE_READ_BUDGET_SECONDS = 0.010
    BUSY_WRITE_RESERVE_SECONDS = 0.020
    BUSY_POST_DRAIN_BUDGET_SECONDS = 0.020
    MAX_BUSY_READ_BYTES = 16384
    POST_RESPONSE_DRAIN_SECONDS = 0.05
    MAX_DRAIN_BYTES = 65536
_CONTRACT_CONSTANTS = {
    "HOST": "127.0.0.1", "LISTEN_BACKLOG": 16, "MAX_IN_FLIGHT_OPERATIONS": 6,
    "MAX_REQUEST_LINE_BYTES": 8192, "MAX_HEADER_BLOCK_BYTES": 16384,
    "MAX_HEADER_COUNT": 64, "MAX_JSON_BODY_BYTES": 65536,
    "MAX_SAVE_BODY_BYTES": 1048576, "MAX_READ_BODY_BYTES": 1048576,
    "DEFAULT_SOCKET_TIMEOUT_SECONDS": 5.0, "DEFAULT_DRAIN_TIMEOUT_SECONDS": 5.0,
    "ACCEPT_POLL_INTERVAL_SECONDS": 0.05, "BUSY_REJECT_BUDGET_SECONDS": 0.050,
    "BUSY_PRE_READ_BUDGET_SECONDS": 0.010, "BUSY_WRITE_RESERVE_SECONDS": 0.020,
    "BUSY_POST_DRAIN_BUDGET_SECONDS": 0.020,
    "MAX_BUSY_READ_BYTES": 16384, "POST_RESPONSE_DRAIN_SECONDS": 0.05,
    "MAX_DRAIN_BYTES": 65536,
}
SMOKE_CALLS = 600
SOAK_CALLS = 10000
THRESHOLDS = {
    "max_workspace_bytes": 128 * 1024 * 1024,
    "max_concurrent_operations": 6,
    "expected_adapter_threads": 7,
    "fd_baseline_slack": 2,
    "rss_growth_bytes_after_warmup": 64 * 1024 * 1024,
    "max_stage_seconds": 15 * 60,
    "no_temp_accumulation": True,
    "no_cross_root_bleed": True,
    "no_unrecognized_errors": True,
}
_MAIN_TOKEN = "d067-main-" + "A" * 40
_OTHER_TOKEN = "d067-other-" + "B" * 40
_SMALL_TOKEN = "d067-small-" + "C" * 40
_READONLY_TOKEN = "d067-ro-" + "D" * 40
_ALIAS_A_TOKEN = "d067-aliasa-" + "E" * 40
_ALIAS_B_TOKEN = "d067-aliasb-" + "F" * 40
_UNKNOWN_TOKEN = "d067-unknown-" + "Z" * 40
def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
def _build_root(base, name):
    root = os.path.join(base, name)
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    os.makedirs(os.path.join(root, "savepool"), exist_ok=True)
    _write(os.path.join(root, "readme.txt"), b"d067 fixture readme\n" * 4)
    _write(os.path.join(root, "data.bin"), bytes(range(256)) * 16)   # 4096 B
    _write(os.path.join(root, "short.bin"), b"abcde")                 # 5 B
    _write(os.path.join(root, "empty.bin"), b"")                      # 0 B
    _write(os.path.join(root, "big.bin"), b"B" * 262144)              # 256 KiB
    _write(os.path.join(root, "sub", "note.txt"), b"nested note\n")
    return os.path.realpath(root)
def _local_supported():
    try:
        from drive_tool.local import LocalAdapter
        with tempfile.TemporaryDirectory() as td:
            LocalAdapter("probe", td).close()
        return True
    except Exception:  # noqa: BLE001
        return False
_LOCAL_SUPPORTED = _local_supported()
def _grant(alias, root, ops, *, max_read=1048576, max_write=1048576):
    return SourceGrant(alias=alias, root=root, operations=frozenset(ops),
                       max_read_bytes=max_read, max_write_bytes=max_write)
def _policy(agent_id, token, grants):
    return AgentPolicy(agent_id=agent_id,
                       token_sha256=hashlib.sha256(token.encode("ascii")).digest(),
                       grants=tuple(grants))
def _standard_policies(base):
    r = {n: _build_root(base, "r_" + n) for n in
         ("main", "other", "small", "ro", "alias_a", "alias_b")}
    all_ops = {"find", "list", "read", "save"}
    read_ops = {"find", "list", "read"}
    policies = (
        _policy("agent-main", _MAIN_TOKEN, [_grant("main", r["main"], all_ops)]),
        _policy("agent-other", _OTHER_TOKEN, [_grant("other", r["other"], all_ops)]),
        _policy("agent-small", _SMALL_TOKEN,
                [_grant("small", r["small"], all_ops, max_read=1024, max_write=1024)]),
        _policy("agent-ro", _READONLY_TOKEN, [_grant("ro", r["ro"], read_ops)]),
        _policy("agent-alias-a", _ALIAS_A_TOKEN,
                [_grant("shared", r["alias_a"], all_ops)]),
        _policy("agent-alias-b", _ALIAS_B_TOKEN,
                [_grant("shared", r["alias_b"], all_ops)]),
    )
    return policies, r
def _frame(target, headers, body=b"", *, method="POST", version="HTTP/1.1"):
    start = ("%s %s %s" % (method, target, version)).encode("latin-1")
    return start + b"\r\n" + b"".join(h + b"\r\n" for h in headers) + b"\r\n" + body
def _jb(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")
def _hdrs(token, *, ct=b"application/json", cl=None, body=b"",
          host=b"127.0.0.1", conn=b"close"):
    h = [b"Host: " + host]
    if token is not None:
        h.append(b"Authorization: Bearer " + token.encode("ascii"))
    h.append(b"Content-Type: " + ct)
    h.append(b"Content-Length: " + str(len(body) if cl is None else cl).encode())
    if conn is not None:
        h.append(b"Connection: " + conn)
    return h
def _json_req(target, payload, token, *, ct=b"application/json"):
    body = _jb(payload)
    return _frame(target, _hdrs(token, ct=ct, body=body), body)
def _save_req(query, body, token):
    return _frame("/v1/save?" + query,
                  _hdrs(token, ct=b"application/octet-stream", body=body), body)
_LIST_PAYLOAD = {"source_id": "main", "file_id": "root", "limit": 5, "offset": 0}
_READ_PAYLOAD = {"source_id": "main", "file_id": "p/data.bin", "offset": 0, "length": 16}
_SAVE_Q = "source_id=main&file_id=p%2Fsavepool%2Fa.bin&overwrite=true&create_parents=true"
class _Raw:
    def __init__(self, port, timeout=3.0):
        self.sock = socket.create_connection((HOST, port), timeout=timeout)
        self._t = timeout
    def send(self, data):
        self.sock.sendall(data)
    def read_all(self, deadline=None):
        if deadline is None:
            deadline = time.monotonic() + self._t
        chunks = []
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.0, deadline - time.monotonic()))
            try:
                part = self.sock.recv(65536)
            except (socket.timeout, TimeoutError, OSError):
                break
            if not part:
                break
            chunks.append(part)
        return b"".join(chunks)
    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
def _exchange(port, raw, *, timeout=3.0):
    c = _Raw(port, timeout=timeout)
    try:
        c.send(raw)
        return c.read_all()
    finally:
        c.close()
def _parse(raw):
    if not raw:
        return None, {}, b""
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        return None, {}, raw
    lines = head.split(b"\r\n")
    try:
        status = int(lines[0].split(b" ")[1])
    except (IndexError, ValueError):
        return None, {}, body
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(b":")
        headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
    return status, headers, body
def _err(raw):
    status, _h, body = _parse(raw)
    obj = json.loads(body.decode("utf-8"))
    assert obj.get("ok") is False, obj
    e = obj["error"]
    return status, e["code"], e["outcome_uncertain"], e["retry_safe"]
def _nfd():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return None
def _rss_kb():
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None
def _tree(root):
    out = []
    for dp, dns, fns in os.walk(root):
        for n in dns + fns:
            out.append(os.path.relpath(os.path.join(dp, n), root))
    return sorted(out)
class InstrumentedService(LocalFileService):
    """The REAL LocalFileService with call/lease instrumentation only.
    Every method delegates straight to ``super()``; nothing of the accepted
    backend is altered. Used to observe which public method ran, with which
    ``file_id``, and whether a binding lease (after which filesystem work is
    possible) was ever acquired. A real-service seam, not an adapter mock.
    """
    def __init__(self, policies):
        super().__init__(policies)
        self.calls = []
        self.leases = 0
        self.list_override = None
        self.read_override = None
        self._lock = threading.Lock()
        self.block = False
        self.block_target = 0
        self.block_timeout = 10.0
        self.entered = threading.Event()
        self.release = threading.Event()
        self._entered = 0
    def _pre(self, name, file_id):
        with self._lock:
            self.calls.append((name, file_id))
        if self.block:
            with self._lock:
                self._entered += 1
                if self._entered >= self.block_target:
                    self.entered.set()
            self.release.wait(timeout=self.block_timeout)
    def _acquire_binding_lease(self, agent_id, alias):
        self.leases += 1
        return super()._acquire_binding_lease(agent_id, alias)
    def release_gate(self):
        self.block = False
        self.release.set()
    def list(self, token, source_id, file_id=ROOT_ID, *, limit=20, offset=0,
             name_contains=None):
        self._pre("list", file_id)
        if self.list_override is not None:
            return self.list_override
        return super().list(token, source_id, file_id, limit=limit, offset=offset,
                            name_contains=name_contains)
    def find(self, token, source_id, file_id=ROOT_ID, *, name_contains, limit=20,
             max_entries=2000, max_depth=4):
        self._pre("find", file_id)
        return super().find(token, source_id, file_id, name_contains=name_contains,
                            limit=limit, max_entries=max_entries, max_depth=max_depth)
    def read(self, token, source_id, file_id, *, offset=0, length=262144):
        self._pre("read", file_id)
        if self.read_override is not None:
            return self.read_override
        return super().read(token, source_id, file_id, offset=offset, length=length)
    def save(self, token, source_id, file_id, data, *, overwrite=False,
             create_parents=True):
        self._pre("save", file_id)
        return super().save(token, source_id, file_id, data, overwrite=overwrite,
                            create_parents=create_parents)
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
@unittest.skipUnless(_LOCAL_SUPPORTED, "LocalAdapter unsupported on this platform")
class _Base(unittest.TestCase):
    socket_timeout = 5.0
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="d067-http-")
        self.addCleanup(self._rm)
        self.policies, self.roots = _standard_policies(self._tmp)
        self._adapters = []
    def _rm(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
    def make(self, *, policies=None, service=None, port=0, socket_timeout=None):
        st = self.socket_timeout if socket_timeout is None else socket_timeout
        if service is not None:
            a = DriveHTTPAdapter(service=service, port=port, socket_timeout=st)
        else:
            a = DriveHTTPAdapter(policies or self.policies, port=port,
                                 socket_timeout=st)
        self._adapters.append(a)
        self.addCleanup(self._close, a)
        return a
    def instrumented(self, policies=None):
        s = InstrumentedService(policies or self.policies)
        self.addCleanup(s.release_gate)
        return s
    @staticmethod
    def _close(a):
        try:
            if isinstance(a.service, InstrumentedService):
                a.service.release_gate()
        except Exception:  # noqa: BLE001
            pass
        try:
            a.close(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
    def client(self, a, token, *, timeout=None):
        return DriveClient(port=a.port, token=token,
                           timeout=timeout or self.socket_timeout)
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class ContractConstantsTests(unittest.TestCase):
    def test_constants_match_contract(self):
        import drive_tool.http_adapter as m
        for n, v in _CONTRACT_CONSTANTS.items():
            self.assertTrue(hasattr(m, n), "missing %s" % n)
            self.assertEqual(getattr(m, n), v, "drift in %s" % n)
    def test_all_exports_present(self):
        import drive_tool.http_adapter as m
        expected = set(_CONTRACT_CONSTANTS) | {"DriveHTTPAdapter"}
        self.assertTrue(expected.issubset(set(m.__all__)), m.__all__)
    def test_adapter_does_not_reference_forbidden_backends(self):
        import drive_tool.http_adapter as m
        src = open(m.__file__, encoding="utf-8").read()
        for bad in ("LocalAdapter", "LocalWriter", "ListingService", "PolicyTable"):
            self.assertNotIn(bad, src)
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class ConstructorValidationTests(_Base):
    def test_requires_exactly_one_of_policies_or_service(self):
        with self.assertRaises(ValueError):
            DriveHTTPAdapter()
        with self.assertRaises(ValueError):
            DriveHTTPAdapter(self.policies, service=InstrumentedService(self.policies))
    def test_service_must_be_localfileservice(self):
        with self.assertRaises(TypeError):
            DriveHTTPAdapter(service=object())
    def test_port_validation(self):
        for bad, exc in ((True, TypeError), ("80", TypeError),
                         (-1, ValueError), (70000, ValueError)):
            with self.assertRaises(exc):
                DriveHTTPAdapter(self.policies, port=bad)
    def test_socket_timeout_validation(self):
        for bad, exc in ((True, TypeError), ("5", TypeError), (0.0, ValueError),
                         (-1.0, ValueError), (float("nan"), ValueError),
                         (float("inf"), ValueError)):
            with self.assertRaises(exc):
                DriveHTTPAdapter(self.policies, socket_timeout=bad)
    def test_properties(self):
        a = self.make()
        self.assertEqual(a.host, "127.0.0.1")
        self.assertTrue(1 <= a.port <= 65535)
        self.assertEqual(a.socket_timeout, self.socket_timeout)
        self.assertIsInstance(a.service, LocalFileService)
    def test_bind_failure_rolls_back_without_thread_leak(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        try:
            before = threading.active_count()
            with self.assertRaises(OSError):
                DriveHTTPAdapter(self.policies, port=blocker.getsockname()[1],
                                 socket_timeout=1.0)
            deadline = time.monotonic() + 2.0
            while threading.active_count() > before and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertLessEqual(threading.active_count(), before)
        finally:
            blocker.close()
    def test_injected_service_is_owned_and_closed(self):
        s = self.instrumented()
        a = self.make(service=s)
        self.assertTrue(a.close(timeout=5.0))
        with self.assertRaises(ServiceError) as cm:
            s.list(_MAIN_TOKEN, "main", ROOT_ID)
        self.assertEqual(cm.exception.code, "unavailable")
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class EndToEndTests(_Base):
    def setUp(self):
        super().setUp()
        self.a = self.make()
        self.c = self.client(self.a, _MAIN_TOKEN)
    def test_list_and_find(self):
        names = {i["name"] for i in self.c.list("main", limit=50)["items"]}
        self.assertTrue({"readme.txt", "data.bin", "sub"} <= names)
        found = {i["name"] for i in self.c.find("main", name_contains="data",
                                                limit=10)["items"]}
        self.assertIn("data.bin", found)
    def test_list_offset_beyond_entries_is_invalid(self):
        with self.assertRaises(DriveClientError) as cm:
            self.c.list("main", limit=5, offset=100000)
        self.assertEqual(cm.exception.code, "invalid")
    def test_find_whitespace_only_is_invalid(self):
        with self.assertRaises(DriveClientError) as cm:
            self.c.find("main", name_contains="   ", limit=10)
        self.assertEqual(cm.exception.code, "invalid")
    def test_save_then_read_roundtrip(self):
        payload = b"round-trip-payload-0123456789"
        res = self.c.save("main", "p/savepool/rt.bin", payload, overwrite=True)
        self.assertEqual(res["bytes_written"], len(payload))
        reply = self.c.read("main", "p/savepool/rt.bin", length=64)
        self.assertEqual(reply.data, payload)
        self.assertTrue(reply.eof_observed)
    def test_read_empty_file(self):
        r = self.c.read("main", "p/empty.bin", offset=0, length=16)
        self.assertIsInstance(r, ReadReply)
        self.assertEqual(r.data, b"")
        self.assertTrue(r.eof_observed)
    def test_read_short_window_sets_eof(self):
        r = self.c.read("main", "p/short.bin", offset=0, length=100)
        self.assertEqual(r.data, b"abcde")
        self.assertTrue(r.eof_observed)
    def test_read_exact_fill_not_eof(self):
        r = self.c.read("main", "p/data.bin", offset=0, length=4096)
        self.assertEqual(len(r.data), 4096)
        self.assertFalse(r.eof_observed)
    def test_read_past_eof(self):
        r = self.c.read("main", "p/data.bin", offset=10 ** 9, length=64)
        self.assertEqual(r.data, b"")
        self.assertTrue(r.eof_observed)
    def test_explicit_length_within_small_grant_cap(self):
        r = self.client(self.a, _SMALL_TOKEN).read("small", "p/data.bin",
                                                   offset=0, length=1024)
        self.assertEqual(len(r.data), 1024)
        self.assertFalse(r.eof_observed)
    def test_default_read_on_small_grant_is_too_large(self):
        with self.assertRaises(DriveClientError) as cm:
            self.client(self.a, _SMALL_TOKEN).read("small", "p/data.bin")
        self.assertEqual(cm.exception.code, "too_large")
        self.assertFalse(cm.exception.outcome_uncertain)
        self.assertFalse(cm.exception.retry_safe)
def _framing_cases():
    body = _jb(_LIST_PAYLOAD)
    std = _hdrs(_MAIN_TOKEN, body=body)
    rbody = _jb(_READ_PAYLOAD)
    rstd = _hdrs(_MAIN_TOKEN, body=rbody)
    cases = {}
    def add(name, raw):
        cases[name] = raw
    add("non_post", _frame("/v1/list", std, body, method="GET"))
    add("bad_version", _frame("/v1/list", std, body, version="HTTP/1.0"))
    add("request_line_too_long",
        _frame("/v1/list?" + "a" * (MAX_REQUEST_LINE_BYTES + 32),
               _hdrs(_MAIN_TOKEN, body=b""), b""))
    add("header_block_too_long",
        _frame("/v1/list", std + [b"X-Pad: " + b"p" * (MAX_HEADER_BLOCK_BYTES + 64)],
               body))
    add("too_many_headers",
        _frame("/v1/list", std + [b"X-N-%d: v" % i for i in range(MAX_HEADER_COUNT + 4)],
               body))
    add("bare_lf", _frame("/v1/list", std, body).replace(b"\r\n", b"\n", 1))
    add("obs_fold", _frame("/v1/list", std + [b" X-Folded: v"], body))
    add("ws_before_colon",
        _frame("/v1/list", [b"Host : 127.0.0.1"] + std[1:], body))
    add("missing_host",
        _frame("/v1/list", [b"Authorization: Bearer " + _MAIN_TOKEN.encode(),
                            b"Content-Type: application/json",
                            b"Content-Length: " + str(len(body)).encode(),
                            b"Connection: close"], body))
    add("duplicate_host", _frame("/v1/list", std + [b"Host: 127.0.0.1"], body))
    add("conn_keep_alive",
        _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body, conn=b"keep-alive"), body))
    add("duplicate_connection", _frame("/v1/list", std + [b"Connection: close"], body))
    add("transfer_encoding",
        _frame("/v1/list", std + [b"Transfer-Encoding: chunked"], body))
    add("expect_continue", _frame("/v1/list", std + [b"Expect: 100-continue"], body))
    add("missing_content_length",
        _frame("/v1/list", [b"Host: 127.0.0.1",
                            b"Authorization: Bearer " + _MAIN_TOKEN.encode(),
                            b"Content-Type: application/json",
                            b"Connection: close"], body))
    add("duplicate_content_length",
        _frame("/v1/list", std + [b"Content-Length: " + str(len(body)).encode()], body))
    add("non_digit_content_length",
        _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body, cl="+5"), body))
    add("huge_digit_content_length",
        _frame("/v1/list",
               [h for h in std if not h.startswith(b"Content-Length")] +
               [b"Content-Length: " + b"9" * 5000], body))
    add("missing_content_type",
        _frame("/v1/list", [b"Host: 127.0.0.1",
                            b"Authorization: Bearer " + _MAIN_TOKEN.encode(),
                            b"Content-Length: " + str(len(body)).encode(),
                            b"Connection: close"], body))
    add("duplicate_content_type",
        _frame("/v1/list", std + [b"Content-Type: application/json"], body))
    add("parameterized_content_type",
        _frame("/v1/list",
               _hdrs(_MAIN_TOKEN, ct=b"application/json; charset=utf-8", body=body),
               body))
    add("wrong_ct_for_read",
        _frame("/v1/read", _hdrs(_MAIN_TOKEN, ct=b"application/octet-stream",
                                 body=rbody), rbody))
    add("query_on_list", _frame("/v1/list?x=1", std, body))
    add("query_on_read", _frame("/v1/read?a=1", rstd, rbody))
    add("save_no_query",
        _frame("/v1/save", _hdrs(_MAIN_TOKEN, ct=b"application/octet-stream"), b""))
    add("save_empty_query",
        _frame("/v1/save?", _hdrs(_MAIN_TOKEN, ct=b"application/octet-stream"), b""))
    add("save_missing_key",
        _save_req("source_id=main&file_id=p%2Fsavepool%2Fa.bin&overwrite=false",
                  b"payload", _MAIN_TOKEN))
    add("save_extra_key",
        _save_req("source_id=main&file_id=p%2Fsavepool%2Fa.bin&overwrite=false"
                  "&create_parents=true&token=abc", b"payload", _MAIN_TOKEN))
    add("save_duplicate_key",
        _save_req("source_id=main&source_id=main&file_id=p%2Fsavepool%2Fa.bin"
                  "&overwrite=false&create_parents=true", b"payload", _MAIN_TOKEN))
    add("buffered_trailing_bytes", _frame("/v1/list", std, body) + b"X")
    return cases
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class FramingRejectionTests(_Base):
    # Adopted contract: a malformed frame is 400 invalid, except (a) a huge but
    # syntactically valid Content-Length, which reaches the Step 8 wire cap and
    # is 413 too_large, and (b) a duplicate save source_id, whose source is
    # ambiguous and therefore denied 403.  Everything else stays 400 invalid.
    _CONTRACT_STATUS = {
        "huge_digit_content_length": (413, "too_large"),
        "save_duplicate_key": (403, "denied"),
    }

    def test_framing_cases_rejected_per_contract(self):
        a = self.make()
        for name, raw in _framing_cases().items():
            with self.subTest(case=name):
                expected = self._CONTRACT_STATUS.get(name, (400, "invalid"))
                status, code, unc, retry = _err(_exchange(a.port, raw))
                self.assertEqual(status, expected[0], name)
                self.assertEqual(code, expected[1], name)
                self.assertFalse(unc, name)
                self.assertFalse(retry, name)
    def test_second_request_is_not_executed(self):
        a = self.make()
        first = _json_req("/v1/list", _LIST_PAYLOAD, _MAIN_TOKEN)
        second = _save_req("source_id=main&file_id=p%2Fsavepool%2Fnever.bin"
                           "&overwrite=true&create_parents=true",
                           b"SHOULD-NOT-BE-WRITTEN", _MAIN_TOKEN)
        raw = _exchange(a.port, first + second)
        self.assertEqual(raw.count(b"HTTP/1.1"), 1, raw[:200])
        self.assertFalse(os.path.exists(
            os.path.join(self.roots["main"], "savepool", "never.bin")))
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class AuthFirstZeroFsTests(_Base):
    def setUp(self):
        super().setUp()
        self.s = self.instrumented()
        self.a = self.make(service=self.s)
    def _send(self, raw):
        return _err(_exchange(self.a.port, raw))
    def test_missing_authorization_denied_before_body(self):
        body = _jb(_LIST_PAYLOAD)
        raw = _frame("/v1/list", [b"Host: 127.0.0.1",
                                  b"Content-Type: application/json",
                                  b"Content-Length: " + str(len(body)).encode(),
                                  b"Connection: close"], body)
        self.assertEqual(self._send(raw)[:2], (403, "denied"))
        self.assertEqual(self.s.calls, [])
    def test_bad_authorization_variants_denied(self):
        body = _jb(_LIST_PAYLOAD)
        for auth in (b"Authorization: Basic " + _MAIN_TOKEN.encode(),
                     b"Authorization: Bearer short"):
            with self.subTest(auth=auth[:16]):
                raw = _frame("/v1/list",
                             _hdrs(None, body=body) + [auth], body)
                self.assertEqual(self._send(raw)[:2], (403, "denied"))
        dup = _frame("/v1/list",
                     _hdrs(_MAIN_TOKEN, body=body) +
                     [b"Authorization: Bearer " + _MAIN_TOKEN.encode()], body)
        self.assertEqual(self._send(dup)[:2], (403, "denied"))
    def test_unknown_token_malformed_body_is_denied_not_invalid(self):
        for body in (b"{not-valid-json",
                     b'{"source_id":"main","source_id":"other","file_id":"root",'
                     b'"limit":5,"offset":0}'):
            with self.subTest(body=body[:16]):
                raw = _frame("/v1/list", _hdrs(_UNKNOWN_TOKEN, body=body), body)
                status, code, unc, retry = self._send(raw)
                self.assertEqual((status, code), (403, "denied"))
                self.assertFalse(unc)
                self.assertFalse(retry)
        self.assertEqual(self.s.leases, 0)
    def test_valid_token_unauthorized_source_bad_body_is_denied(self):
        body = b"{bad"
        raw = _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body)
        self.assertEqual(self._send(raw)[:2], (403, "denied"))
        self.assertEqual(self.s.leases, 0)
    def test_unextractable_source_denied_uniformly_across_token_states(self):
        body = b"{bad"
        raw = _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body)
        status, code, unc, retry = self._send(raw)
        self.assertEqual((status, code), (403, "denied"))
        self.assertFalse(unc)
        self.assertFalse(retry)
        self.assertEqual(self.s.calls, [("list", None)])
        self.assertEqual(self.s.leases, 0)
        # Paired: an unknown token and an unauthorized known token receive the
        # same uniform denial, and no binding lease is ever acquired.
        for token in (_UNKNOWN_TOKEN, _OTHER_TOKEN):
            with self.subTest(token=token[:12]):
                raw = _frame("/v1/list", _hdrs(token, body=body), body)
                self.assertEqual(self._send(raw)[:2], (403, "denied"))
        self.assertEqual(self.s.leases, 0)
    def test_missing_key_delegates_file_id_none(self):
        body = _jb({"source_id": "main", "file_id": "root", "limit": 5})
        raw = _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body)
        self.assertEqual(self._send(raw)[:2], (400, "invalid"))
        self.assertEqual(self.s.calls, [("list", None)])
        self.assertEqual(self.s.leases, 0)
    def test_invalid_save_query_delegates_file_id_none_no_mutation(self):
        raw = _save_req("source_id=main&file_id=p%2Fx&overwrite=maybe"
                        "&create_parents=true", b"data", _MAIN_TOKEN)
        self.assertEqual(self._send(raw)[:2], (400, "invalid"))
        self.assertEqual(self.s.calls, [("save", None)])
        self.assertEqual(self.s.leases, 0)
        self.assertFalse(os.path.exists(os.path.join(self.roots["main"], "x")))
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class CapabilityBoundaryTests(_Base):
    """Adopted capability boundary (director clarification over D083).

    Lexical vs service authentication.  Step 7 of ``_check_headers`` is a purely
    lexical Authorization *framing* check: scheme ``Bearer ``, token length
    32..256 and printable bytes.  It cannot authenticate a token, because the
    frozen backend resolves capability as ``(token, source_id, operation)`` and
    exposes no token-only method.  A lexically valid but unknown token therefore
    passes Step 7 and can still receive a pre-body 413 from the wire cap.  A
    missing, unextractable or non-string ``source_id`` reaches the service as
    ``source_id=None`` and is uniformly 403 denied for known, unknown and
    unauthorized tokens alike.  An extractable, *authorized* source with bad
    remaining schema is 400 invalid; an extractable but *unauthorized* source is
    403 denied.
    """

    def test_unextractable_source_denied_for_every_token_state(self):
        a = self.make()
        body = b"{bad"
        for token in (_MAIN_TOKEN, _UNKNOWN_TOKEN, _OTHER_TOKEN, _READONLY_TOKEN):
            with self.subTest(token=token[:12]):
                raw = _frame("/v1/list", _hdrs(token, body=body), body)
                status, code, unc, retry = _err(_exchange(a.port, raw))
                self.assertEqual((status, code), (403, "denied"))
                self.assertFalse(unc)
                self.assertFalse(retry)

    def test_extractable_authorized_source_bad_schema_is_invalid(self):
        a = self.make()
        body = _jb({"source_id": "main", "file_id": "root", "limit": 5})
        raw = _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body)
        status, code, unc, retry = _err(_exchange(a.port, raw))
        self.assertEqual((status, code), (400, "invalid"))
        self.assertFalse(unc)
        self.assertFalse(retry)

    def test_extractable_unauthorized_source_is_denied(self):
        a = self.make()
        body = _jb({"source_id": "other", "file_id": "root", "limit": 5})
        raw = _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body)
        self.assertEqual(_err(_exchange(a.port, raw))[:2], (403, "denied"))

    def test_duplicate_json_key_ambiguous_source_is_denied(self):
        a = self.make()
        body = (b'{"source_id":"main","source_id":"main","file_id":"root",'
                b'"limit":5,"offset":0}')
        raw = _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body)
        self.assertEqual(_err(_exchange(a.port, raw))[:2], (403, "denied"))

    def test_leading_zero_content_length_accepted_and_normalised(self):
        a = self.make()
        body = _jb(_LIST_PAYLOAD)
        raw = _frame("/v1/list",
                     _hdrs(_MAIN_TOKEN, body=body,
                           cl="00000000" + str(len(body))), body)
        # Zero-padded ASCII decimal is accepted within the header bounds and
        # normalised, so the request proceeds normally (200 OK) rather than
        # being classified 413 too_large.
        status, _headers, body_out = _parse(_exchange(a.port, raw))
        self.assertEqual(status, 200, body_out[:120])
        self.assertIn(b'"ok":true', body_out)

    def test_huge_content_length_is_413_only_after_the_lexical_auth_gate(self):
        a = self.make()
        body = _jb(_LIST_PAYLOAD)
        base = [b"Host: 127.0.0.1", b"Content-Type: application/json"]
        huge = [b"Content-Length: " + b"9" * 5000]
        bearer = lambda t: [b"Authorization: Bearer " + t.encode()]  # noqa: E731

        # Known token: lexical gate passes, the Step 8 wire cap applies.
        raw = _frame("/v1/list", base + bearer(_MAIN_TOKEN) + huge, body)
        status, code, unc, retry = _err(_exchange(a.port, raw))
        self.assertEqual((status, code), (413, "too_large"))
        self.assertFalse(unc)
        self.assertFalse(retry)

        # Lexically valid but UNKNOWN token: it cannot be authenticated without
        # a source_id and body, so it still passes the lexical gate and the wire
        # cap applies (413, not 403).
        raw = _frame("/v1/list", base + bearer(_UNKNOWN_TOKEN) + huge, body)
        self.assertEqual(_err(_exchange(a.port, raw))[:2], (413, "too_large"))

        # Lexically INVALID token: the lexical gate rejects before the cap.
        raw = _frame("/v1/list", base + [b"Authorization: Bearer short"] + huge, body)
        self.assertEqual(_err(_exchange(a.port, raw))[:2], (403, "denied"))

        # No Authorization at all: same, rejected before the cap.
        raw = _frame("/v1/list", base + huge, body)
        self.assertEqual(_err(_exchange(a.port, raw))[:2], (403, "denied"))


@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class AliasIsolationTests(_Base):
    def test_same_alias_distinct_roots_isolated(self):
        a = self.make()
        _write(os.path.join(self.roots["alias_a"], "only-in-a.txt"), b"a")
        _write(os.path.join(self.roots["alias_b"], "only-in-b.txt"), b"b")
        na = {i["name"] for i in self.client(a, _ALIAS_A_TOKEN).list("shared", limit=100)["items"]}
        nb = {i["name"] for i in self.client(a, _ALIAS_B_TOKEN).list("shared", limit=100)["items"]}
        self.assertIn("only-in-a.txt", na)
        self.assertNotIn("only-in-b.txt", na)
        self.assertIn("only-in-b.txt", nb)
        self.assertNotIn("only-in-a.txt", nb)
    def test_cross_agent_alias_denied(self):
        a = self.make()
        with self.assertRaises(DriveClientError) as cm:
            self.client(a, _ALIAS_A_TOKEN).list("other")
        self.assertEqual(cm.exception.code, "denied")
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class ResponseCapTests(_Base):
    def setUp(self):
        super().setUp()
        self.s = self.instrumented()
        self.a = self.make(service=self.s)
        self.c = self.client(self.a, _MAIN_TOKEN)
    def test_oversized_json_result_too_large(self):
        self.s.list_override = {"items": [{"pad": "x" * 1024} for _ in range(200)],
                                "next_offset": None}
        with self.assertRaises(DriveClientError) as cm:
            self.c.list("main", limit=100)
        self.assertEqual(cm.exception.code, "too_large")
        self.assertFalse(cm.exception.outcome_uncertain)
        self.assertFalse(cm.exception.retry_safe)
    def test_wrong_read_result_type_unavailable(self):
        class Bad:
            data = "not-bytes"; offset = 0; observed_size = 0
            observed_mtime_ns = 0; eof_observed = True
        self.s.read_override = Bad()
        with self.assertRaises(DriveClientError) as cm:
            self.c.read("main", "p/data.bin", length=16)
        self.assertEqual(cm.exception.code, "unavailable")
        self.assertFalse(cm.exception.outcome_uncertain)
        self.assertTrue(cm.exception.retry_safe)
    def test_pre_body_json_cl_over_cap_too_large(self):
        raw = _frame("/v1/list",
                     _hdrs(_MAIN_TOKEN, cl=MAX_JSON_BODY_BYTES + 1, body=b""), b"")
        status, code, unc, retry = _err(_exchange(self.a.port, raw))
        self.assertEqual((status, code), (413, "too_large"))
        self.assertFalse(unc)
        self.assertFalse(retry)
    def test_pre_body_save_cl_over_cap_too_large(self):
        raw = _frame("/v1/save?" + _SAVE_Q,
                     _hdrs(_MAIN_TOKEN, ct=b"application/octet-stream",
                           cl=MAX_SAVE_BODY_BYTES + 1, body=b""), b"")
        status, code, unc, retry = _err(_exchange(self.a.port, raw))
        self.assertEqual((status, code), (413, "too_large"))
        self.assertFalse(unc)
        self.assertFalse(retry)
    def test_save_over_grant_write_cap_too_large_pre_operation(self):
        with self.assertRaises(DriveClientError) as cm:
            self.client(self.a, _SMALL_TOKEN).save(
                "small", "p/savepool/big.bin", b"z" * 2048, overwrite=True)
        self.assertEqual(cm.exception.code, "too_large")
        self.assertFalse(cm.exception.outcome_uncertain)
        self.assertFalse(cm.exception.retry_safe)
        self.assertEqual(self.s.leases, 0)
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class SaveUncertaintyTests(_Base):
    def test_disconnect_before_full_body_mutates_nothing(self):
        a = self.make()
        target = os.path.join(self.roots["main"], "savepool", "partial.bin")
        raw = _frame("/v1/save?source_id=main&file_id=p%2Fsavepool%2Fpartial.bin"
                     "&overwrite=true&create_parents=true",
                     _hdrs(_MAIN_TOKEN, ct=b"application/octet-stream", cl=1000),
                     b"only-a-few-bytes")
        c = _Raw(a.port)
        try:
            c.send(raw)
        finally:
            c.close()
        time.sleep(0.2)
        self.assertFalse(os.path.exists(target))
    def test_committed_save_persists_no_rollback(self):
        a = self.make()
        payload = b"committed-payload-xyz"
        self.client(a, _MAIN_TOKEN).save("main", "p/savepool/committed.bin",
                                         payload, overwrite=True)
        with open(os.path.join(self.roots["main"], "savepool", "committed.bin"),
                  "rb") as fh:
            self.assertEqual(fh.read(), payload)
    def test_post_lease_save_failure_is_uncertain(self):
        import errno
        from drive_tool import local_write
        s = self.instrumented()
        a = self.make(service=s)
        c = self.client(a, _MAIN_TOKEN)
        # overwrite=True publishes through _os_rename, not _os_link: the fault
        # must be injected on the primitive the save actually uses, and only
        # after it commits, so the failure is genuinely post-publish.
        target = os.path.join(self.roots["main"], "savepool", "fault.bin")
        self.assertFalse(os.path.exists(target))
        orig = local_write.LocalWriter._os_rename
        def boom(self, *args, **kwargs):
            orig(self, *args, **kwargs)   # real atomic rename: bytes published
            raise OSError(errno.EIO, "injected post-publish failure")
        local_write.LocalWriter._os_rename = boom
        try:
            with self.assertRaises(DriveClientError) as cm:
                c.save("main", "p/savepool/fault.bin", b"FAULT-PAYLOAD",
                       overwrite=True)
        finally:
            local_write.LocalWriter._os_rename = orig
        self.assertEqual(cm.exception.code, "unavailable")
        self.assertTrue(cm.exception.outcome_uncertain)
        self.assertFalse(cm.exception.retry_safe)
        # post-publish: the committed bytes really are on disk ...
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"FAULT-PAYLOAD")
        # ... and the failed save left no stale temp file behind.
        leftovers = [n for n in os.listdir(os.path.dirname(target))
                     if n.startswith(local_write.TEMP_PREFIX)]
        self.assertEqual(leftovers, [])
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class ReadSemanticsTests(_Base):
    def test_empty_body_with_eof_header_accepted(self):
        a = self.make()
        r = self.client(a, _MAIN_TOKEN).read("main", "p/empty.bin", offset=0, length=16)
        self.assertEqual(r.data, b"")
        self.assertTrue(r.eof_observed)
    def test_wire_length_zero_invalid(self):
        a = self.make()
        body = _jb({"source_id": "main", "file_id": "p/data.bin", "offset": 0,
                    "length": 0})
        raw = _frame("/v1/read", _hdrs(_MAIN_TOKEN, body=body), body)
        status, code, unc, retry = _err(_exchange(a.port, raw))
        self.assertEqual((status, code), (400, "invalid"))
        self.assertFalse(unc)
        self.assertFalse(retry)
def _payload_cases():
    return {
        "duplicate_keys": b'{"source_id":"main","source_id":"main","file_id":"root",'
                          b'"limit":5,"offset":0}',
        "nan": b'{"source_id":"main","file_id":"root","limit":NaN,"offset":0}',
        "infinity": b'{"source_id":"main","file_id":"root","limit":Infinity,"offset":0}',
        "bom": b"\xef\xbb\xbf" + _jb(_LIST_PAYLOAD),
        "invalid_utf8": b'{"source_id":"\xff\xfe","file_id":"root","limit":5,"offset":0}',
        "extra_key": _jb({**_LIST_PAYLOAD, "extra": 1}),
        "missing_key": _jb({"source_id": "main", "file_id": "root", "limit": 5}),
        "non_object": b"[1,2,3]",
    }
# Payload cases whose source_id cannot be extracted at all: the frozen backend
# authorizes (token, source_id, operation), so the adapter delegates with
# source_id=None and the request is uniformly 403 denied.
_UNEXTRACTABLE_SOURCE_CASES = (
    "duplicate_keys", "nan", "infinity", "bom", "invalid_utf8", "non_object",
)
# Payload cases with an extractable, authorized source_id but a bad remaining
# schema: those are 400 invalid before any binding lease.
_EXTRACTABLE_BAD_SCHEMA_CASES = ("extra_key", "missing_key")


@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class PayloadParsingTests(_Base):
    def test_payload_cases_rejected_per_contract(self):
        a = self.make()
        for name, body in _payload_cases().items():
            with self.subTest(case=name):
                if name in _UNEXTRACTABLE_SOURCE_CASES:
                    expected = (403, "denied")
                elif name in _EXTRACTABLE_BAD_SCHEMA_CASES:
                    expected = (400, "invalid")
                else:  # pragma: no cover - guard against an unclassified case
                    self.fail("unclassified payload case %r" % (name,))
                raw = _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body)
                status, code, _u, _r = _err(_exchange(a.port, raw))
                self.assertEqual((status, code), expected, name)
    def test_deeply_nested_json_does_not_kill_worker(self):
        a = self.make()
        body = b"[" * 5000 + b"]" * 5000
        raw = _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body)
        # The body never yields an extractable source_id, so it is denied 403
        # rather than parsed into an invalid request.
        status, code, _u, _r = _err(_exchange(a.port, raw))
        self.assertEqual((status, code), (403, "denied"))
        ok = self.client(a, _MAIN_TOKEN).list("main", limit=5)
        self.assertIn("items", ok)
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class AdmissionAndDeadlineTests(_Base):
    def setUp(self):
        super().setUp()
        self.s = self.instrumented()
        self.a = self.make(service=self.s)
    def _block_six(self):
        self.s.block = True
        self.s.block_target = MAX_IN_FLIGHT_OPERATIONS
        self.s.block_timeout = 10.0
        barrier = threading.Barrier(MAX_IN_FLIGHT_OPERATIONS)
        results = []
        lock = threading.Lock()
        def worker():
            c = self.client(self.a, _MAIN_TOKEN)
            barrier.wait(timeout=5.0)
            try:
                c.list("main", limit=5)
                with lock:
                    results.append("ok")
            except DriveClientError as exc:
                with lock:
                    results.append(exc.code)
        ts = [threading.Thread(target=worker) for _ in range(MAX_IN_FLIGHT_OPERATIONS)]
        for t in ts:
            t.start()
        return ts, results
    def test_seventh_connection_busy_then_all_six_succeed(self):
        ts, results = self._block_six()
        try:
            self.assertTrue(self.s.entered.wait(timeout=5.0))
            with self.assertRaises(DriveClientError) as cm:
                self.client(self.a, _MAIN_TOKEN).list("main", limit=5)
            self.assertEqual(cm.exception.code, "busy")
            self.assertFalse(cm.exception.outcome_uncertain)
            self.assertTrue(cm.exception.retry_safe)
        finally:
            self.s.release_gate()
        for t in ts:
            t.join(timeout=10.0)
        self.assertEqual(results.count("ok"), MAX_IN_FLIGHT_OPERATIONS, results)
    def test_busy_rejection_under_concurrent_trickle(self):
        ts, _results = self._block_six()
        stop = threading.Event()
        def trickle():
            try:
                c = _Raw(self.a.port, timeout=2.0)
                try:
                    c.send(b"POST /v1/list HTTP/1.1\r\n")
                    while not stop.is_set():
                        c.send(b"X")
                        time.sleep(0.05)
                finally:
                    c.close()
            except OSError:
                pass
        trick = threading.Thread(target=trickle, daemon=True)
        try:
            self.assertTrue(self.s.entered.wait(timeout=5.0))
            trick.start()
            with self.assertRaises(DriveClientError) as cm:
                self.client(self.a, _MAIN_TOKEN).list("main", limit=5)
            self.assertEqual(cm.exception.code, "busy")
        finally:
            stop.set()
            self.s.release_gate()
        for t in ts:
            t.join(timeout=10.0)
    def test_thread_count_is_constant_plus_seven(self):
        base = self.make()
        _ = base.port
        time.sleep(0.1)
        before = threading.active_count()
        second = self.make()
        _ = second.port
        deadline = time.monotonic() + 2.0
        delta = 0
        while time.monotonic() < deadline:
            delta = threading.active_count() - before
            if delta >= MAX_IN_FLIGHT_OPERATIONS + 1:
                break
            time.sleep(0.02)
        self.assertEqual(delta, MAX_IN_FLIGHT_OPERATIONS + 1)
    def _trickle_to_deadline(self, head, *, socket_timeout=0.5):
        a = self.make(policies=self.policies, socket_timeout=socket_timeout)
        c = _Raw(a.port, timeout=5.0)
        start = time.monotonic()
        try:
            c.send(head)
            for _ in range(10):
                try:
                    c.send(b"X")
                except OSError:
                    break
                time.sleep(0.2)
            raw = c.read_all(deadline=time.monotonic() + 2.0)
        finally:
            c.close()
        return time.monotonic() - start, raw
    def test_slow_header_trickle_hits_absolute_deadline(self):
        elapsed, raw = self._trickle_to_deadline(b"POST /v1/list HTTP/1.1\r\n")
        self.assertLess(elapsed, 3.0, "trickle not bounded: %.2fs" % elapsed)
        if raw:
            status, _h, _b = _parse(raw)
            if status is not None:
                self.assertEqual(status, 504)
    def test_slow_body_trickle_hits_absolute_deadline(self):
        head = _frame("/v1/list", _hdrs(_MAIN_TOKEN, cl=1000, body=b""), b"")
        elapsed, raw = self._trickle_to_deadline(head)
        self.assertLess(elapsed, 3.0, "slow body not bounded: %.2fs" % elapsed)
        if raw:
            status, _h, _b = _parse(raw)
            if status is not None:
                self.assertEqual(status, 504)
@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class ShutdownDrainRedactionTests(_Base):
    def test_close_rejects_invalid_timeouts(self):
        a = self.make()
        for bad in (-1.0, float("nan"), float("inf"), True, "5"):
            with self.assertRaises(ValueError):
                a.close(timeout=bad)
    def test_close_with_blocked_op_false_then_true(self):
        s = self.instrumented()
        a = self.make(service=s)
        s.block = True
        s.block_target = 1
        s.block_timeout = 10.0
        def call():
            try:
                self.client(a, _MAIN_TOKEN).list("main", limit=5)
            except DriveClientError:
                pass
        t = threading.Thread(target=call)
        t.start()
        try:
            self.assertTrue(s.entered.wait(timeout=5.0))
            start = time.monotonic()
            result = a.close(timeout=0.1)
            elapsed = time.monotonic() - start
            self.assertFalse(result)
            self.assertLess(elapsed, 1.0, "close overran: %.2fs" % elapsed)
        finally:
            s.release_gate()
        t.join(timeout=10.0)
        self.assertTrue(a.close(timeout=5.0))
    def test_new_requests_rejected_after_close(self):
        a = self.make()
        port = a.port
        self.assertTrue(a.close(timeout=5.0))
        with self.assertRaises((DriveClientError, OSError)):
            DriveClient(port=port, token=_MAIN_TOKEN, timeout=1.0).list("main", limit=5)
    def test_repr_is_redacted(self):
        a = self.make()
        text = repr(a)
        for secret in (_MAIN_TOKEN, self.roots["main"], "savepool"):
            self.assertNotIn(secret, text)
    def test_error_envelope_is_redacted(self):
        s = self.instrumented()
        a = self.make(service=s)
        body = _jb({"source_id": "main", "file_id": "root", "limit": 5})
        raw = _exchange(a.port, _frame("/v1/list", _hdrs(_MAIN_TOKEN, body=body), body))
        text = raw.decode("latin-1")
        for secret in (_MAIN_TOKEN, self.roots["main"], "Traceback", 'File "'):
            self.assertNotIn(secret, text)
class SmokeSoakGateContractTests(unittest.TestCase):
    def test_helper_raises_without_production_adapter(self):
        if _ADAPTER_AVAILABLE:
            self.skipTest("production adapter present; gate test not applicable")
        import shutil
        base = tempfile.mkdtemp(prefix="d067-gate-")
        try:
            policies, _roots = _standard_policies(base)
            with self.assertRaises(RuntimeError):
                run_smoke(policies)
            with self.assertRaises(RuntimeError):
                run_soak(policies)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    def test_thresholds_fixed(self):
        self.assertEqual(THRESHOLDS["max_concurrent_operations"], 6)
        self.assertEqual(THRESHOLDS["expected_adapter_threads"], 7)
        self.assertEqual(THRESHOLDS["max_workspace_bytes"], 128 * 1024 * 1024)
        self.assertEqual(THRESHOLDS["rss_growth_bytes_after_warmup"], 64 * 1024 * 1024)
        self.assertEqual(THRESHOLDS["max_stage_seconds"], 15 * 60)
        self.assertEqual(SMOKE_CALLS, 600)
        self.assertEqual(SOAK_CALLS, 10000)
def _require_adapter():
    if not _ADAPTER_AVAILABLE:
        raise RuntimeError(
            "production drive_tool.http_adapter (D068) is absent; the 600-call "
            "smoke and 10,000-call soak are gated and cannot be claimed")
def _mixed_call(client, source_id, index):
    roll = index % 10
    try:
        if roll < 3:
            client.list(source_id, limit=20)
            return "list"
        if roll < 5:
            client.find(source_id, name_contains="data", limit=10)
            return "find"
        if roll < 8:
            client.read(source_id, "p/readme.txt", offset=0, length=64)
            return "read"
        client.save(source_id, "p/savepool/s%02d.bin" % (index % 32), b"x" * 8,
                    overwrite=True)
        return "save"
    except DriveClientError as exc:
        return "err:" + exc.code
_SOAK_AGENTS = ((_MAIN_TOKEN, "main"), (_OTHER_TOKEN, "other"),
                (_SMALL_TOKEN, "small"), (_READONLY_TOKEN, "ro"),
                (_ALIAS_A_TOKEN, "shared"), (_ALIAS_B_TOKEN, "shared"))
def _run_workload(policies, *, agents, calls, concurrency, stage_budget):
    _require_adapter()
    adapter = DriveHTTPAdapter(policies, port=0, socket_timeout=5.0)
    try:
        time.sleep(0.1)
        fd0, rss0 = _nfd(), _rss_kb()
        start = time.monotonic()
        deadline = start + stage_budget
        counts = {}
        lock = threading.Lock()
        def worker(w):
            local = {}
            for i in range(w, calls, concurrency):
                if time.monotonic() > deadline:
                    break
                token, source_id = agents[i % len(agents)]
                client = DriveClient(port=adapter.port, token=token, timeout=5.0)
                tag = _mixed_call(client, source_id, i)
                local[tag] = local.get(tag, 0) + 1
            with lock:
                for k, v in local.items():
                    counts[k] = counts.get(k, 0) + v
        ts = [threading.Thread(target=worker, args=(w,)) for w in range(concurrency)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=stage_budget)
        elapsed = time.monotonic() - start
        closed = adapter.close(timeout=10.0)
        fd1, rss1 = _nfd(), _rss_kb()
        completed = sum(counts.values())
        errs = {k: v for k, v in counts.items() if k.startswith("err:")}
        failures = []
        if completed != calls:
            failures.append("completed %d != %d" % (completed, calls))
        if not closed:
            failures.append("adapter.close() did not return True")
        if fd0 is not None and fd1 is not None and \
                fd1 > fd0 + THRESHOLDS["fd_baseline_slack"]:
            failures.append("fd grew %s -> %s" % (fd0, fd1))
        if rss0 is not None and rss1 is not None and \
                (rss1 - rss0) * 1024 > THRESHOLDS["rss_growth_bytes_after_warmup"]:
            failures.append("rss grew %d KiB" % (rss1 - rss0))
        if elapsed > stage_budget:
            failures.append("stage overran budget: %.1fs" % elapsed)
        if errs:
            failures.append("unexpected error codes: %s" % errs)
        return {"calls_requested": calls, "calls_completed": completed,
                "outcomes": dict(sorted(counts.items())), "error_codes": errs,
                "closed": bool(closed), "fd_before": fd0, "fd_after": fd1,
                "rss_before_kb": rss0, "rss_after_kb": rss1,
                "seconds": round(elapsed, 2), "failures": failures,
                "result": "PASS" if not failures else "FAIL"}
    finally:
        try:
            adapter.close(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
def run_smoke(policies, *, stage_budget=THRESHOLDS["max_stage_seconds"]):
    """Fixed 600-call mixed-call HTTP smoke. Fails closed without the adapter."""
    return _run_workload(policies, agents=((_MAIN_TOKEN, "main"),),
                         calls=SMOKE_CALLS, concurrency=MAX_IN_FLIGHT_OPERATIONS,
                         stage_budget=stage_budget)
def run_soak(policies, *, stage_budget=THRESHOLDS["max_stage_seconds"]):
    """Fixed 10,000-call six-agent HTTP soak. Fails closed without the adapter."""
    return _run_workload(policies, agents=_SOAK_AGENTS, calls=SOAK_CALLS,
                         concurrency=MAX_IN_FLIGHT_OPERATIONS,
                         stage_budget=stage_budget)
_RUN_SMOKE_SOAK = os.environ.get("D067_RUN_HTTP_SMOKE_SOAK") == "1"
@unittest.skipUnless(_ADAPTER_AVAILABLE and _RUN_SMOKE_SOAK,
                     "smoke/soak opt-in (D067_RUN_HTTP_SMOKE_SOAK=1) + production adapter")
class SmokeSoakGateTests(_Base):
    _smoke = None
    def test_stage_smoke_600(self):
        policies, _r = _standard_policies(self._tmp)
        res = run_smoke(policies)
        SmokeSoakGateTests._smoke = res
        self.assertEqual(res["result"], "PASS", res)
    def test_stage_soak_10000(self):
        if SmokeSoakGateTests._smoke is None:
            self.skipTest("soak is gated on a passing smoke stage")
        policies, _r = _standard_policies(self._tmp)
        self.assertEqual(run_soak(policies)["result"], "PASS")
# ---------------------------------------------------------------------------
# D080 security-fix regressions.
#
# Translated from the D080 example regressions (which were written for pytest
# with an imagined ``adapter`` fixture) into this existing unittest harness.
# Semantics are preserved; the two tests whose example form is not
# deterministic or not reproducible in this environment use a deterministic
# seam instead, as noted at each site.  No pytest dependency is introduced.


def _raw_exchange(port, payload, *, timeout=1.0, half_close=True):
    """Send raw bytes to the loopback adapter and collect the whole reply."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(payload)
        if half_close:
            s.shutdown(socket.SHUT_WR)
        s.settimeout(timeout)
        chunks = []
        while True:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        s.close()


@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class D080SecurityFixRegressionTests(_Base):
    def test_partial_header_bounds_reject_early(self):
        a = self.make(socket_timeout=1.0)
        cases = (
            ("overlong_request_line",
             b"POST /" + b"a" * 8200 + b" HTTP/1.1\r\n"),
            ("overlong_header_block",
             b"POST /v1/list HTTP/1.1\r\nX-Pad: " + b"a" * 16384 + b"\r\n"),
            ("bare_lf_header_block",
             b"POST /v1/list HTTP/1.1\nHost: 127.0.0.1\n\n"),
        )
        for name, payload in cases:
            with self.subTest(case=name):
                raw = _raw_exchange(a.port, payload, timeout=1.0)
                self.assertIn(b" 400 ", raw, raw)
                self.assertIn(b'"code":"invalid"', raw, raw)
                self.assertIn(b'"retry_safe":false', raw, raw)
                self.assertNotIn(b" 504 ", raw, raw)

    def test_content_length_priority_and_leading_zeros(self):
        a = self.make(socket_timeout=1.0)
        # Missing Authorization + huge Content-Length: the auth check must
        # precede the size gate.
        raw = _raw_exchange(
            a.port,
            b"POST /v1/list HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Length: 99999999999\r\n"
            b"Content-Type: application/json\r\n\r\n",
            timeout=1.0,
        )
        self.assertIn(b" 403 ", raw, raw)
        self.assertNotIn(b" 413 ", raw, raw)

        # Valid lexical Authorization + invalid Content-Type + huge
        # Content-Length: the Content-Type check must precede the size gate.
        raw = _raw_exchange(
            a.port,
            b"POST /v1/list HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Authorization: Bearer " + b"x" * 32 + b"\r\n"
            b"Content-Length: 99999999999\r\n"
            b"Content-Type: text/plain\r\n\r\n",
            timeout=1.0,
        )
        self.assertIn(b" 400 ", raw, raw)
        self.assertNotIn(b" 413 ", raw, raw)

        # Leading zeros are normalised: 00000000002 is 2, not too_large.
        raw = _raw_exchange(
            a.port,
            b"POST /v1/list HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Authorization: Bearer " + b"x" * 32 + b"\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 00000000002\r\n\r\n{}",
            timeout=1.0,
        )
        self.assertNotIn(b" 413 ", raw, raw)

    def test_startup_six_worker_readiness(self):
        a = self.make(socket_timeout=3.0)
        # Deterministic seam (the D080 example used a 6-way concurrent burst,
        # which does not deterministically expose the startup window): the
        # corrected __init__ registers all six slots as idle synchronously,
        # before any thread can run, so no arrival can be falsely refused.
        self.assertEqual(len(a._idle_slots), MAX_IN_FLIGHT_OPERATIONS)
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(MAX_IN_FLIGHT_OPERATIONS)

        def hit():
            barrier.wait(timeout=5.0)
            raw = _raw_exchange(
                a.port,
                b"POST /v1/list HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Length: 0\r\n"
                b"Content-Type: application/json\r\n\r\n",
                timeout=3.0,
            )
            with lock:
                results.append(raw)

        threads = [threading.Thread(target=hit)
                   for _ in range(MAX_IN_FLIGHT_OPERATIONS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        self.assertEqual(len(results), MAX_IN_FLIGHT_OPERATIONS, results)
        self.assertFalse(any(b" 503 " in r for r in results), results)

    def test_repeat_close_does_not_wake_released_port(self):
        # Deterministic seam (the D080 example re-bound the released port and
        # asserted accept() timed out; that rebind is not reproducible here
        # because the in-flight connection still holds the port): count
        # _wake_listener calls, which is the same property stated directly.
        svc = self.instrumented()
        a = self.make(service=svc, socket_timeout=5.0)
        wakes = []
        real_wake = a._wake_listener

        def counting_wake():
            wakes.append(True)
            return real_wake()

        a._wake_listener = counting_wake

        svc.block = True
        svc.block_target = 1
        svc.block_timeout = 10.0

        def in_flight():
            try:
                self.client(a, _MAIN_TOKEN).list("main", limit=5)
            except DriveClientError:
                pass

        t = threading.Thread(target=in_flight, daemon=True)
        t.start()
        self.assertTrue(svc.entered.wait(timeout=5.0))
        # The first close cannot drain the in-flight worker.
        self.assertIs(a.close(timeout=0.01), False)
        self.assertEqual(len(wakes), 1)
        # A repeat close must not wake/close the already-released listener.
        a.close(timeout=0.05)
        self.assertEqual(len(wakes), 1)
        svc.release_gate()
        t.join(timeout=10.0)
        self.assertTrue(a.close(timeout=5.0))


# ---------------------------------------------------------------------------
# D087 close()-budget regressions.
#
# Translated from the D087 example regression (which was a pytest module with a
# bare adapter built via __new__) into this existing unittest harness.  The
# assertions are preserved and extended with the properties D091 asks for: the
# contended caller honours its own budget within a documented scheduler margin,
# the failed close touches no active descriptor, the lock is released exactly
# once, close is idempotent afterwards, and no thread leaks.


class _BlockingService:
    """Minimal service whose close() blocks until it is released."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.close_calls = 0

    def close(self, *, timeout):
        self.close_calls += 1
        self.entered.set()
        self.release.wait(timeout=5.0)
        return True


class _Slot:
    """One worker slot, matching the adapter's internal shape."""

    __slots__ = ("conn", "thread")

    def __init__(self, conn=None):
        self.conn = conn
        self.thread = None


def _bare_adapter(service, slots=()):
    """A DriveHTTPAdapter with no listener or threads, for close()-only tests.

    ``_port`` stays 0 so the adapter's own ``_wake_listener`` short-circuits.
    """
    a = DriveHTTPAdapter.__new__(DriveHTTPAdapter)
    a._close_lock = threading.Lock()
    a._closed = False
    a._listener_closed = False
    a._draining = False
    a._pool_cond = threading.Condition()
    a._slots = list(slots)
    a._listener = None
    a._accept_thread = None
    a._workers = []
    a._service = service
    a._port = 0
    return a


@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class CloseBudgetTests(unittest.TestCase):
    def _contend(self, slots=()):
        service = _BlockingService()
        adapter = _bare_adapter(service, slots=slots)
        first_result = []
        first = threading.Thread(
            target=lambda: first_result.append(adapter.close(timeout=5.0)),
            daemon=True,
        )
        first.start()
        self.assertTrue(service.entered.wait(timeout=1.0),
                        "first close never reached the service lease")
        return adapter, service, first, first_result

    def test_second_close_honors_deadline_while_first_holds_lock(self):
        adapter, service, first, first_result = self._contend()
        try:
            started = time.monotonic()
            second_result = adapter.close(timeout=0.05)
            elapsed = time.monotonic() - started
            # budget 0.05s plus a 0.10s documented scheduler margin
            self.assertIs(second_result, False)
            self.assertLess(elapsed, 0.05 + 0.10, elapsed)
            self.assertEqual(service.close_calls, 1)
            service.release.set()
            first.join(timeout=1.0)
            self.assertFalse(first.is_alive())
            self.assertEqual(first_result, [True])
            self.assertIs(adapter._closed, True)
            self.assertEqual(service.close_calls, 1)
            # idempotent afterwards, with no further service lease
            self.assertIs(adapter.close(timeout=0.1), True)
            self.assertEqual(service.close_calls, 1)
        finally:
            service.release.set()
            first.join(timeout=5.0)

    def test_contended_close_does_not_close_active_descriptors(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        adapter, service, first, first_result = self._contend(slots=(_Slot(left),))
        threads_before = threading.active_count()
        try:
            # the winner closes the pending handoff before it parks in the
            # service lease: that descriptor-ownership behaviour is preserved
            self.assertIsNone(adapter._slots[0].conn)
            self.assertEqual(left.fileno(), -1)
            # a live descriptor the contended caller must not touch
            probe_l, probe_r = socket.socketpair()
            self.addCleanup(probe_l.close)
            self.addCleanup(probe_r.close)
            fd_before = len(os.listdir("/proc/self/fd"))
            self.assertIs(adapter.close(timeout=0.05), False)
            self.assertEqual(len(os.listdir("/proc/self/fd")), fd_before,
                             "the contended close closed a descriptor")
            probe_r.sendall(b"ping")
            self.assertEqual(probe_l.recv(4), b"ping")
        finally:
            service.release.set()
            first.join(timeout=5.0)
        self.assertEqual(first_result, [True])
        self.assertLessEqual(threading.active_count(), threads_before)
        self.assertIs(adapter.close(timeout=0.1), True)

    def test_lock_is_released_once_and_close_is_idempotent(self):
        adapter, service, first, first_result = self._contend()
        try:
            self.assertIs(adapter.close(timeout=0.05), False)
            service.release.set()
            first.join(timeout=1.0)
            self.assertEqual(first_result, [True])
            self.assertTrue(adapter._close_lock.acquire(blocking=False),
                            "close lock was not released")
            adapter._close_lock.release()
            for _ in range(3):
                self.assertIs(adapter.close(timeout=0.05), True)
            self.assertEqual(service.close_calls, 1)
        finally:
            service.release.set()
            first.join(timeout=5.0)


# ---------------------------------------------------------------------------
# D094 busy-phase budget regressions.
#
# Translated from the D094 example regression (written against an imagined
# ``server``/``Server``) onto this harness and the real
# ``drive_tool.http_adapter`` / ``DriveHTTPAdapter``.  Fully deterministic: a fake
# monotonic clock and a fake socket, no network and no pytest dependency.
#
# The defect under test is that the pre-read used the whole outer budget, so a
# peer that sends nothing consumed all 50 ms and the 503 busy write was never
# attempted -- the client then saw a transport reset (unavailable) instead of a
# busy envelope.  The phase split reserves a write window.

from unittest import mock as _mock


class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _FakeConn:
    def __init__(self, clock, chunks=None, recv_advance=None,
                 timeout_on_empty=False, endless=False):
        self.clock = clock
        self.chunks = list(chunks or [])
        self.recv_advance = recv_advance
        self.timeout_on_empty = timeout_on_empty
        self.endless = endless
        self.timeout = None
        self.sent = bytearray()
        self.total_read = 0
        self.closed = False
        self.shutdown_called = False
        self.recv_calls = 0

    def settimeout(self, t):
        self.timeout = t

    def recv(self, n):
        self.recv_calls += 1
        if self.timeout is None:
            raise socket.timeout()
        advance = self.recv_advance if self.recv_advance is not None else self.timeout
        self.clock.advance(advance)
        if self.endless:
            chunk = b"x"
            self.total_read += len(chunk)
            return chunk
        if self.chunks:
            chunk = self.chunks.pop(0)
            if chunk:
                chunk = chunk[:n]
                self.total_read += len(chunk)
                return chunk
        if self.timeout_on_empty:
            raise socket.timeout()
        return b""

    def send(self, data):
        self.sent.extend(data)
        return len(data)

    def shutdown(self, how):
        self.shutdown_called = True

    def close(self):
        self.closed = True


@unittest.skipUnless(_ADAPTER_AVAILABLE, _SKIP_REASON)
class BusyPhaseBudgetTests(unittest.TestCase):
    def setUp(self):
        import drive_tool.http_adapter as _ha
        self.ha = _ha
        self.clock = _FakeClock()
        patcher = _mock.patch.object(_ha.time, "monotonic", self.clock.monotonic)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.adapter = object.__new__(_ha.DriveHTTPAdapter)

        def send_with_deadline(conn, data, deadline):
            now = _ha.time.monotonic()
            if now >= deadline:
                return False
            conn.settimeout(max(0.0, deadline - now))
            conn.send(data)
            return True

        self.adapter._send_with_deadline = send_with_deadline

    def _elapsed(self):
        return self.clock.t - 1000.0

    def test_delayed_payload_pre_read_cannot_starve_busy_write(self):
        conn = _FakeConn(self.clock, timeout_on_empty=True)
        self.adapter._busy_reject(conn)
        self.assertGreater(len(conn.sent), 0,
                           "503 write must be attempted after the bounded pre-read")
        self.assertIn(b"503 Service Unavailable", conn.sent)
        self.assertIn(b'"code":"busy"', conn.sent)
        self.assertLessEqual(self._elapsed(),
                             self.ha.BUSY_REJECT_BUDGET_SECONDS + 0.001)

    def test_total_read_and_deadline_caps(self):
        conn = _FakeConn(self.clock, chunks=[b"x" * 4096] * 10, recv_advance=0.001)
        self.adapter._busy_reject(conn)
        self.assertLessEqual(conn.total_read, self.ha.MAX_BUSY_READ_BYTES)
        self.assertLessEqual(self._elapsed(),
                             self.ha.BUSY_REJECT_BUDGET_SECONDS + 0.001)

    def test_post_drain_is_bounded(self):
        conn = _FakeConn(self.clock, endless=True, recv_advance=0.005)
        self.adapter._busy_reject(conn)
        self.assertLessEqual(self._elapsed(),
                             self.ha.BUSY_REJECT_BUDGET_SECONDS + 0.001)
        self.assertLessEqual(conn.total_read, self.ha.MAX_BUSY_READ_BYTES)

    def test_busy_envelope_is_not_unavailable(self):
        conn = _FakeConn(self.clock, timeout_on_empty=True)
        self.adapter._busy_reject(conn)
        sent_lower = bytes(conn.sent).lower()
        self.assertIn(b'"outcome_uncertain":false', sent_lower)
        self.assertIn(b'"retry_safe":true', sent_lower)
        # The D094 example asserted that the whole response never contains
        # "unavailable", which cannot hold: 503's reason phrase is literally
        # "Service Unavailable".  The property that matters is that the error
        # CODE is busy and not unavailable, so check the envelope body.
        self.assertIn(b'"code":"busy"', sent_lower)
        self.assertNotIn(b"unavailable",
                         bytes(conn.sent).split(b"\r\n\r\n", 1)[1].lower())

    def test_phase_constants_are_frozen(self):
        self.assertEqual(self.ha.BUSY_REJECT_BUDGET_SECONDS, 0.050)
        self.assertEqual(self.ha.BUSY_PRE_READ_BUDGET_SECONDS, 0.010)
        self.assertEqual(self.ha.BUSY_WRITE_RESERVE_SECONDS, 0.020)
        self.assertEqual(self.ha.BUSY_POST_DRAIN_BUDGET_SECONDS, 0.020)
        self.assertEqual(self.ha.MAX_BUSY_READ_BYTES, 16384)
        self.assertEqual(self.ha.MAX_IN_FLIGHT_OPERATIONS, 6)
        self.assertEqual(self.ha.POST_RESPONSE_DRAIN_SECONDS, 0.05)
        self.assertLessEqual(
            self.ha.BUSY_PRE_READ_BUDGET_SECONDS + self.ha.BUSY_WRITE_RESERVE_SECONDS,
            self.ha.BUSY_REJECT_BUDGET_SECONDS)


if __name__ == "__main__":
    unittest.main()
