"""Adversarial proof tests for the MODULE-005A partial scan and bounded find.

Each test targets a specific rule and fails if the implementation takes the
forbidden shortcut that rule forbids: probing entry ``B + 1``, dropping matches
found in an over-budget folder, counting only metadata instead of raw entries,
declaring a hard-capped scan complete, ignoring ``ScanFailure.inspected``,
scanning a sibling after failed work exhausted the budget, or leaking an
iterator/descriptor on a partial return.

Physical entry order is controlled through the ``OrderedScanAdapter`` seam
wherever a particular prefix matters; the tests never assume filesystem
alphabetical order.
"""

from __future__ import annotations

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path

from drive_tool.local import LocalAdapter, LocalStorageError, UnsupportedPlatformError
from drive_tool.search import find
from drive_tool.storage import (
    ROOT_ID,
    EnumerationLimitError,
    FileMetadata,
    FileRef,
    ScanBatch,
    ScanFailure,
    ScanUnsupportedError,
    StorageError,
)


def _local_supported() -> bool:
    try:
        with tempfile.TemporaryDirectory() as td:
            a = LocalAdapter("probe", td)
            a.close()
        return True
    except UnsupportedPlatformError:
        return False


_LOCAL_SUPPORTED = _local_supported()


class _ScanProbe:
    def __init__(self, order=None):
        self.order = order
        self.next_attempts = 0
        self.yielded = 0
        self.stat_calls = 0
        self.entered = 0
        self.exited = 0
        self.stat_error: dict[str, BaseException] = {}
        self.iterate_error_after: int | None = None
        self.close_error = False


class _ProbeEntry:
    def __init__(self, inner, probe):
        self._inner = inner
        self._probe = probe

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def stat(self, *, follow_symlinks=True):
        self._probe.stat_calls += 1
        exc = self._probe.stat_error.get(self._inner.name)
        if exc is not None:
            raise exc
        return self._inner.stat(follow_symlinks=follow_symlinks)


class _ProbeScandir:
    def __init__(self, inner, probe):
        self._inner = inner
        self._probe = probe
        self._it = None

    def __enter__(self):
        self._probe.entered += 1
        self._inner.__enter__()
        entries = list(self._inner)
        if self._probe.order is not None:
            rank = {name: i for i, name in enumerate(self._probe.order)}
            entries.sort(key=lambda e: rank.get(e.name, len(rank)))
        self._it = iter(entries)
        return self

    def __iter__(self):
        return self

    def __next__(self):
        self._probe.next_attempts += 1
        after = self._probe.iterate_error_after
        if after is not None and self._probe.yielded >= after:
            raise OSError(errno.EIO, "iteration boom")
        entry = next(self._it)
        self._probe.yielded += 1
        return _ProbeEntry(entry, self._probe)

    def __exit__(self, exc_type, exc, tb):
        self._probe.exited += 1
        result = self._inner.__exit__(exc_type, exc, tb)
        if self._probe.close_error:
            raise OSError(errno.EIO, "scandir close boom")
        return result


class OrderedScanAdapter(LocalAdapter):
    def __init__(self, source_id, root, *, order=None, **kwargs):
        self.probe = _ScanProbe(order=order)
        super().__init__(source_id, root, **kwargs)

    def _os_scandir(self, fd):
        return _ProbeScandir(super()._os_scandir(fd), self.probe)


def _write_files(root: Path, names):
    for name in names:
        (root / name).write_text("x")


