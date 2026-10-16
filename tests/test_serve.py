"""Tests for drive_tool.serve: config validation and end-to-end run."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drive_tool.serve import ConfigError, hash_token, load_config, run
from drive_tool.service_client import DriveClient

_TOKEN = "A" * 40  # visible ASCII, length 40

def _valid_grant(root):
    return {
        "alias": "work",
        "root": root,
        "operations": ["list", "find", "read", "save"],
    }

def _valid_config(root, port=0):
    return {
        "port": port,
        "agents": [
            {
                "agent_id": "alice",
                "token_sha256": hash_token(_TOKEN),
                "grants": [_valid_grant(root)],
            }
        ],
    }

class ServeConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "root")
        os.mkdir(self.root)

    def _write(self, data, name="config.json", mode=0o600):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(data, str):
                fh.write(data)
            else:
                json.dump(data, fh)
        os.chmod(path, mode)
        return path

    def test_valid_config_loads(self):
        path = self._write(_valid_config(self.root))
        port, agents = load_config(path)
        self.assertEqual(port, 0)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0].agent_id, "alice")
        self.assertEqual(
            agents[0].token_sha256, hashlib.sha256(_TOKEN.encode()).digest()
        )
        self.assertEqual(len(agents[0].grants), 1)
        self.assertEqual(agents[0].grants[0].alias, "work")
        self.assertEqual(agents[0].grants[0].operations, frozenset(
            {"list", "find", "read", "save"}
        ))

    def test_hash_token_matches_hashlib(self):
        self.assertEqual(
            hash_token("hello"), hashlib.sha256(b"hello").hexdigest()
        )

    def test_duplicate_json_keys_rejected(self):
        path = self._write('{"port":0,"port":0,"agents":[]}')
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_unknown_top_level_key_rejected(self):
        cfg = _valid_config(self.root)
        cfg["extra"] = 1
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_unknown_agent_key_rejected(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["extra"] = 1
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_unknown_grant_key_rejected(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["grants"][0]["extra"] = 1
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_port_bool_rejected(self):
        cfg = _valid_config(self.root)
        cfg["port"] = True
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_port_out_of_range_rejected(self):
        cfg = _valid_config(self.root)
        cfg["port"] = 70000
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_bad_hex_rejected(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["token_sha256"] = "Z" * 64
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_bad_hex_length_rejected(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["token_sha256"] = "a" * 63
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_uppercase_hex_rejected(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["token_sha256"] = "A" * 64
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_empty_agents_rejected(self):
        cfg = {"port": 0, "agents": []}
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_duplicate_agent_id_rejected(self):
        cfg = _valid_config(self.root)
        second = {
            "agent_id": "alice",
            "token_sha256": hash_token("B" * 40),
            "grants": [_valid_grant(self.root)],
        }
        cfg["agents"].append(second)
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_duplicate_token_hash_rejected(self):
        cfg = _valid_config(self.root)
        second = {
            "agent_id": "bob",
            "token_sha256": hash_token(_TOKEN),
            "grants": [_valid_grant(self.root)],
        }
        cfg["agents"].append(second)
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_missing_root_rejected(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["grants"][0]["root"] = os.path.join(
            self.tmp.name, "nope"
        )
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_relative_root_rejected(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["grants"][0]["root"] = "relative/dir"
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_bad_operation_rejected(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["grants"][0]["operations"] = ["delete"]
        with self.assertRaises(ConfigError):
            load_config(self._write(cfg))

    def test_writable_config_rejected(self):
        path = self._write(_valid_config(self.root), mode=0o666)
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_oversized_config_rejected(self):
        path = os.path.join(self.tmp.name, "big.json")
        with open(path, "wb") as fh:
            fh.write(b" " * (1024 * 1024 + 1))
        os.chmod(path, 0o600)
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_error_message_never_contains_token_hash(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["token_sha256"] = "Z" * 64
        path = self._write(cfg)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertNotIn("Z" * 64, str(ctx.exception))

    def test_optional_caps_accepted(self):
        cfg = _valid_config(self.root)
        cfg["agents"][0]["grants"][0]["max_read_bytes"] = 4096
        cfg["agents"][0]["grants"][0]["max_write_bytes"] = 8192
        port, agents = load_config(self._write(cfg))
        grant = agents[0].grants[0]
        self.assertEqual(grant.max_read_bytes, 4096)
        self.assertEqual(grant.max_write_bytes, 8192)

class ServeEndToEndTests(unittest.TestCase):
    def test_run_end_to_end(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.join(tmp.name, "root")
        os.mkdir(root)
        with open(os.path.join(root, "hello.txt"), "wb") as fh:
            fh.write(b"hello world")

        cfg = _valid_config(root, port=0)
        path = os.path.join(tmp.name, "config.json")
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

        self.assertTrue(ready.wait(timeout=10.0), "adapter never became ready")
        port = port_holder["port"]
        self.assertGreater(port, 0)

        client = DriveClient(port=port, token=_TOKEN, timeout=5.0)

        listing = client.list("work", "root")
        self.assertIn("entries", listing)

        client.save("work", "p/notes.txt", b"new content", overwrite=False)

        reply = client.read("work", "p/notes.txt", offset=0, length=64)
        self.assertEqual(reply.data, b"new content")
        self.assertEqual(reply.offset, 0)
        self.assertTrue(reply.eof_observed)

        stop.set()
        thread.join(timeout=10.0)
        self.assertFalse(thread.is_alive(), "run() thread did not end")
        self.assertEqual(result["rc"], 0)

if __name__ == "__main__":
    unittest.main()
