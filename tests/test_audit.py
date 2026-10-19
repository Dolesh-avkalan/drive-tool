"""Tests for drive_tool.audit (record dt17-code-1).

Fixtures mirror ``tests/test_service_move.py``: grants are built with
``os.path.realpath(str(root))`` because ``SourceGrant.root`` rejects a
``pathlib.Path``, agent tokens are 32..256 visible-ASCII strings, and the
service is a real ``MovableFileService`` over a temporary synthetic root.

Every expected value comes from the code record:

* ``AuditLog.record`` writes one JSON line per call: keys sorted, no spaces,
  a ``ts`` field added.
* ``AuditedService`` records ``op``, ``agent`` (``agent_id`` whose
  ``token_sha256`` matches, else ``"unknown"``), ``source_id``, ``file_id``,
  ``dest_parent_id``/``new_name`` for move, ``bytes`` for save, and
  ``outcome`` (``"ok"`` or the ``ServiceError`` code).
* The wrapped error is re-raised unchanged.
* Logging failures are swallowed and counted in ``audit_failures``.

The move service validates the ``new_name`` *string shape* before it attempts
the underlying local move, and a name that is not a single path component is
rejected with ``ServiceError.code == "denied"``.  The over-long-name test
therefore passes a 5000-byte name, which the storage layer refuses (a single
name component over 255 bytes), and asserts the audit line stays one bounded
JSON line with a truncated string ``new_name`` and ``outcome`` denied.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from drive_tool.audit import AuditLog, AuditedService
from drive_tool.file_service import SERVICE_MAX_DEPTH, ServiceError
from drive_tool.file_service_move import MovableFileService
from drive_tool.service_policy import AgentPolicy, SourceGrant
from drive_tool.storage import ROOT_ID

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

def _long_component(length: int) -> str:
    # One path component that is itself very long (still a single component,
    # so the service's shape check accepts it).
    return "n" * length

class _TempRoots(unittest.TestCase):
    """Temporary synthetic roots, mirroring test_service_move.py."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="audit_svc_")
        self.base_dir = Path(self._tmp.name)
        self.addCleanup(self._rm)

    def _rm(self) -> None:
        self._tmp.cleanup()

    def _mkdir(self, rel: str) -> Path:
        path = self.base_dir / rel
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

