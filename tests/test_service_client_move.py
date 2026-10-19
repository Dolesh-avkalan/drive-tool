"""Tests for drive_tool.service_client_move (MODULE-006).

Move-side loopback client: ``MoveClient.move`` and ``MoveClient.trash``.

Two tiers, matching tests/test_service_client.py:

* Argument validation runs *before* any network I/O; those tests patch
  ``_open`` to fail if it is ever called.
* Wire behaviour is exercised against a real ``MoveHTTPAdapter`` served on
  loopback, built exactly as tests/test_service_move.py builds it: a
  ``SourceGrant`` (root as ``os.path.realpath`` string), an ``AgentPolicy``
  whose ``token_sha256`` is the raw 32-byte SHA-256 digest, then
  ``MoveHTTPAdapter((policy,), port=0, socket_timeout=5.0)``. A loopback
  service is not the real Drive service; these tests do not establish
  production acceptance.

Expected codes and replies come from the code record (MoveClient dispatches
with ``write_op=True``; service errors surface as ``DriveClientError``):
``exists`` / ``denied`` from ``file_service_move``, keys as validated in
``http_move.py``.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest

from drive_tool.http_move import MoveHTTPAdapter
from drive_tool.service_client import DriveClientError
from drive_tool.service_client_move import MoveClient
from drive_tool.service_policy import AgentPolicy, SourceGrant

IS_ROOT = os.geteuid() == 0

# ---------------------------------------------------------------------------
# Fixtures (same construction pattern as tests/test_service_move.py)
# ---------------------------------------------------------------------------

_TOKEN_PAD = "0123456789abcdef0123456789abcdef"
_ALL_OPS = frozenset({"list", "find", "read", "save", "move", "trash"})
_SAVE_OPS = frozenset({"list", "find", "read", "save"})

def _pad_token(base: str) -> str:
    # service_policy requires a 32..256 char visible-ASCII bearer token.
    return (base + _TOKEN_PAD)[:40]

def _token_digest(token: str) -> bytes:
    # AgentPolicy.token_sha256 is the raw 32-byte SHA-256 digest, not hex.
    return hashlib.sha256(token.encode("utf-8")).digest()

def _make_grant(alias, root, *, operations=_ALL_OPS):
    # SourceGrant.root must be a str; Path is rejected with TypeError.
    return SourceGrant(
        alias=alias,
        root=os.path.realpath(str(root)),
        operations=frozenset(operations),
    )

def _make_agent(agent_id, token, grants):
    return AgentPolicy(
        agent_id=agent_id,
        token_sha256=_token_digest(token),
        grants=tuple(grants),
    )

def _client(port, token, timeout=5.0):
    return MoveClient(port=port, token=token, timeout=timeout)

def _exploding_open(client):
    def _open():
        raise AssertionError("network must not be used")

    client._open = _open

# ---------------------------------------------------------------------------
# Argument validation happens before any network
# ---------------------------------------------------------------------------

class TestMoveValidation(unittest.TestCase):
    def setUp(self):
        # Port 1 is unserved; the seam must never be reached anyway.
        self.client = _client(1, _pad_token("validation-token-0001"))
        _exploding_open(self.client)

    def _assert_before_network(self, exc_type, fn, *args, **kwargs):
        with self.assertRaises(exc_type):
            fn(*args, **kwargs)

    def test_move_bad_source_id(self):
        c = self.client
        self._assert_before_network(TypeError, c.move, 123, "p/dir-a/a.txt", "p/dir-b")
        self._assert_before_network(ValueError, c.move, "", "p/dir-a/a.txt", "p/dir-b")
        self._assert_before_network(
            ValueError, c.move, "a" * 65, "p/dir-a/a.txt", "p/dir-b"
        )
        self._assert_before_network(
            ValueError, c.move, "bad id", "p/dir-a/a.txt", "p/dir-b"
        )

    def test_move_root_as_file_id_rejected(self):
        # file_id is allow_root=False; "root" is not a movable file.
        self._assert_before_network(
            ValueError, self.client.move, "src", "root", "p/dir-b"
        )

    def test_move_bad_dest_parent_id(self):
        c = self.client
        self._assert_before_network(TypeError, c.move, "src", "p/dir-a/a.txt", 5)
        self._assert_before_network(
            ValueError, c.move, "src", "p/dir-a/a.txt", "not-canonical"
        )

    def test_move_new_name_non_str_rejected(self):
        c = self.client
        for bad in (123, b"b.txt", [], object()):
            self._assert_before_network(
                TypeError, c.move, "src", "p/dir-a/a.txt", "p/dir-b", new_name=bad
            )

    def test_move_new_name_empty_rejected(self):
        self._assert_before_network(
            ValueError,
            self.client.move,
            "src",
            "p/dir-a/a.txt",
            "p/dir-b",
            new_name="",
        )

    def test_trash_bad_source_id(self):
        c = self.client
        self._assert_before_network(TypeError, c.trash, 123, "p/dir-a/a.txt")
        self._assert_before_network(ValueError, c.trash, "", "p/dir-a/a.txt")
        self._assert_before_network(ValueError, c.trash, "bad id", "p/dir-a/a.txt")

    def test_trash_root_as_file_id_rejected(self):
        self._assert_before_network(ValueError, self.client.trash, "src", "root")

# ---------------------------------------------------------------------------
# Wire behaviour against a real MoveHTTPAdapter on loopback
# ---------------------------------------------------------------------------

class TestMoveWire(unittest.TestCase):
    _TOKEN = _pad_token("move-client-token-0001")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="move_client_")
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "root")
        os.makedirs(os.path.join(self.root, "a"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "b"), exist_ok=True)
        with open(os.path.join(self.root, "a", "x.txt"), "wb") as fh:
            fh.write(b"X-BYTES")
        with open(os.path.join(self.root, "a", "taken.txt"), "wb") as fh:
            fh.write(b"TAKEN-BYTES")

    def _make_adapter(self, *, operations=_ALL_OPS, token=None):
        token = token or self._TOKEN
        grant = _make_grant("work", self.root, operations=operations)
        policy = _make_agent("agent", token, (grant,))
        adapter = MoveHTTPAdapter((policy,), port=0, socket_timeout=5.0)
        self.addCleanup(lambda: adapter.close())
        return adapter

    def test_move_with_rename(self):
        adapter = self._make_adapter()
        client = _client(adapter.port, self._TOKEN)
        reply = client.move(
            "work", "p/a/x.txt", "p/a", new_name="renamed.txt"
        )
        self.assertIsInstance(reply, dict)
        self.assertEqual(
            reply, {"ref": {"source_id": "work", "file_id": "p/a/renamed.txt"}}
        )
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", "renamed.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "a", "x.txt")))
        self.assertEqual(
            client.list("work", "p/a")["items"][0]["name"], "renamed.txt"
        )

    def test_move_into_another_folder(self):
        adapter = self._make_adapter()
        client = _client(adapter.port, self._TOKEN)
        reply = client.move("work", "p/a/x.txt", "p/b")
        self.assertEqual(
            reply, {"ref": {"source_id": "work", "file_id": "p/b/x.txt"}}
        )
        self.assertTrue(os.path.exists(os.path.join(self.root, "b", "x.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "a", "x.txt")))
        listed = [i["name"] for i in client.list("work", "p/b")["items"]]
        self.assertIn("x.txt", listed)

    def test_move_to_grant_root_accepts_root_dest(self):
        # dest_parent_id="root" is allowed (allow_root=True); the file lands
        # at the grant root under its leaf name.
        adapter = self._make_adapter()
        client = _client(adapter.port, self._TOKEN)
        reply = client.move("work", "p/a/x.txt", "root")
        self.assertEqual(
            reply, {"ref": {"source_id": "work", "file_id": "p/x.txt"}}
        )
        self.assertTrue(os.path.exists(os.path.join(self.root, "x.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "a", "x.txt")))
        listed = [i["name"] for i in client.list("work", "root")["items"]]
        self.assertIn("x.txt", listed)

    def test_move_target_exists_is_error_and_leaves_file_untouched(self):
        adapter = self._make_adapter()
        client = _client(adapter.port, self._TOKEN)
        with self.assertRaises(DriveClientError) as ctx:
            client.move("work", "p/a/x.txt", "p/a", new_name="taken.txt")
        self.assertEqual(ctx.exception.code, "exists")
        with open(os.path.join(self.root, "a", "x.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"X-BYTES")
        with open(os.path.join(self.root, "a", "taken.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"TAKEN-BYTES")

    def test_trash_removes_file_from_listing(self):
        adapter = self._make_adapter()
        client = _client(adapter.port, self._TOKEN)
        reply = client.trash("work", "p/a/x.txt")
        self.assertEqual(
            reply, {"ref": {"source_id": "work", "file_id": "p/a/x.txt"}, "trashed": True}
        )
        listed = [i["name"] for i in client.list("work", "p/a")["items"]]
        self.assertNotIn("x.txt", listed)
        self.assertFalse(os.path.exists(os.path.join(self.root, "a", "x.txt")))

    @unittest.skipIf(IS_ROOT, "permission tests fail as root")
    def test_wrong_token_is_denied(self):
        adapter = self._make_adapter()
        bad = _pad_token("wrong-move-token-9999")
        client = _client(adapter.port, bad)
        with self.assertRaises(DriveClientError) as ctx:
            client.move("work", "p/a/x.txt", "p/b")
        self.assertEqual(ctx.exception.code, "denied")
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", "x.txt")))

    @unittest.skipIf(IS_ROOT, "permission tests fail as root")
    def test_grant_without_move_is_denied(self):
        adapter = self._make_adapter(operations=_SAVE_OPS)
        client = _client(adapter.port, self._TOKEN)
        with self.assertRaises(DriveClientError) as ctx:
            client.move("work", "p/a/x.txt", "p/b")
        self.assertEqual(ctx.exception.code, "denied")
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", "x.txt")))

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
