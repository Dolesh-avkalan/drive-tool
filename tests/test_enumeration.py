"""Tests for bounded enumeration (MODULE-004).

Covers LocalAdapter, InMemoryAdapter, and ListingService enumeration limits,
argument validation, check ordering, and descriptor hygiene.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from drive_tool.listing import ListingService
from drive_tool.local import (
    AdapterClosedError,
    LocalAdapter,
    UnsupportedPlatformError,
)
from drive_tool.storage import (
    ROOT_ID,
    EnumerationLimitError,
    FileRef,
    InMemoryAdapter,
    NotDirectoryError,
    SourceMismatchError,
)

def _local_supported() -> bool:
    try:
        with tempfile.TemporaryDirectory() as td:
            adapter = LocalAdapter("probe", td)
            adapter.close()
        return True
    except UnsupportedPlatformError:
        return False

_LOCAL_SUPPORTED = _local_supported()

def _make_local_adapter(root, **kwargs) -> LocalAdapter:
    return LocalAdapter("src", str(root), **kwargs)

@unittest.skipUnless(_LOCAL_SUPPORTED, "LocalAdapter unsupported on this platform")
class LocalAdapterEnumerationTests(unittest.TestCase):
    def test_limit_exact_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(3):
                (root / f"f{i}.txt").write_text("x")
            adapter = _make_local_adapter(root, max_list_entries=3)
            try:
                children = adapter.list_children(FileRef("src", ROOT_ID))
                self.assertEqual(len(children), 3)
                self.assertEqual(
                    sorted(c.name for c in children),
                    ["f0.txt", "f1.txt", "f2.txt"],
                )
            finally:
                adapter.close()

    def test_limit_exceeded_by_one_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(4):
                (root / f"f{i}.txt").write_text("x")
            adapter = _make_local_adapter(root, max_list_entries=3)
            try:
                with self.assertRaises(EnumerationLimitError) as cm:
                    adapter.list_children(FileRef("src", ROOT_ID))
                self.assertEqual(cm.exception.limit, 3)
            finally:
                adapter.close()

    def test_symlinks_and_special_entries_count_toward_limit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("x")
            (root / "b.txt").write_text("x")
            os.symlink(root / "a.txt", root / "link")
            os.mkfifo(root / "fifo")

            # 4 entries on disk: limit 3 must raise.
            adapter = _make_local_adapter(root, max_list_entries=3)
            try:
                with self.assertRaises(EnumerationLimitError) as cm:
                    adapter.list_children(FileRef("src", ROOT_ID))
                self.assertEqual(cm.exception.limit, 3)
            finally:
                adapter.close()

            # Limit 4 passes; symlink and FIFO are omitted from the result.
            adapter = _make_local_adapter(root, max_list_entries=4)
            try:
                children = adapter.list_children(FileRef("src", ROOT_ID))
                self.assertEqual(sorted(c.name for c in children), ["a.txt", "b.txt"])
            finally:
                adapter.close()

    def test_per_call_max_entries_overrides_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(3):
                (root / f"f{i}.txt").write_text("x")
            adapter = _make_local_adapter(root, max_list_entries=2)
            try:
                ref = FileRef("src", ROOT_ID)
                # Default would raise.
                with self.assertRaises(EnumerationLimitError):
                    adapter.list_children(ref)
                # Per-call override with 3 passes.
                children = adapter.list_children(ref, max_entries=3)
                self.assertEqual(len(children), 3)
                # Per-call override with 1 raises with the tighter limit.
                with self.assertRaises(EnumerationLimitError) as cm:
                    adapter.list_children(ref, max_entries=1)
                self.assertEqual(cm.exception.limit, 1)
            finally:
                adapter.close()

    def test_invalid_limit_values_raise_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "f.txt").write_text("x")
            for bad in (0, -1, True, False, "5", 1.5):
                with self.assertRaises(ValueError):
                    _make_local_adapter(root, max_list_entries=bad)
            adapter = _make_local_adapter(root)
            try:
                ref = FileRef("src", ROOT_ID)
                for bad in (0, -1, True, False, "5", 1.5):
                    with self.assertRaises(ValueError):
                        adapter.list_children(ref, max_entries=bad)
            finally:
                adapter.close()

    def test_check_order_source_closed_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "f.txt").write_text("x")
            adapter = _make_local_adapter(root)
            try:
                # Source mismatch precedes argument validation.
                with self.assertRaises(SourceMismatchError):
                    adapter.list_children(FileRef("other", ROOT_ID), max_entries=0)
                # Open adapter, bad max_entries: ValueError.
                with self.assertRaises(ValueError):
                    adapter.list_children(FileRef("src", ROOT_ID), max_entries=0)
            finally:
                adapter.close()

        with tempfile.TemporaryDirectory() as td2:
            adapter2 = _make_local_adapter(td2)
            adapter2.close()
            # Closed adapter: source mismatch still checked first.
            with self.assertRaises(SourceMismatchError):
                adapter2.list_children(FileRef("other", ROOT_ID), max_entries=0)
            # Closed adapter beats argument validation.
            with self.assertRaises(AdapterClosedError):
                adapter2.list_children(FileRef("src", ROOT_ID), max_entries=0)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc/self/fd only")
    def test_no_fd_leak_on_repeated_limit_errors(self):
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("/proc/self/fd not available")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(10):
                (root / f"f{i}.txt").write_text("x")
            adapter = _make_local_adapter(root)
            try:
                ref = FileRef("src", ROOT_ID)
                # Warm up so first-call allocations do not skew the count.
                with self.assertRaises(EnumerationLimitError):
                    adapter.list_children(ref, max_entries=2)
                before = len(os.listdir("/proc/self/fd"))
                for _ in range(50):
                    with self.assertRaises(EnumerationLimitError):
                        adapter.list_children(ref, max_entries=2)
                after = len(os.listdir("/proc/self/fd"))
                self.assertEqual(before, after)
            finally:
                adapter.close()

class InMemoryAdapterEnumerationTests(unittest.TestCase):
    def _make(self, n: int) -> InMemoryAdapter:
        return InMemoryAdapter("mem", {f"f{i}.txt": b"x" for i in range(n)})

    def test_limit_exact_passes_and_exceeded_raises(self):
        adapter = self._make(3)
        root = FileRef("mem", ROOT_ID)
        self.assertEqual(len(adapter.list_children(root)), 3)
        self.assertEqual(len(adapter.list_children(root, max_entries=3)), 3)
        with self.assertRaises(EnumerationLimitError) as cm:
            adapter.list_children(root, max_entries=2)
        self.assertEqual(cm.exception.limit, 2)

    def test_invalid_values_raise_value_error(self):
        adapter = self._make(1)
        root = FileRef("mem", ROOT_ID)
        for bad in (0, -1, True, False, "5", 1.5):
            with self.assertRaises(ValueError):
                adapter.list_children(root, max_entries=bad)

    def test_source_mismatch_precedes_validation(self):
        adapter = self._make(1)
        with self.assertRaises(SourceMismatchError):
            adapter.list_children(FileRef("other", ROOT_ID), max_entries=0)

    def test_non_directory_parent_still_raises_not_directory(self):
        adapter = self._make(2)
        with self.assertRaises(NotDirectoryError):
            adapter.list_children(FileRef("mem", "f0.txt"), max_entries=1)

class ListingServiceEnumerationTests(unittest.TestCase):
    def test_max_entries_validated_before_adapter(self):
        calls: list[int | None] = []

        class SpyAdapter:
            source_id = "spy"

            def list_children(self, parent, *, max_entries=None):
                calls.append(max_entries)
                return []

            def stat(self, ref):
                raise NotImplementedError

            def read_bytes(self, ref):
                raise NotImplementedError

        svc = ListingService(SpyAdapter())
        ref = FileRef("spy", ROOT_ID)
        for bad in (0, -1, True, False, "5", 1.5):
            with self.assertRaises(ValueError):
                svc.list_page(ref, max_entries=bad)
        self.assertEqual(calls, [])

    def test_max_entries_passed_through_only_when_given(self):
        calls: list[int | None] = []

        class RecordingAdapter:
            source_id = "rec"

            def list_children(self, parent, *, max_entries=None):
                calls.append(max_entries)
                return []

            def stat(self, ref):
                raise NotImplementedError

            def read_bytes(self, ref):
                raise NotImplementedError

        svc = ListingService(RecordingAdapter())
        ref = FileRef("rec", ROOT_ID)
        svc.list_page(ref, max_entries=5)
        svc.list_page(ref)
        self.assertEqual(calls, [5, None])

    def test_enumeration_limit_error_propagates(self):
        class RaisingAdapter:
            source_id = "r"

            def list_children(self, parent, *, max_entries=None):
                raise EnumerationLimitError(7)

            def stat(self, ref):
                raise NotImplementedError

            def read_bytes(self, ref):
                raise NotImplementedError

        svc = ListingService(RaisingAdapter())
        with self.assertRaises(EnumerationLimitError) as cm:
            svc.list_page(FileRef("r", ROOT_ID), max_entries=7)
        self.assertEqual(cm.exception.limit, 7)

    def test_legacy_adapter_without_keyword_works_when_max_entries_none(self):
        class LegacyAdapter:
            source_id = "leg"

            def list_children(self, parent):
                # Deliberately does NOT accept max_entries.
                return []

            def stat(self, ref):
                raise NotImplementedError

            def read_bytes(self, ref):
                raise NotImplementedError

        svc = ListingService(LegacyAdapter())
        page = svc.list_page(FileRef("leg", ROOT_ID))
        self.assertEqual(page.items, ())
        self.assertIsNone(page.next_offset)

if __name__ == "__main__":
    unittest.main()
