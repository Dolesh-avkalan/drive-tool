"""Edge-case tests for the MODULE-002 paginated listing helper (dt3-cases-1).

Each test corresponds to one case from the cases record. Expected values are
taken directly from drive_tool/listing.py.

Synthetic fixtures only. Run from the repository root:

    python3 -m unittest discover -s tests -v
"""

import unittest

from drive_tool.listing import ListingService
from drive_tool.storage import (
    ROOT_ID,
    FileMetadata,
    FileRef,
    InMemoryAdapter,
)

SOURCE = "src-a"

def meta(name: str, source_id: str = SOURCE, file_id: str | None = None) -> FileMetadata:
    return FileMetadata(
        ref=FileRef(source_id, file_id if file_id is not None else name),
        name=name,
        parent_id=ROOT_ID,
        kind="file",
        size_bytes=0,
    )

class FakeAdapter:
    """A StorageAdapter stand-in that returns fixed metadata."""

    def __init__(self, metadata=()) -> None:
        self.source_id = "src-fake"
        self.calls = 0
        self._metadata = list(metadata)

    def list_children(self, parent: FileRef) -> list[FileMetadata]:
        self.calls += 1
        return list(self._metadata)

    def stat(self, ref: FileRef) -> FileMetadata:  # pragma: no cover - unused
        raise NotImplementedError

    def read_bytes(self, ref: FileRef) -> bytes:  # pragma: no cover - unused
        raise NotImplementedError

class TestCase1BooleanLimit(unittest.TestCase):
    """CASE 1: boolean limit -> ValueError('limit must be an int, got bool')."""

    def test_limit_true_raises(self) -> None:
        adapter = FakeAdapter()
        service = ListingService(adapter)
        with self.assertRaises(ValueError) as ctx:
            service.list_page(FileRef(SOURCE, ROOT_ID), limit=True)
        self.assertEqual(str(ctx.exception), "limit must be an int, got bool")
        self.assertEqual(adapter.calls, 0)

    def test_limit_false_raises(self) -> None:
        adapter = FakeAdapter()
        service = ListingService(adapter)
        with self.assertRaises(ValueError) as ctx:
            service.list_page(FileRef(SOURCE, ROOT_ID), limit=False)
        self.assertEqual(str(ctx.exception), "limit must be an int, got bool")
        self.assertEqual(adapter.calls, 0)

class TestCase2OffsetAboveFilteredLength(unittest.TestCase):
    """CASE 2: offset strictly greater than filtered length -> ValueError."""

    def test_offset_five_with_three_items(self) -> None:
        adapter = FakeAdapter([meta("a.txt"), meta("b.txt"), meta("c.txt")])
        service = ListingService(adapter)
        with self.assertRaises(ValueError) as ctx:
            service.list_page(FileRef(SOURCE, ROOT_ID), offset=5)
        self.assertEqual(
            str(ctx.exception),
            "offset 5 is greater than the filtered length 3",
        )

    def test_offset_above_filtered_length_uses_filtered_total(self) -> None:
        # Four items, filter 'a' keeps only two; offset 3 exceeds filtered length 2.
        adapter = FakeAdapter(
            [meta("a1.txt"), meta("a2.txt"), meta("b1.txt"), meta("b2.txt")]
        )
        service = ListingService(adapter)
        with self.assertRaises(ValueError) as ctx:
            service.list_page(
                FileRef(SOURCE, ROOT_ID), offset=3, name_contains="a"
            )
        self.assertEqual(
            str(ctx.exception),
            "offset 3 is greater than the filtered length 2",
        )

class TestCase3EmptyResults(unittest.TestCase):
    """CASE 3: empty folder or no-match filter at offset=0 -> (), None."""

    def test_empty_folder(self) -> None:
        adapter = InMemoryAdapter(SOURCE, {})
        page = ListingService(adapter).list_page(FileRef(SOURCE, ROOT_ID), offset=0)
        self.assertEqual(page.items, ())
        self.assertIsNone(page.next_offset)

    def test_non_matching_filter(self) -> None:
        adapter = InMemoryAdapter(SOURCE, {"a.txt": b"x", "b.txt": b"x"})
        page = ListingService(adapter).list_page(
            FileRef(SOURCE, ROOT_ID), offset=0, name_contains="zzz"
        )
        self.assertEqual(page.items, ())
        self.assertIsNone(page.next_offset)

class TestCase4ExactFitNextOffset(unittest.TestCase):
    """CASE 4: items fit exactly into the page -> next_offset is None."""

    def test_two_items_limit_two(self) -> None:
        adapter = FakeAdapter([meta("a.txt"), meta("b.txt")])
        page = ListingService(adapter).list_page(
            FileRef(SOURCE, ROOT_ID), limit=2, offset=0
        )
        self.assertEqual([m.name for m in page.items], ["a.txt", "b.txt"])
        self.assertIsNone(page.next_offset)

    def test_last_page_exactly_full(self) -> None:
        # Four items, limit 2: page at offset 2 holds the final two exactly.
        adapter = FakeAdapter(
            [meta("a.txt"), meta("b.txt"), meta("c.txt"), meta("d.txt")]
        )
        page = ListingService(adapter).list_page(
            FileRef(SOURCE, ROOT_ID), limit=2, offset=2
        )
        self.assertEqual([m.name for m in page.items], ["c.txt", "d.txt"])
        self.assertIsNone(page.next_offset)

class TestCase5TiebreakSorting(unittest.TestCase):
    """CASE 5: identical names ordered by (name, source_id, file_id)."""

    def test_tiebreak_by_source_id(self) -> None:
        adapter = FakeAdapter(
            [meta("dup.txt", "s2"), meta("dup.txt", "s1"), meta("aaa.txt", "s9")]
        )
        page = ListingService(adapter).list_page(FileRef(SOURCE, ROOT_ID))
        self.assertEqual(
            [(m.name, m.ref.source_id) for m in page.items],
            [("aaa.txt", "s9"), ("dup.txt", "s1"), ("dup.txt", "s2")],
        )

    def test_tiebreak_by_file_id_when_name_and_source_match(self) -> None:
        adapter = FakeAdapter(
            [meta("dup.txt", "s1", "z"), meta("dup.txt", "s1", "a")]
        )
        page = ListingService(adapter).list_page(FileRef(SOURCE, ROOT_ID))
        self.assertEqual([m.ref.file_id for m in page.items], ["a", "z"])

if __name__ == "__main__":
    unittest.main()
