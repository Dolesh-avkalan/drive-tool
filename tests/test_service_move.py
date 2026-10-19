"""Tests for the move/trash additions (MODULE-006).

Covers the service layer (``MovableFileService``), the HTTP endpoints
(``MoveHTTPAdapter``) and the launcher config (``drive_tool.serve``), using the
same construction patterns as ``tests/test_file_service.py`` and
``tests/test_http_adapter.py``.

Two facts about the attached code shape this module:

* ``SourceGrant.__post_init__`` calls ``_require_root`` which raises
  ``TypeError('root must be str')`` for a ``pathlib.Path``.  Every grant here
  is therefore built with ``os.path.realpath(...)`` (a ``str``), exactly as
  ``test_http_adapter._build_root`` does.
* The committed ``tests/test_service_policy.py`` asserts that ``OPERATIONS``
  equals the frozen operation set.  This module makes no claim about
  ``OPERATIONS`` globally; it only verifies the move/trash surface built on
  top of it.

Every expected error code/value is taken from the attached code:
``exists`` / ``not_found`` / ``denied`` / ``invalid`` from
``file_service`` and ``local_move``, 400 ``invalid`` / 403 ``denied`` / 503
``unavailable`` from ``http_adapter``, and ``ConfigError`` from ``serve``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from drive_tool.file_service import SERVICE_MAX_DEPTH, ServiceError
from drive_tool.file_service_move import MovableFileService
from drive_tool.local import LocalAdapter
from drive_tool.service_policy import AgentPolicy, SourceGrant
from drive_tool.storage import ROOT_ID

try:
    from drive_tool.http_move import MoveHTTPAdapter
    from drive_tool.service_client import DriveClient

    _HTTP_AVAILABLE = True
    _HTTP_IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    MoveHTTPAdapter = None  # type: ignore
    DriveClient = None  # type: ignore
    _HTTP_AVAILABLE = False
    _HTTP_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

try:
    import drive_tool.serve as serve_module

    _SERVE_AVAILABLE = True
    _SERVE_IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    serve_module = None  # type: ignore
    _SERVE_AVAILABLE = False
    _SERVE_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

HTTP_SKIP = "http move adapter unavailable: %s" % (_HTTP_IMPORT_ERROR,)
SERVE_SKIP = "drive_tool.serve unavailable: %s" % (_SERVE_IMPORT_ERROR,)

_TOKEN_PAD = "0123456789abcdef0123456789abcdef"
_ALL_OPS = frozenset({"list", "find", "read", "save", "move", "trash"})
_SAVE_OPS = frozenset({"list", "find", "read", "save"})

def _pad_token(base: str) -> str:
    # service_policy requires a 32..256 char visible-ASCII bearer token.
    return (base + _TOKEN_PAD)[:40]

def _token_digest(token: str) -> bytes:
    # AgentPolicy.token_sha256 is the raw 32-byte SHA-256 digest, not hex.
    return hashlib.sha256(token.encode("utf-8")).digest()

def _make_grant(alias: str, root: Path | str, *, operations=_SAVE_OPS) -> SourceGrant:
    # SourceGrant.root must be a str; Path is rejected with TypeError.
    return SourceGrant(
        alias=alias,
        root=os.path.realpath(str(root)),
        operations=frozenset(operations),
    )

def _make_agent(agent_id: str, token: str, grants) -> AgentPolicy:
    return AgentPolicy(
        agent_id=agent_id,
        token_sha256=_token_digest(token),
        grants=tuple(grants),
    )

def _local_supported() -> bool:
    try:
        with tempfile.TemporaryDirectory() as td:
            LocalAdapter("probe", td).close()
        return True
    except Exception:  # noqa: BLE001
        return False

_LOCAL_SUPPORTED = _local_supported()
_LOCAL_SKIP = "LocalAdapter unsupported on this platform"

class _TempRoots(unittest.TestCase):
    """Temporary synthetic roots, mirroring test_file_service.py."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="move_svc_")
        self.base_dir = Path(self._tmp.name)
        self.addCleanup(self._rm)

    def _rm(self) -> None:
        shutil.rmtree(self._tmp.name, ignore_errors=True)

    def _mkdir(self, rel: str) -> Path:
        path = self.base_dir / rel
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

