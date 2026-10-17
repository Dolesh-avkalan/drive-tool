"""Edge-case tests for drive_tool.serve, mirroring tests/test_serve.py setup."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drive_tool.serve import ConfigError, hash_token, load_config

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

class ServeEdgeCaseTests(unittest.TestCase):
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

    def test_group_or_other_writable_config_rejected(self):
        # CASE 1: mode allowing group/other write -> ConfigError with exact message.
        path = self._write(_valid_config(self.root), mode=0o664)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertEqual(
            str(ctx.exception),
            "config file must not be group/other writable",
        )

    def test_duplicate_object_keys_rejected(self):
        # CASE 2: duplicate keys at same level -> ConfigError("duplicate JSON key").
        path = self._write('{"port": 8000, "port": 9000, "agents": []}')
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertEqual(str(ctx.exception), "duplicate JSON key")

    def test_hash_token_non_string_rejected(self):
        # CASE 3: hash_token(12345) -> TypeError("token must be a str").
        with self.assertRaises(TypeError) as ctx:
            hash_token(12345)
        self.assertEqual(str(ctx.exception), "token must be a str")

    def test_grant_root_regular_file_rejected(self):
        # CASE 4: grant root pointing at a regular file -> ConfigError with
        # "agents[0].grants[0].root must be an existing directory".
        not_a_dir = os.path.join(self.tmp.name, "plain.txt")
        with open(not_a_dir, "wb") as fh:
            fh.write(b"not a directory")
        cfg = _valid_config(self.root)
        cfg["agents"][0]["grants"][0]["root"] = not_a_dir
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._write(cfg))
        self.assertEqual(
            str(ctx.exception),
            "agents[0].grants[0].root must be an existing directory",
        )

    def test_grant_root_missing_path_rejected(self):
        # CASE 4: grant root pointing at a missing path -> same ConfigError.
        missing = os.path.join(self.tmp.name, "does-not-exist")
        cfg = _valid_config(self.root)
        cfg["agents"][0]["grants"][0]["root"] = missing
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._write(cfg))
        self.assertEqual(
            str(ctx.exception),
            "agents[0].grants[0].root must be an existing directory",
        )

    def test_duplicate_token_sha256_rejected(self):
        # CASE 5: two distinct agents sharing one token digest ->
        # ConfigError("duplicate token_sha256").
        cfg = _valid_config(self.root)
        second = {
            "agent_id": "bob",
            "token_sha256": hash_token(_TOKEN),
            "grants": [_valid_grant(self.root)],
        }
        cfg["agents"].append(second)
        with self.assertRaises(ConfigError) as ctx:
            load_config(self._write(cfg))
        self.assertEqual(str(ctx.exception), "duplicate token_sha256")

if __name__ == "__main__":
    unittest.main()