class _AuditFixture(_TempRoots):
    """A real MovableFileService plus an AuditedService over one root."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self._mkdir("root")
        self.log_path = str(self.base_dir / "audit.log")
        self.token = _pad_token("audit-agent-token-0001")
        self.agent_id = "agent-audit"
        grant = _make_grant("work", self.root, operations=_ALL_OPS)
        self.policies = (_make_agent(self.agent_id, self.token, (grant,)),)
        self._service = MovableFileService(self.policies)
        self.addCleanup(self._service.close)
        self.log = AuditLog(self.log_path, max_bytes=1048576)
        self.service = AuditedService(self._service, self.policies, self.log)

    def _lines(self) -> list[dict]:
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        return [json.loads(line) for line in text.splitlines() if line]

    def _raw_log(self) -> bytes:
        if not os.path.exists(self.log_path):
            return b""
        with open(self.log_path, "rb") as fh:
            return fh.read()

class SaveMoveTrashRecordTests(_AuditFixture):
    """Each audited operation writes exactly one correctly-shaped line."""

    def test_save_records_one_line(self) -> None:
        data = b"hello-audit"
        result = self.service.save(self.token, "work", "p/a.txt", data)
        self.assertEqual(result["ref"]["file_id"], "p/a.txt")

        lines = self._lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["op"], "save")
        self.assertEqual(rec["agent"], self.agent_id)
        self.assertEqual(rec["source_id"], "work")
        self.assertEqual(rec["file_id"], "p/a.txt")
        self.assertEqual(rec["outcome"], "ok")
        self.assertEqual(rec["bytes"], len(data))
        self.assertIn("ts", rec)
        self.assertTrue(rec["ts"].endswith("Z"))

    def test_move_records_one_line(self) -> None:
        self._write(self.root / "src.txt", b"payload")
        result = self.service.move(
            self.token, "work", "p/src.txt", "root", "renamed.txt"
        )
        self.assertEqual(result["ref"]["file_id"], "p/renamed.txt")

        lines = self._lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["op"], "move")
        self.assertEqual(rec["agent"], self.agent_id)
        self.assertEqual(rec["source_id"], "work")
        self.assertEqual(rec["file_id"], "p/src.txt")
        self.assertEqual(rec["dest_parent_id"], "root")
        self.assertEqual(rec["new_name"], "renamed.txt")
        self.assertEqual(rec["outcome"], "ok")
        self.assertNotIn("bytes", rec)

    def test_move_without_new_name_records_none(self) -> None:
        self._write(self.root / "keep.txt", b"keep")
        self._mkdir("root/sub")
        self.service.move(self.token, "work", "p/keep.txt", "p/sub")
        rec = self._lines()[0]
        self.assertEqual(rec["op"], "move")
        self.assertIsNone(rec["new_name"])
        self.assertEqual(rec["dest_parent_id"], "p/sub")
        self.assertEqual(rec["outcome"], "ok")

    def test_trash_records_one_line(self) -> None:
        self._write(self.root / "gone.txt", b"TRASH-ME")
        result = self.service.trash(self.token, "work", "p/gone.txt")
        self.assertTrue(result["trashed"])

        lines = self._lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["op"], "trash")
        self.assertEqual(rec["agent"], self.agent_id)
        self.assertEqual(rec["source_id"], "work")
        self.assertEqual(rec["file_id"], "p/gone.txt")
        self.assertEqual(rec["outcome"], "ok")

    def test_lines_are_sorted_compact_and_z_terminated(self) -> None:
        self.service.save(self.token, "work", "p/a.txt", b"x")
        raw = self._raw_log()
        self.assertTrue(raw.endswith(b"\n"))
        line = raw.splitlines()[0].decode("utf-8")
        self.assertNotIn(": ", line)
        self.assertNotIn(", ", line)
        obj = json.loads(line)
        self.assertEqual(line, json.dumps(obj, sort_keys=True, separators=(",", ":")))

class FailureRecordTests(_AuditFixture):
    """Failures record the error code, unknown agent, and re-raise unchanged."""

    def test_wrong_token_move_denied_records_and_reraises(self) -> None:
        self._write(self.root / "x.txt", b"x")
        bad = _pad_token("wrong-token-value-999")
        with self.assertRaises(ServiceError) as ctx:
            self.service.move(bad, "work", "p/x.txt", "root", "y.txt")
        self.assertEqual(ctx.exception.code, "denied")

        lines = self._lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["op"], "move")
        self.assertEqual(rec["agent"], "unknown")
        self.assertEqual(rec["outcome"], "denied")
        self.assertEqual(rec["file_id"], "p/x.txt")
        self.assertTrue((self.root / "x.txt").exists())

    def test_wrong_token_save_denied(self) -> None:
        bad = _pad_token("wrong-token-value-998")
        with self.assertRaises(ServiceError) as ctx:
            self.service.save(bad, "work", "p/a.txt", b"data")
        self.assertEqual(ctx.exception.code, "denied")
        rec = self._lines()[0]
        self.assertEqual(rec["op"], "save")
        self.assertEqual(rec["agent"], "unknown")
        self.assertEqual(rec["outcome"], "denied")
        self.assertEqual(rec["bytes"], 4)

    def test_wrong_token_trash_denied(self) -> None:
        self._write(self.root / "x.txt", b"x")
        bad = _pad_token("wrong-token-value-997")
        with self.assertRaises(ServiceError) as ctx:
            self.service.trash(bad, "work", "p/x.txt")
        self.assertEqual(ctx.exception.code, "denied")
        rec = self._lines()[0]
        self.assertEqual(rec["op"], "trash")
        self.assertEqual(rec["agent"], "unknown")
        self.assertEqual(rec["outcome"], "denied")

    def test_move_exists_records_code(self) -> None:
        self._write(self.root / "src.txt", b"SRC")
        self._write(self.root / "taken.txt", b"TGT")
        with self.assertRaises(ServiceError) as ctx:
            self.service.move(self.token, "work", "p/src.txt", "root", "taken.txt")
        self.assertEqual(ctx.exception.code, "exists")
        rec = self._lines()[0]
        self.assertEqual(rec["outcome"], "exists")
        self.assertEqual(rec["agent"], self.agent_id)
        self.assertEqual(rec["new_name"], "taken.txt")

class RedactionTests(_AuditFixture):
    """The token and file payload never reach the log."""

    def test_token_and_bytes_absent(self) -> None:
        payload = b"UNIQUE-PAYLOAD-MARKER-424242"
        self.service.save(self.token, "work", "p/secret.txt", payload)
        self._write(self.root / "m.txt", b"MOVE-BYTES-MARKER-777")
        self.service.move(self.token, "work", "p/m.txt", "root", "n.txt")
        self.service.trash(self.token, "work", "p/n.txt")
        raw = self._raw_log()
        self.assertEqual(len(self._lines()), 3)
        self.assertNotIn(self.token.encode("ascii"), raw)
        self.assertNotIn(payload, raw)
        self.assertNotIn(b"MOVE-BYTES-MARKER-777", raw)

    def test_token_absent_on_denied_call(self) -> None:
        bad = _pad_token("leaky-token-value-123")
        with self.assertRaises(ServiceError):
            self.service.save(bad, "work", "p/a.txt", b"d")
        self.assertNotIn(bad.encode("ascii"), self._raw_log())

class PassthroughTests(_AuditFixture):
    """Unaudited calls pass through and write nothing."""

    def test_list_and_read_pass_through(self) -> None:
        self._write(self.root / "a.txt", b"contents")
        self.service.save(self.token, "work", "p/b.txt", b"bee")
        # Clear the line the save above wrote, so only passthrough is measured.
        os.remove(self.log_path)

        listing = self.service.list(self.token, "work", ROOT_ID)
        names = {i["name"] for i in listing["items"]}
        self.assertIn("a.txt", names)
        self.assertIn("b.txt", names)

        reply = self.service.read(self.token, "work", "p/a.txt", offset=0, length=64)
        self.assertEqual(reply.data, b"contents")

        self.assertEqual(self._lines(), [])

    def test_getattr_passes_through(self) -> None:
        self.assertTrue(hasattr(self.service, "close"))
        self.assertEqual(self.service.close.__self__, self._service)

class RotationTests(_TempRoots):
    """A small max_bytes rotates the log to ``.1``."""

    def test_rotation_to_dot_one(self) -> None:
        log_path = str(self.base_dir / "rot.log")
        log = AuditLog(log_path, max_bytes=200)
        log.record(op="save", outcome="ok", payload="A" * 120)
        log.record(op="save", outcome="ok", payload="B" * 120)

        self.assertTrue(os.path.exists(log_path + ".1"))
        self.assertTrue(os.path.exists(log_path))
        with open(log_path + ".1", "r", encoding="utf-8") as fh:
            older = [json.loads(line) for line in fh.read().splitlines()]
        with open(log_path, "r", encoding="utf-8") as fh:
            newer = [json.loads(line) for line in fh.read().splitlines()]
        self.assertEqual(len(older), 1)
        self.assertEqual(len(newer), 1)
        self.assertEqual(older[0]["payload"], "A" * 120)
        self.assertEqual(newer[0]["payload"], "B" * 120)

    def test_rotation_replaces_older_dot_one(self) -> None:
        log_path = str(self.base_dir / "rot2.log")
        log = AuditLog(log_path, max_bytes=200)
        for tag in ("A", "B", "C"):
            log.record(op="save", outcome="ok", payload=tag * 120)

        # Only the two most recent lines survive: the .1 holds "B", the live
        # file holds "C", and "A" has been replaced away.
        with open(log_path + ".1", "r", encoding="utf-8") as fh:
            older = [json.loads(line) for line in fh.read().splitlines()]
        with open(log_path, "r", encoding="utf-8") as fh:
            newer = [json.loads(line) for line in fh.read().splitlines()]
        self.assertEqual([r["payload"][0] for r in older], ["B"])
        self.assertEqual([r["payload"][0] for r in newer], ["C"])

class LongLineTests(_AuditFixture):
    """An over-long line stays one valid JSON line under 4096 bytes."""

    def test_overlong_new_name_single_bounded_line(self) -> None:
        # A 5000-byte new_name is refused by the storage layer (a single name
        # component over 255 bytes), so the move raises ServiceError with code
        # "denied".  The audit line must stay a single JSON line under 4096
        # bytes with the long new_name string truncated.
        self._write(self.root / "x.txt", b"x")
        long_name = _long_component(5000)
        before = self._raw_log()
        with self.assertRaises(ServiceError) as ctx:
            self.service.move(self.token, "work", "p/x.txt", "root", long_name)
        self.assertEqual(ctx.exception.code, "denied")

        raw = self._raw_log()
        new_raw = raw[len(before):]
        new_lines = [line for line in new_raw.split(b"\n") if line]
        self.assertEqual(len(new_lines), 1, new_raw[:80])
        line = new_lines[0]
        self.assertLessEqual(len(line) + 1, 4096)
        rec = json.loads(line.decode("utf-8"))
        self.assertEqual(rec["op"], "move")
        self.assertEqual(rec["outcome"], "denied")
        self.assertEqual(rec["agent"], self.agent_id)
        self.assertIsInstance(rec["new_name"], str)
        self.assertLess(len(rec["new_name"]), len(long_name))

    def test_overlong_line_still_one_valid_json_line_on_denied(self) -> None:
        # Same bound holds on the failure path: an invalid new_name shape is
        # rejected with "denied" and the (long) value is still bounded in the
        # one-line record.
        self._write(self.root / "x.txt", b"x")
        bad = _pad_token("long-line-bad-token-01")
        long_name = "q" * 8000
        with self.assertRaises(ServiceError) as ctx:
            self.service.move(bad, "work", "p/x.txt", "root", long_name)
        self.assertEqual(ctx.exception.code, "denied")

        raw = self._raw_log()
        self.assertEqual(raw.count(b"\n"), 1, raw[:80])
        self.assertLessEqual(len(raw), 4096)
        rec = json.loads(raw.decode("utf-8"))
        self.assertEqual(rec["op"], "move")
        self.assertEqual(rec["outcome"], "denied")
        self.assertEqual(rec["agent"], "unknown")
        self.assertIsInstance(rec["new_name"], str)
        self.assertLess(len(rec["new_name"]), len(long_name))

class UnwritableLogTests(_AuditFixture):
    """A broken log path must not change the operation's result."""

    def test_removed_log_directory_swallowed_and_counted(self) -> None:
        log_dir = self.base_dir / "logdir"
        log_dir.mkdir()
        log_path = str(log_dir / "audit.log")
        log = AuditLog(log_path)
        service = AuditedService(self._service, self.policies, log)

        # Remove the directory so os.open on the log path fails.
        os.rmdir(log_dir)

        result = service.save(self.token, "work", "p/ok.txt", b"survives")
        self.assertEqual(result["ref"]["file_id"], "p/ok.txt")
        self.assertEqual((self.root / "ok.txt").read_bytes(), b"survives")
        self.assertEqual(service.audit_failures, 1)

        self._write(self.root / "m.txt", b"m")
        moved = service.move(self.token, "work", "p/m.txt", "root", "n.txt")
        self.assertEqual(moved["ref"]["file_id"], "p/n.txt")
        self.assertEqual(service.audit_failures, 2)

        trashed = service.trash(self.token, "work", "p/n.txt")
        self.assertTrue(trashed["trashed"])
        self.assertEqual(service.audit_failures, 3)

    def test_error_still_reraised_when_log_broken(self) -> None:
        log_dir = self.base_dir / "logdir2"
        log_dir.mkdir()
        log = AuditLog(str(log_dir / "audit.log"))
        service = AuditedService(self._service, self.policies, log)
        os.rmdir(log_dir)

        self._write(self.root / "x.txt", b"x")
        bad = _pad_token("broken-log-token-5555")
        with self.assertRaises(ServiceError) as ctx:
            service.move(bad, "work", "p/x.txt", "root", "y.txt")
        self.assertEqual(ctx.exception.code, "denied")
        self.assertTrue((self.root / "x.txt").exists())
        self.assertEqual(service.audit_failures, 1)