class GrantSurfaceTests(unittest.TestCase):
    """A grant accepts move/trash and rejects unknown operations."""

    def test_grant_accepts_move_and_trash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            grant = _make_grant("g", td, operations={"move", "trash"})
            self.assertEqual(grant.operations, frozenset({"move", "trash"}))

    def test_grant_rejects_unknown_operation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                _make_grant("g", td, operations={"delete"})

    def test_grant_rejects_path_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(TypeError):
                SourceGrant(
                    alias="g",
                    root=Path(td),
                    operations=frozenset({"move"}),
                )

@unittest.skipUnless(_LOCAL_SUPPORTED, _LOCAL_SKIP)
class MoveServiceTests(_TempRoots):
    """MovableFileService.move / .trash against real synthetic roots."""

    def _service(self, operations=_ALL_OPS, token=None):
        token = token or _pad_token("move-agent-token-001")
        self.root = self._mkdir("root")
        grant = _make_grant("work", self.root, operations=operations)
        service = MovableFileService((_make_agent("agent", token, (grant,)),))
        self.addCleanup(service.close)
        return service, token

    def test_move_with_rename(self) -> None:
        service, token = self._service()
        self._write(self.root / "src.txt", b"payload")
        res = service.move(token, "work", "p/src.txt", "root", "renamed.txt")
        self.assertEqual(
            res, {"ref": {"source_id": "work", "file_id": "p/renamed.txt"}}
        )
        self.assertFalse((self.root / "src.txt").exists())
        self.assertEqual((self.root / "renamed.txt").read_bytes(), b"payload")

    def test_move_keeps_leaf_name_when_new_name_none(self) -> None:
        service, token = self._service()
        self._write(self.root / "keep.txt", b"keep")
        sub = self._mkdir("root/sub")
        res = service.move(token, "work", "p/keep.txt", "p/sub")
        self.assertEqual(
            res, {"ref": {"source_id": "work", "file_id": "p/sub/keep.txt"}}
        )
        self.assertEqual((sub / "keep.txt").read_bytes(), b"keep")
        self.assertFalse((self.root / "keep.txt").exists())

    def test_move_into_another_folder(self) -> None:
        service, token = self._service()
        self._write(self.root / "a.txt", b"aaa")
        dest = self._mkdir("root/dest")
        res = service.move(token, "work", "p/a.txt", "p/dest", "b.txt")
        self.assertEqual(
            res, {"ref": {"source_id": "work", "file_id": "p/dest/b.txt"}}
        )
        self.assertEqual((dest / "b.txt").read_bytes(), b"aaa")
        self.assertFalse((self.root / "a.txt").exists())

    def test_move_onto_existing_target_errors_and_leaves_both_untouched(self) -> None:
        service, token = self._service()
        self._write(self.root / "src.txt", b"SRC-BYTES")
        self._write(self.root / "taken.txt", b"TARGET-BYTES")
        with self.assertRaises(ServiceError) as ctx:
            service.move(token, "work", "p/src.txt", "root", "taken.txt")
        err = ctx.exception
        self.assertEqual(err.code, "exists")
        self.assertTrue(err.outcome_uncertain)
        self.assertFalse(err.retry_safe)
        self.assertIsNone(err.__cause__)
        self.assertEqual((self.root / "src.txt").read_bytes(), b"SRC-BYTES")
        self.assertEqual((self.root / "taken.txt").read_bytes(), b"TARGET-BYTES")

    def test_move_directory_invalid(self) -> None:
        service, token = self._service()
        self._mkdir("root/somedir")
        with self.assertRaises(ServiceError) as ctx:
            service.move(token, "work", "p/somedir", "root", "other")
        self.assertEqual(ctx.exception.code, "invalid")
        self.assertFalse(ctx.exception.outcome_uncertain)

    def test_trash_removes_from_list_and_keeps_bytes_on_disk(self) -> None:
        service, token = self._service()
        self._write(self.root / "gone.txt", b"TRASH-ME")
        listed = {i["name"] for i in service.list(token, "work", ROOT_ID)["items"]}
        self.assertIn("gone.txt", listed)

        res = service.trash(token, "work", "p/gone.txt")
        self.assertEqual(
            res,
            {"ref": {"source_id": "work", "file_id": "p/gone.txt"}, "trashed": True},
        )

        listed_after = {
            i["name"] for i in service.list(token, "work", ROOT_ID)["items"]
        }
        self.assertNotIn("gone.txt", listed_after)
        self.assertFalse((self.root / "gone.txt").exists())
        # Trashed bytes are retained somewhere under the root, never deleted.
        retained = [
            p
            for p in self.root.rglob("*")
            if p.is_file() and p.read_bytes() == b"TRASH-ME"
        ]
        self.assertEqual(len(retained), 1, retained)
        # The response never discloses the trash location.
        self.assertNotIn(str(retained[0].relative_to(self.root)), json.dumps(res))

    def test_trash_twice_same_name(self) -> None:
        service, token = self._service()
        self._write(self.root / "dup.txt", b"FIRST")
        first = service.trash(token, "work", "p/dup.txt")
        self.assertTrue(first["trashed"])

        # Recreate a file with the same name and trash it too.
        self._write(self.root / "dup.txt", b"SECOND")
        second = service.trash(token, "work", "p/dup.txt")
        self.assertTrue(second["trashed"])
        self.assertEqual(second["ref"]["file_id"], "p/dup.txt")

        # Neither trashed copy is visible via list, but both byte strings survive.
        listed = {i["name"] for i in service.list(token, "work", ROOT_ID)["items"]}
        self.assertNotIn("dup.txt", listed)
        blobs = sorted(p.read_bytes() for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(blobs, [b"FIRST", b"SECOND"])

    def test_trash_missing_file_errors(self) -> None:
        service, token = self._service()
        with self.assertRaises(ServiceError) as ctx:
            service.trash(token, "work", "p/nope.txt")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_grant_without_move_denied(self) -> None:
        service, token = self._service(operations=_SAVE_OPS)
        self._write(self.root / "x.txt", b"x")
        with self.assertRaises(ServiceError) as ctx:
            service.move(token, "work", "p/x.txt", "root", "y.txt")
        err = ctx.exception
        self.assertEqual(err.code, "denied")
        self.assertFalse(err.outcome_uncertain)
        self.assertFalse(err.retry_safe)
        self.assertTrue((self.root / "x.txt").exists())

    def test_grant_without_trash_denied(self) -> None:
        service, token = self._service(
            operations=frozenset({"list", "find", "read", "save", "move"})
        )
        self._write(self.root / "x.txt", b"x")
        with self.assertRaises(ServiceError) as ctx:
            service.trash(token, "work", "p/x.txt")
        self.assertEqual(ctx.exception.code, "denied")
        self.assertTrue((self.root / "x.txt").exists())

    def test_move_grant_without_save_still_works(self) -> None:
        service, token = self._service(operations=frozenset({"list", "move", "trash"}))
        self._write(self.root / "x.txt", b"x")
        res = service.move(token, "work", "p/x.txt", "root", "y.txt")
        self.assertEqual(res["ref"]["file_id"], "p/y.txt")
        self.assertTrue((self.root / "y.txt").exists())

    def test_wrong_token_denied(self) -> None:
        service, _token = self._service()
        self._write(self.root / "x.txt", b"x")
        bad = _pad_token("wrong-token-value-999")
        for call in (
            lambda: service.move(bad, "work", "p/x.txt", "root", "y.txt"),
            lambda: service.trash(bad, "work", "p/x.txt"),
        ):
            with self.assertRaises(ServiceError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "denied")
            self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue((self.root / "x.txt").exists())

    def test_file_id_deeper_than_allowed_invalid(self) -> None:
        service, token = self._service()
        deep_id = (
            "p/" + "/".join("d%d" % i for i in range(SERVICE_MAX_DEPTH)) + "/f.txt"
        )
        with self.assertRaises(ServiceError) as ctx:
            service.move(token, "work", deep_id, "root", "g.txt")
        self.assertEqual(ctx.exception.code, "invalid")
        self.assertFalse(ctx.exception.outcome_uncertain)

        with self.assertRaises(ServiceError) as ctx_dest:
            service.move(token, "work", "p/x.txt", deep_id, "g.txt")
        self.assertEqual(ctx_dest.exception.code, "invalid")

    def test_root_file_id_rejected_for_move_and_trash(self) -> None:
        service, token = self._service()
        for call in (
            lambda: service.move(token, "work", ROOT_ID, "root", "y"),
            lambda: service.trash(token, "work", ROOT_ID),
        ):
            with self.assertRaises(ServiceError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "invalid")

    def test_non_string_new_name_invalid(self) -> None:
        service, token = self._service()
        self._write(self.root / "x.txt", b"x")
        with self.assertRaises(ServiceError) as ctx:
            service.move(token, "work", "p/x.txt", "root", new_name=123)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "invalid")

