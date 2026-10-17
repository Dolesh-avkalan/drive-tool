"""Edge-case tests for the MODULE-001 storage contract and in-memory adapter.

Covers the 5 cases from drive-tool/tasks/dt4-cases-1.md. Synthetic data only.
Run from the repository root:

    python3 -m unittest discover -s tests -v
"""

import unittest

from drive_tool.storage import (
    ROOT_ID,
    EnumerationLimitError,
    FileRef,
    InMemoryAdapter,
    IsDirectoryError,
    ScanBatch,
    SourceMismatchError,
)

SOURCE = "src-a"
OTHER_SOURCE = "src-b"

FIXTURES = {
    "alpha.txt": b"hello",
    "empty.bin": b"",
    "zulu.dat": bytes(range(256)),
    "beta.bin": b"\x00\x01\x02\xff",
}

def make_adapter() -> InMemoryAdapter:
    return InMemoryAdapter(SOURCE, dict(FIXTURES))

class TestReservedFilenameTokens(unittest.TestCase):
    """CASE 1: reserved filename tokens are rejected with ValueError."""

    def test_reserved_tokens_rejected(self) -> None:
        for bad in (".", "..", ROOT_ID):
            with self.subTest(filename=bad):
                with self.assertRaises(ValueError):
                    InMemoryAdapter(SOURCE, {bad: b"x"})

    def test_reserved_token_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            InMemoryAdapter(SOURCE, {ROOT_ID: b"x"})
        self.assertEqual(str(ctx.exception), "filename 'root' is reserved")

class TestSourceMismatch(unittest.TestCase):
    """CASE 2: mismatched source_id raises SourceMismatchError."""

    def setUp(self) -> None:
        self.adapter = make_adapter()

    def test_list_children_wrong_source(self) -> None:
        with self.assertRaises(SourceMismatchError):
            self.adapter.list_children(FileRef(OTHER_SOURCE, ROOT_ID))

    def test_stat_wrong_source(self) -> None:
        with self.assertRaises(SourceMismatchError):
            self.adapter.stat(FileRef(OTHER_SOURCE, "alpha.txt"))

    def test_wrong_source_precedes_unknown_file_id(self) -> None:
        with self.assertRaises(SourceMismatchError):
            self.adapter.stat(FileRef(OTHER_SOURCE, "does-not-exist"))
        with self.assertRaises(SourceMismatchError):
            self.adapter.list_children(FileRef(OTHER_SOURCE, "does-not-exist"))

class TestEnumerationLimit(unittest.TestCase):
    """CASE 3: list_children on root over max_entries raises EnumerationLimitError."""

    def setUp(self) -> None:
        self.adapter = make_adapter()

    def test_root_over_limit_raises(self) -> None:
        with self.assertRaises(EnumerationLimitError):
            self.adapter.list_children(FileRef(SOURCE, ROOT_ID), max_entries=3)

    def test_error_limit_attribute(self) -> None:
        with self.assertRaises(EnumerationLimitError) as ctx:
            self.adapter.list_children(FileRef(SOURCE, ROOT_ID), max_entries=3)
        self.assertEqual(ctx.exception.limit, 3)

    def test_root_at_limit_is_allowed(self) -> None:
        children = self.adapter.list_children(
            FileRef(SOURCE, ROOT_ID), max_entries=len(FIXTURES)
        )
        self.assertEqual(len(children), len(FIXTURES))

    def test_root_over_limit_does_not_return_partial(self) -> None:
        with self.assertRaises(EnumerationLimitError):
            self.adapter.list_children(FileRef(SOURCE, ROOT_ID), max_entries=1)

class TestReadBytesOnRoot(unittest.TestCase):
    """CASE 4: read_bytes on the synthetic root raises IsDirectoryError."""

    def test_read_root_raises(self) -> None:
        adapter = make_adapter()
        with self.assertRaises(IsDirectoryError):
            adapter.read_bytes(FileRef(SOURCE, ROOT_ID))

    def test_read_root_message(self) -> None:
        adapter = make_adapter()
        with self.assertRaises(IsDirectoryError) as ctx:
            adapter.read_bytes(FileRef(SOURCE, ROOT_ID))
        self.assertEqual(str(ctx.exception), "'root' is a directory, not a file")

class TestScanChildrenEmptyRoot(unittest.TestCase):
    """CASE 5: scan_children on an empty root returns an empty complete batch."""

    def test_empty_root_scan(self) -> None:
        adapter = InMemoryAdapter(SOURCE, {})
        batch = adapter.scan_children(FileRef(SOURCE, ROOT_ID), max_entries=10)
        self.assertEqual(batch, ScanBatch(items=(), inspected=0, complete=True))

    def test_empty_root_scan_fields(self) -> None:
        adapter = InMemoryAdapter(SOURCE, {})
        batch = adapter.scan_children(FileRef(SOURCE, ROOT_ID), max_entries=10)
        self.assertEqual(batch.items, ())
        self.assertEqual(batch.inspected, 0)
        self.assertTrue(batch.complete)

if __name__ == "__main__":
    unittest.main()