@unittest.skipUnless(_LOCAL_SUPPORTED, "LocalAdapter unsupported on this platform")
class PartialScanProofTests(unittest.TestCase):
    def _adapter(self, root, order=None):
        return OrderedScanAdapter("src", str(root), order=order)

    # -- no lookahead: a huge directory stops after exactly B ----------------

    def test_huge_directory_stops_after_exactly_b_no_b_plus_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i:03d}.txt" for i in range(200)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                batch = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=5
                )
                self.assertEqual(batch.inspected, 5)
                self.assertEqual(len(batch.items), 5)
                self.assertFalse(batch.complete)
                self.assertEqual(adapter.probe.next_attempts, 5)
                self.assertEqual(adapter.probe.yielded, 5)
                self.assertLessEqual(adapter.probe.stat_calls, 5)
                self.assertEqual([m.name for m in batch.items], names[:5])
            finally:
                adapter.close()

    # -- core defect regression: early match retained from an over-budget dir -

    def test_early_match_retained_from_over_budget_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ["needle.txt"] + [f"other{i:02d}.txt" for i in range(49)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                result = find(
                    adapter,
                    FileRef("src", ROOT_ID),
                    "needle",
                    max_entries=10,
                )
                self.assertEqual([m.name for m in result.items], ["needle.txt"])
                self.assertTrue(result.truncated)
                self.assertEqual(result.reason, "max_entries")
                self.assertEqual(result.inspected, 10)
            finally:
                adapter.close()

    # -- a zero-item partial scan is truncation, not not-found ---------------

    def test_zero_item_partial_scan_is_truncation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i:02d}.dat" for i in range(20)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                batch = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=5
                )
                # Five supported entries were scanned, but the walk finds none.
                self.assertEqual(len(batch.items), 5)
                self.assertEqual(batch.inspected, 5)
                self.assertFalse(batch.complete)

                result = find(
                    adapter, FileRef("src", ROOT_ID), "zzz", max_entries=5
                )
                self.assertEqual(result.items, ())
                self.assertTrue(result.truncated)
                self.assertEqual(result.reason, "max_entries")
                self.assertEqual(result.inspected, 5)
                self.assertEqual(result.skipped, ())
            finally:
                adapter.close()

    # -- result limit still charges the full bounded scan --------------------

    def test_result_limit_still_charges_full_scan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"match{i:02d}.txt" for i in range(10)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                result = find(
                    adapter,
                    FileRef("src", ROOT_ID),
                    "match",
                    max_results=1,
                    max_entries=5000,
                )
                self.assertEqual(len(result.items), 1)
                self.assertTrue(result.truncated)
                self.assertEqual(result.reason, "max_results")
                # The whole 10-entry scan is charged before matching stops.
                self.assertEqual(result.inspected, 10)
            finally:
                adapter.close()

    def test_result_limit_in_partial_folder_charges_full_batch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ["m0.txt"] + [f"other{i:02d}.txt" for i in range(49)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                result = find(
                    adapter,
                    FileRef("src", ROOT_ID),
                    "m0",
                    max_results=1,
                    max_entries=10,
                )
                self.assertEqual(len(result.items), 1)
                self.assertTrue(result.truncated)
                self.assertEqual(result.reason, "max_results")
                self.assertEqual(result.inspected, 10)
            finally:
                adapter.close()

    # -- raw counts summed across multiple BFS folders -----------------------

    def test_raw_counts_summed_across_bfs_folders(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a").mkdir()
            (root / "b").mkdir()
            _write_files(root / "a", ["a1.txt", "a2.txt", "a3.txt"])
            _write_files(root / "b", ["b1.txt", "b2.txt"])
            adapter = self._adapter(root)
            try:
                result = find(
                    adapter, FileRef("src", ROOT_ID), "zzz", max_entries=100
                )
                # root (2 dirs) + a (3 files) + b (2 files) = 7 raw entries.
                self.assertEqual(result.inspected, 7)
                self.assertFalse(result.truncated)
            finally:
                adapter.close()

    # -- items are sorted within the scanned prefix only ---------------------

    def test_matches_ordered_within_scanned_prefix_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ["z.txt", "m.txt", "a.txt"]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                result = find(
                    adapter, FileRef("src", ROOT_ID), "txt", max_entries=2
                )
                # Physical prefix is z.txt, m.txt; sorted within the prefix.
                self.assertEqual([m.name for m in result.items], ["m.txt", "z.txt"])
                self.assertNotIn("a.txt", [m.name for m in result.items])
                self.assertTrue(result.truncated)
                self.assertEqual(result.reason, "max_entries")
            finally:
                adapter.close()

    # -- iterator-close failure after partial work charges the prefix --------

    def test_iterator_close_failure_charges_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ["a.txt", "b.txt", "c.txt"]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            adapter.probe.close_error = True
            try:
                with self.assertRaises(ScanFailure) as cm:
                    adapter.scan_children(FileRef("src", ROOT_ID), max_entries=10)
                self.assertEqual(cm.exception.inspected, 3)
                self.assertIsInstance(cm.exception.error, LocalStorageError)
            finally:
                adapter.probe.close_error = False
                adapter.close()

    # -- old list_children exact-limit and limit+1 witness are unchanged -----

    def test_list_children_exact_limit_and_witness_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i}.txt" for i in range(5)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                ref = FileRef("src", ROOT_ID)
                # Exact-limit success.
                children = adapter.list_children(ref, max_entries=5)
                self.assertEqual(len(children), 5)
                # Legacy limit+1 witness still raises.
                with self.assertRaises(EnumerationLimitError) as cm:
                    adapter.list_children(ref, max_entries=4)
                self.assertEqual(cm.exception.limit, 4)
            finally:
                adapter.close()

    # -- repeated partial scans and failure paths leave no fd growth ---------

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc/self/fd only")
    def test_repeated_partial_and_failing_scans_no_fd_growth(self):
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("/proc/self/fd not available")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i:02d}.txt" for i in range(30)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                # Warm up then settle.
                find(adapter, FileRef("src", ROOT_ID), "f", max_entries=4)
                before = len(os.listdir("/proc/self/fd"))
                for _ in range(40):
                    find(adapter, FileRef("src", ROOT_ID), "f", max_entries=4)
                self.assertEqual(before, len(os.listdir("/proc/self/fd")))

                adapter.probe.stat_error = {"f01.txt": OSError(errno.EIO, "boom")}
                for _ in range(40):
                    with self.assertRaises(LocalStorageError):
                        find(adapter, FileRef("src", ROOT_ID), "f", max_entries=10)
                adapter.probe.stat_error = {}
                self.assertEqual(before, len(os.listdir("/proc/self/fd")))
            finally:
                adapter.close()


class ScanBatchContractProofTests(unittest.TestCase):
    def test_scan_batch_is_frozen_and_tuple_valued(self):
        batch = ScanBatch((), 0, True)
        self.assertIsInstance(batch.items, tuple)
        with self.assertRaises(Exception):
            batch.inspected = 5  # type: ignore[misc]

    def test_scan_unsupported_error_is_exported_and_a_storage_error(self):
        self.assertTrue(issubclass(ScanUnsupportedError, StorageError))

    def test_scan_failure_is_a_storage_error(self):
        self.assertTrue(issubclass(ScanFailure, StorageError))


if __name__ == "__main__":
    unittest.main()