@unittest.skipUnless(_HTTP_AVAILABLE, HTTP_SKIP)
@unittest.skipUnless(_LOCAL_SUPPORTED, _LOCAL_SKIP)
class MoveHTTPTests(_TempRoots):
    """MoveHTTPAdapter over loopback HTTP, driven with raw sockets."""

    _TOKEN = _pad_token("http-move-token-0001")

    def setUp(self) -> None:
        super().setUp()
        self.root = self._mkdir("http_root")
        self._write(self.root / "src.txt", b"hello-http")
        self._write(self.root / "taken.txt", b"occupied")
        grant = _make_grant("work", self.root, operations=_ALL_OPS)
        self.policy = _make_agent("agent", self._TOKEN, (grant,))
        self.adapter = MoveHTTPAdapter(
            (self.policy,), port=0, socket_timeout=5.0
        )
        self.addCleanup(self._close_adapter)

    def _close_adapter(self) -> None:
        try:
            self.adapter.close(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass

    def _client(self):
        return DriveClient(port=self.adapter.port, token=self._TOKEN, timeout=5.0)

    def _post(self, target: bytes, body: bytes, *, token=None) -> bytes:
        token = token or self._TOKEN
        head = (
            b"POST " + target + b" HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Authorization: Bearer " + token.encode("ascii") + b"\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n\r\n"
        )
        sock = socket.create_connection(("127.0.0.1", self.adapter.port), timeout=5.0)
        try:
            sock.sendall(head + body)
            chunks = []
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.append(part)
            return b"".join(chunks)
        finally:
            sock.close()

    @staticmethod
    def _status(raw: bytes) -> int:
        line = raw.split(b"\r\n", 1)[0]
        return int(line.split(b" ")[1])

    @staticmethod
    def _body(raw: bytes) -> bytes:
        return raw.split(b"\r\n\r\n", 1)[1]

    def _move_payload(self, **overrides) -> bytes:
        payload = {
            "source_id": "work",
            "file_id": "p/src.txt",
            "dest_parent_id": "root",
        }
        payload.update(overrides)
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def test_http_move_succeeds(self) -> None:
        raw = self._post(b"/v1/move", self._move_payload(new_name="moved.txt"))
        self.assertEqual(self._status(raw), 200, raw[:200])
        body = json.loads(self._body(raw))
        self.assertTrue(body.get("ok"))
        self.assertEqual(
            body["result"],
            {"ref": {"source_id": "work", "file_id": "p/moved.txt"}},
        )
        self.assertEqual((self.root / "moved.txt").read_bytes(), b"hello-http")
        self.assertFalse((self.root / "src.txt").exists())

    def test_http_move_without_new_name(self) -> None:
        # Move into a *different* folder without a new name: leaf name is kept.
        dest = self._mkdir("http_root/sub")
        raw = self._post(
            b"/v1/move",
            self._move_payload(file_id="p/src.txt", dest_parent_id="p/sub"),
        )
        self.assertEqual(self._status(raw), 200, raw[:200])
        body = json.loads(self._body(raw))
        self.assertTrue(body.get("ok"))
        self.assertEqual(
            body["result"],
            {"ref": {"source_id": "work", "file_id": "p/sub/src.txt"}},
        )
        self.assertEqual((dest / "src.txt").read_bytes(), b"hello-http")
        self.assertFalse((self.root / "src.txt").exists())

    def test_http_trash_succeeds(self) -> None:
        body = json.dumps(
            {"source_id": "work", "file_id": "p/src.txt"}, separators=(",", ":")
        ).encode("utf-8")
        raw = self._post(b"/v1/trash", body)
        self.assertEqual(self._status(raw), 200, raw[:200])
        envelope = json.loads(self._body(raw))
        self.assertTrue(envelope.get("ok"))
        self.assertEqual(
            envelope["result"],
            {"ref": {"source_id": "work", "file_id": "p/src.txt"}, "trashed": True},
        )
        self.assertFalse((self.root / "src.txt").exists())

    def test_http_move_target_exists_reports_exists(self) -> None:
        raw = self._post(
            b"/v1/move",
            self._move_payload(file_id="p/src.txt", new_name="taken.txt"),
        )
        envelope = json.loads(self._body(raw))
        self.assertFalse(envelope.get("ok"))
        self.assertEqual(envelope["error"]["code"], "exists")
        self.assertTrue(envelope["error"]["outcome_uncertain"])
        self.assertFalse(envelope["error"]["retry_safe"])
        self.assertEqual((self.root / "taken.txt").read_bytes(), b"occupied")
        self.assertEqual((self.root / "src.txt").read_bytes(), b"hello-http")

    def test_http_move_missing_key_is_400(self) -> None:
        body = json.dumps(
            {"source_id": "work", "file_id": "p/src.txt"}, separators=(",", ":")
        ).encode("utf-8")
        raw = self._post(b"/v1/move", body)
        self.assertEqual(self._status(raw), 400, raw[:200])
        envelope = json.loads(self._body(raw))
        self.assertEqual(envelope["error"]["code"], "invalid")
        self.assertFalse(envelope["error"]["outcome_uncertain"])
        self.assertFalse(envelope["error"]["retry_safe"])

    def test_http_trash_missing_key_is_400(self) -> None:
        body = json.dumps({"source_id": "work"}, separators=(",", ":")).encode("utf-8")
        raw = self._post(b"/v1/trash", body)
        self.assertEqual(self._status(raw), 400, raw[:200])
        self.assertEqual(json.loads(self._body(raw))["error"]["code"], "invalid")

    def test_http_move_extra_key_is_400(self) -> None:
        body = json.dumps(
            {
                "source_id": "work",
                "file_id": "p/src.txt",
                "dest_parent_id": "root",
                "unexpected": True,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        raw = self._post(b"/v1/move", body)
        self.assertEqual(self._status(raw), 400, raw[:200])
        self.assertEqual(json.loads(self._body(raw))["error"]["code"], "invalid")
        self.assertTrue((self.root / "src.txt").exists())

    def test_http_trash_extra_key_is_400(self) -> None:
        body = json.dumps(
            {"source_id": "work", "file_id": "p/src.txt", "extra": 1},
            separators=(",", ":"),
        ).encode("utf-8")
        raw = self._post(b"/v1/trash", body)
        self.assertEqual(self._status(raw), 400, raw[:200])
        self.assertEqual(json.loads(self._body(raw))["error"]["code"], "invalid")
        self.assertTrue((self.root / "src.txt").exists())

    def test_http_unknown_endpoint_is_400(self) -> None:
        body = json.dumps(
            {"source_id": "work", "file_id": "p/src.txt"}, separators=(",", ":")
        ).encode("utf-8")
        raw = self._post(b"/v1/nonexistent", body)
        self.assertEqual(self._status(raw), 400, raw[:200])
        envelope = json.loads(self._body(raw))
        self.assertEqual(envelope["error"]["code"], "invalid")

    def test_http_bad_token_denied(self) -> None:
        bad = _pad_token("http-bad-token-9999")
        raw = self._post(b"/v1/move", self._move_payload(new_name="z.txt"), token=bad)
        self.assertEqual(self._status(raw), 403, raw[:200])
        self.assertEqual(json.loads(self._body(raw))["error"]["code"], "denied")
        self.assertTrue((self.root / "src.txt").exists())

    def test_http_invalid_body_with_bad_token_is_denied(self) -> None:
        # Auth runs before payload validation: a malformed body with a bad
        # token must be 403 denied, never 400 invalid.
        bad = _pad_token("http-bad-token-9999")
        missing_key = json.dumps(
            {"source_id": "work"}, separators=(",", ":")
        ).encode("utf-8")
        extra_key = json.dumps(
            {
                "source_id": "work",
                "file_id": "p/src.txt",
                "dest_parent_id": "root",
                "unexpected": True,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        for body in (missing_key, extra_key):
            raw = self._post(b"/v1/move", body, token=bad)
            self.assertEqual(self._status(raw), 403, raw[:200])
            self.assertEqual(json.loads(self._body(raw))["error"]["code"], "denied")
        self.assertTrue((self.root / "src.txt").exists())

    def test_http_invalid_body_without_move_grant_denied(self) -> None:
        # Authorization runs before payload validation: a malformed body sent
        # by an authenticated token whose grant omits "move" must be denied,
        # never 400 invalid.
        no_move_token = _pad_token("http-no-move-token-007")
        grant = _make_grant("work", self.root, operations=_SAVE_OPS)
        policy = _make_agent("agent2", no_move_token, (grant,))
        adapter = MoveHTTPAdapter((policy,), port=0, socket_timeout=5.0)
        self.addCleanup(lambda: adapter.close(timeout=5.0))
        old_adapter, self.adapter = self.adapter, adapter
        try:
            body = json.dumps(
                {"source_id": "work", "unexpected": True}, separators=(",", ":")
            ).encode("utf-8")
            raw = self._post(b"/v1/move", body, token=no_move_token)
            self.assertEqual(self._status(raw), 403, raw[:200])
            self.assertEqual(json.loads(self._body(raw))["error"]["code"], "denied")
        finally:
            self.adapter = old_adapter
        self.assertTrue((self.root / "src.txt").exists())

    def test_http_move_insufficient_grant_is_denied(self) -> None:
        no_move_token = _pad_token("http-no-move-token-008")
        grant = _make_grant("work", self.root, operations=_SAVE_OPS)
        policy = _make_agent("agent3", no_move_token, (grant,))
        adapter = MoveHTTPAdapter((policy,), port=0, socket_timeout=5.0)
        self.addCleanup(lambda: adapter.close(timeout=5.0))
        old_adapter, self.adapter = self.adapter, adapter
        try:
            raw = self._post(
                b"/v1/move",
                self._move_payload(new_name="denied.txt"),
                token=no_move_token,
            )
            self.assertEqual(self._status(raw), 403, raw[:200])
            envelope = json.loads(self._body(raw))
            self.assertEqual(envelope["error"]["code"], "denied")
            self.assertFalse(envelope["error"]["outcome_uncertain"])
            self.assertFalse(envelope["error"]["retry_safe"])
        finally:
            self.adapter = old_adapter
        self.assertTrue((self.root / "src.txt").exists())
        self.assertFalse((self.root / "denied.txt").exists())

    def test_http_unexpected_exception_is_503(self) -> None:
        # Force an unexpected exception inside the adapter's request handler;
        # the failure must surface as 503 unavailable, retry_safe=False.
        class _Boom(Exception):
            pass

        with unittest.mock.patch.object(
            self.adapter, "_dispatch", side_effect=_Boom("boom")
        ):
            raw = self._post(b"/v1/move", self._move_payload(new_name="x.txt"))
        self.assertEqual(self._status(raw), 503, raw[:200])
        envelope = json.loads(self._body(raw))
        self.assertFalse(envelope.get("ok"))
        self.assertEqual(envelope["error"]["code"], "unavailable")
        self.assertTrue(envelope["error"]["outcome_uncertain"])
        self.assertFalse(envelope["error"]["retry_safe"])
        self.assertTrue((self.root / "src.txt").exists())

    def test_client_roundtrip_move_then_read(self) -> None:
        # Confirms moved bytes remain readable through the inherited read path.
        raw = self._post(b"/v1/move", self._move_payload(new_name="r.txt"))
        self.assertEqual(self._status(raw), 200, raw[:200])
        reply = self._client().read("work", "p/r.txt", offset=0, length=64)
        self.assertEqual(reply.data, b"hello-http")

@unittest.skipUnless(_SERVE_AVAILABLE, SERVE_SKIP)
class ServeConfigTests(_TempRoots):
    """drive_tool.serve accepts move/trash in grants."""

    def _write_config(self, operations) -> Path:
        root = self._mkdir("serve_root")
        token = _pad_token("serve-config-token-01")
        cfg = {
            "port": 0,
            "agents": [
                {
                    "agent_id": "agent",
                    "token_sha256": hashlib.sha256(
                        token.encode("utf-8")
                    ).hexdigest(),
                    "grants": [
                        {
                            "alias": "work",
                            "root": os.path.realpath(str(root)),
                            "operations": list(operations),
                        }
                    ],
                }
            ],
        }
        path = self.base_dir / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def test_config_accepts_move_and_trash(self) -> None:
        path = self._write_config(["list", "find", "read", "save", "move", "trash"])
        port, agents = serve_module.load_config(str(path))
        self.assertEqual(port, 0)
        self.assertEqual(len(agents), 1)
        self.assertEqual(
            agents[0].grants[0].operations,
            frozenset({"list", "find", "read", "save", "move", "trash"}),
        )

    def test_config_rejects_unknown_operation(self) -> None:
        path = self._write_config(["list", "delete"])
        with self.assertRaises(serve_module.ConfigError):
            serve_module.load_config(str(path))

    def test_operations_constant_includes_move_and_trash(self) -> None:
        self.assertIn("move", serve_module._OPERATIONS)
        self.assertIn("trash", serve_module._OPERATIONS)

if __name__ == "__main__":
    unittest.main()
