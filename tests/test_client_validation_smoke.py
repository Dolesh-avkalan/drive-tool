"""Smoke tests: DriveClient rejects bad inputs before any network call.

These tests never start a server. Each one asserts that a lexical validation
error is raised (TypeError / ValueError, as per the source) and that the
validation happens *before* the transport seam is touched.
"""

import unittest

from drive_tool.service_client import DriveClient

class _NoNetworkClient(DriveClient):
    """DriveClient whose transport seam fails loudly if it is ever reached."""

    def _open(self):  # pragma: no cover - only runs if validation is skipped
        raise AssertionError("validation should fail before any network call")

class ClientValidationSmokeTest(unittest.TestCase):
    def setUp(self):
        # Any port/token within range; no server is contacted.
        self.client = _NoNetworkClient(port=8940, token="t" * 32)

    def test_invalid_source_id_rejected(self):
        with self.assertRaises(ValueError):
            self.client.list("bad source!")

    def test_invalid_file_id_rejected(self):
        with self.assertRaises(ValueError):
            self.client.list("src", "../etc/passwd")

    def test_file_id_wrong_type_rejected(self):
        with self.assertRaises(TypeError):
            self.client.read("src", 123)

    def test_limit_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            self.client.list("src", limit=101)

    def test_limit_zero_rejected(self):
        with self.assertRaises(ValueError):
            self.client.list("src", limit=0)

    def test_limit_bool_rejected(self):
        with self.assertRaises(TypeError):
            self.client.list("src", limit=True)

if __name__ == "__main__":
    unittest.main()