class FileModeTests(_AuditFixture):
    """The log file is created with mode 0o600."""

    @unittest.skipIf(
        os.geteuid() == 0 if hasattr(os, "geteuid") else False,
        "permission mode is not enforced for root",
    )
    def test_mode_is_0600(self) -> None:
        self.service.save(self.token, "work", "p/a.txt", b"x")
        self.assertTrue(os.path.exists(self.log_path))
        mode = os.stat(self.log_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_mode_is_0600_unconditionally(self) -> None:
        # os.open's mode argument is applied regardless of euid, so this is
        # safe to assert even as root.
        self.service.save(self.token, "work", "p/a.txt", b"x")
        mode = os.stat(self.log_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

class AgentResolutionTests(_TempRoots):
    """Agent id is resolved from the matching token digest only."""

    def test_second_agent_resolves_correctly(self) -> None:
        root = self._mkdir("root")
        tok_a = _pad_token("multi-agent-token-aaaa")
        tok_b = _pad_token("multi-agent-token-bbbb")
        grant = _make_grant("work", root, operations=_ALL_OPS)
        policies = (
            _make_agent("agent-a", tok_a, (grant,)),
            _make_agent("agent-b", tok_b, (grant,)),
        )
        service = MovableFileService(policies)
        self.addCleanup(service.close)
        log_path = str(self.base_dir / "multi.log")
        audited = AuditedService(service, policies, AuditLog(log_path))

        audited.save(tok_a, "work", "p/a.txt", b"a")
        audited.save(tok_b, "work", "p/b.txt", b"b")

        with open(log_path, "r", encoding="utf-8") as fh:
            recs = [json.loads(line) for line in fh.read().splitlines()]
        self.assertEqual([r["agent"] for r in recs], ["agent-a", "agent-b"])

class DepthBoundaryAuditTests(_AuditFixture):
    """A too-deep move records outcome invalid without touching the file."""

    def test_depth_invalid_recorded(self) -> None:
        too_deep = (
            "p/"
            + "/".join("d%d" % i for i in range(SERVICE_MAX_DEPTH + 1))
            + "/f.txt"
        )
        with self.assertRaises(ServiceError) as ctx:
            self.service.move(self.token, "work", too_deep, "root", "g.txt")
        self.assertEqual(ctx.exception.code, "invalid")
        rec = self._lines()[0]
        self.assertEqual(rec["outcome"], "invalid")
        self.assertEqual(rec["file_id"], too_deep)

if __name__ == "__main__":
    unittest.main()
