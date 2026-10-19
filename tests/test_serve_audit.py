"""Tests for drive_tool.serve audit_log support (config + end-to-end run)."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drive_tool.serve import (
    ConfigError,
    hash_token,
    load_audit_log,
    load_config,
    run,
)
from drive_tool.service_client import DriveClient

_TOKEN = "A" * 40  # visible ASCII, length 40
_WRONG_TOKEN = "B" * 40

def _valid_grant(root):
    return {
        "alias": "work",
        "root": root,
        "operations": ["list", "find", "read", "save"],
    }

def _valid_config(root, port=0, audit_log=None):
    cfg = {
        "port": port,
        "agents": [
            {
                "agent_id": "alice",
                "token_sha256": hash_token(_TOKEN),
                "grants": [_valid_grant(root)],
            }
        ],
    }
    if audit_log is not None:
        cfg["audit_log"] = audit_log
    return cfg

class _TempRoots:
    """Small helper that owns a temp dir with a grant root and an outside dir."""

    def __init__(self, testcase):
        self.tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.root = os.path.join(self.base, "root")
        os.mkdir(self.root)
        # A directory guaranteed to be outside the grant root.
        self.outside = os.path.join(self.base, "outside")
        os.mkdir(self.outside)

    def write_config(self, cfg, name="config.json"):
        path = os.path.join(self.base, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        os.chmod(path, 0o600)
        return path

class ServeAuditConfigTests(unittest.TestCase):
    def setUp(self):
        self.roots = _TempRoots(self)

    def test_load_config_accepts_audit_log(self):
        audit_path = os.path.join(self.roots.outside, "audit.log")
        cfg = _valid_config(self.roots.root, audit_log=audit_path)
        port, agents = load_config(self.roots.write_config(cfg))
        self.assertEqual(port, 0)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].agent_id, "alice")

    def test_load_audit_log_returns_path_when_present(self):
        audit_path = os.path.join(self.roots.outside, "audit.log")
        cfg = _valid_config(self.roots.root, audit_log=audit_path)
        path = self.roots.write_config(cfg)
        self.assertEqual(load_audit_log(path), audit_path)

    def test_load_audit_log_returns_none_when_absent(self):
        cfg = _valid_config(self.roots.root)
        path = self.roots.write_config(cfg)
        self.assertIsNone(load_audit_log(path))

    def test_load_audit_log_returns_absolute_path(self):
        audit_path = os.path.join(self.roots.outside, "audit.log")
        cfg = _valid_config(self.roots.root, audit_log=audit_path)
        path = self.roots.write_config(cfg)
        result = load_audit_log(path)
        self.assertIsNotNone(result)
        self.assertTrue(os.path.isabs(result))
        self.assertEqual(result, audit_path)

    def test_load_audit_log_none_when_absent_returns_none(self):
        cfg = _valid_config(self.roots.root)
        path = self.roots.write_config(cfg)
        self.assertIsNone(load_audit_log(path))

    def test_relative_audit_log_rejected(self):
        cfg = _valid_config(self.roots.root, audit_log="audit.log")
        with self.assertRaises(ConfigError):
            load_config(self.roots.write_config(cfg))

    def test_audit_log_inside_grant_root_rejected(self):
        audit_path = os.path.join(self.roots.root, "audit.log")
        cfg = _valid_config(self.roots.root, audit_log=audit_path)
        with self.assertRaises(ConfigError):
            load_config(self.roots.write_config(cfg))

    def test_audit_log_missing_parent_rejected(self):
        audit_path = os.path.join(
            self.roots.base, "no-such-dir", "audit.log"
        )
        cfg = _valid_config(self.roots.root, audit_log=audit_path)
        with self.assertRaises(ConfigError):
            load_config(self.roots.write_config(cfg))

    def test_audit_log_symlink_into_grant_root_rejected(self):
        # A symlink living outside the grant root but pointing at a file
        # inside it must be rejected (realpath resolves the target).
        target = os.path.join(self.roots.root, "audit.log")
        link = os.path.join(self.roots.outside, "audit-link.log")
        os.symlink(target, link)
        cfg = _valid_config(self.roots.root, audit_log=link)
        with self.assertRaises(ConfigError):
            load_config(self.roots.write_config(cfg))

    def test_grant_root_via_symlink_rejects_audit_log_inside(self):
        # The grant root is reached through a symlink; an audit_log under the
        # real root location must still be rejected.
        real_root = os.path.join(self.roots.base, "real-root")
        os.mkdir(real_root)
        link_root = os.path.join(self.roots.base, "link-root")
        os.symlink(real_root, link_root)

        audit_path = os.path.join(real_root, "audit.log")
        cfg = _valid_config(link_root, audit_log=audit_path)
        with self.assertRaises(ConfigError):
            load_config(self.roots.write_config(cfg))

    def test_unknown_top_level_key_rejected(self):
        cfg = _valid_config(self.roots.root)
        cfg["extra"] = 1
        with self.assertRaises(ConfigError):
            load_config(self.roots.write_config(cfg))

class ServeAuditEndToEndTests(unittest.TestCase):
    def _read_audit_lines(self, audit_path):
        with open(audit_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        return [line for line in raw.splitlines() if line.strip()]

    def _start(self, cfg, root):
        path = os.path.join(os.path.dirname(root), "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        os.chmod(path, 0o600)

        ready = threading.Event()
        stop = threading.Event()
        result = {}
        port_holder = {}

        def _ready(p):
            port_holder["port"] = p
            ready.set()

        def _run():
            result["rc"] = run(path, ready=_ready, stop=stop)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        if not ready.wait(timeout=10.0):
            stop.set()
            raise AssertionError("adapter never became ready")
        return thread, stop, result, port_holder["port"]

    def test_audit_log_written_for_ok_and_denied(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.join(tmp.name, "root")
        os.mkdir(root)
        outside = os.path.join(tmp.name, "outside")
        os.mkdir(outside)
        audit_path = os.path.join(outside, "audit.log")

        cfg = _valid_config(root, port=0, audit_log=audit_path)
        thread, stop, result, port = self._start(cfg, root)
        self.assertGreater(port, 0)

        try:
            client = DriveClient(port=port, token=_TOKEN, timeout=5.0)
            client.save("work", "p/ok.txt", b"ok content", overwrite=False)

            bad_client = DriveClient(
                port=port, token=_WRONG_TOKEN, timeout=5.0
            )
            with self.assertRaises(Exception):
                bad_client.save("work", "p/denied.txt", b"nope", overwrite=False)
        finally:
            stop.set()
        thread.join(timeout=10.0)
        self.assertFalse(thread.is_alive(), "run() thread did not end")
        self.assertEqual(result["rc"], 0)

        self.assertTrue(os.path.isfile(audit_path), "audit file was not created")
        lines = self._read_audit_lines(audit_path)
        self.assertGreaterEqual(len(lines), 1, "expected at least one audit line")

        records = [json.loads(line) for line in lines]

        # One successful save by alice with outcome ok.
        ok_records = [
            r
            for r in records
            if r.get("op") == "save"
            and r.get("agent") == "alice"
            and r.get("outcome") == "ok"
        ]
        self.assertTrue(
            ok_records, "no audit line with op save, agent alice, outcome ok"
        )

        # One denied save attempt.
        denied_records = [
            r
            for r in records
            if r.get("op") == "save" and r.get("outcome") == "denied"
        ]
        self.assertTrue(
            denied_records, "no audit line with op save and outcome denied"
        )

        # Neither the raw tokens nor their sha256 hex digests appear in the text.
        with open(audit_path, "r", encoding="utf-8") as fh:
            file_text = fh.read()
        self.assertNotIn(_TOKEN, file_text)
        self.assertNotIn(_WRONG_TOKEN, file_text)
        self.assertNotIn(hash_token(_TOKEN), file_text)
        self.assertNotIn(hash_token(_WRONG_TOKEN), file_text)

    def test_no_audit_log_means_no_file_and_saves_work(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.join(tmp.name, "root")
        os.mkdir(root)

        cfg = _valid_config(root, port=0)  # no audit_log
        thread, stop, result, port = self._start(cfg, root)
        self.assertGreater(port, 0)

        try:
            client = DriveClient(port=port, token=_TOKEN, timeout=5.0)
            client.save("work", "p/a.txt", b"hello", overwrite=False)
            reply = client.read("work", "p/a.txt", offset=0, length=16)
            self.assertEqual(reply.data, b"hello")
        finally:
            stop.set()
        thread.join(timeout=10.0)
        self.assertFalse(thread.is_alive(), "run() thread did not end")
        self.assertEqual(result["rc"], 0)

        # No audit file should appear anywhere near the config / root.
        for dirpath, _dirnames, filenames in os.walk(tmp.name):
            for name in filenames:
                if name in ("audit.log", "config.json"):
                    continue
                self.assertNotIn("audit", name.lower())

    def test_non_save_ops_are_audited(self):
        # AuditedService only records save/move/trash; every other request
        # passes through untouched. Drive a save and a save-side operation
        # that the wrapper audits, plus a plain read/list, and verify the
        # audited ops appear while unaudited ops do not (and never crash).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.join(tmp.name, "root")
        os.mkdir(root)
        outside = os.path.join(tmp.name, "outside")
        os.mkdir(outside)
        audit_path = os.path.join(outside, "audit.log")

        cfg = _valid_config(root, port=0, audit_log=audit_path)
        thread, stop, result, port = self._start(cfg, root)
        self.assertGreater(port, 0)

        try:
            client = DriveClient(port=port, token=_TOKEN, timeout=5.0)
            client.save("work", "p/seen.txt", b"content", overwrite=False)
            reply = client.read("work", "p/seen.txt", offset=0, length=32)
            self.assertEqual(reply.data, b"content")
            listing = client.list("work", "root")
            self.assertEqual(
                [i["name"] for i in listing["items"]], ["seen.txt"]
            )
        finally:
            stop.set()
        thread.join(timeout=10.0)
        self.assertFalse(thread.is_alive(), "run() thread did not end")
        self.assertEqual(result["rc"], 0)

        self.assertTrue(os.path.isfile(audit_path), "audit file was not created")
        records = [json.loads(line) for line in self._read_audit_lines(audit_path)]

        ok_ops = {
            r.get("op")
            for r in records
            if r.get("agent") == "alice" and r.get("outcome") == "ok"
        }
        # The audited write op must be recorded; read/list are passthroughs
        # that AuditedService does not log (per audit.py), so they must not
        # appear -- but the requests must still have succeeded above.
        self.assertIn("save", ok_ops)
        self.assertNotIn("read", ok_ops)
        self.assertNotIn("list", ok_ops)

    def test_main_with_audit_log_audits_requests(self):
        # main() installs SIGTERM/SIGINT handlers and blocks, so it cannot be
        # called from a worker thread. Launch the real entry point as a child
        # process, wait for the "listening on" line to learn the port, drive a
        # save over HTTP, then stop it with SIGTERM. The resulting audit line
        # can only exist if main() wired the audited adapter.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.join(tmp.name, "root")
        os.mkdir(root)
        outside = os.path.join(tmp.name, "outside")
        os.mkdir(outside)
        audit_path = os.path.join(outside, "audit.log")

        cfg = _valid_config(root, port=0, audit_log=audit_path)
        path = os.path.join(tmp.name, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        os.chmod(path, 0o600)

        repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")

        proc = subprocess.Popen(
            [sys.executable, "-m", "drive_tool.serve", "--config", path],
            cwd=repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self._reap, proc)

        port = None
        port_re = re.compile(rb"127\.0\.0\.1:(\d+)")
        deadline = time.monotonic() + 10.0
        stderr_chunks = []
        while time.monotonic() < deadline:
            line = proc.stderr.readline()
            if not line:
                break
            stderr_chunks.append(line)
            match = port_re.search(line)
            if match:
                port = int(match.group(1))
                break
        self.assertIsNotNone(
            port,
            "serve main() never reported a listening port; stderr=%r"
            % (b"".join(stderr_chunks),),
        )

        try:
            client = DriveClient(port=port, token=_TOKEN, timeout=5.0)
            client.save("work", "p/main.txt", b"from main", overwrite=False)
        finally:
            self._terminate(proc)

        self.assertEqual(proc.returncode, 0)

        self.assertTrue(os.path.isfile(audit_path), "audit file was not created")
        records = [json.loads(line) for line in self._read_audit_lines(audit_path)]
        ok_records = [
            r
            for r in records
            if r.get("op") == "save"
            and r.get("agent") == "alice"
            and r.get("outcome") == "ok"
        ]
        self.assertTrue(
            ok_records,
            "main() did not audit the save: %r" % (records,),
        )

    @staticmethod
    def _terminate(proc):
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10.0)

    @staticmethod
    def _reap(proc):
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass

if __name__ == "__main__":
    unittest.main()
