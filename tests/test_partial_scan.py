"""Unit, boundary and cleanup tests for the MODULE-005A partial scan.

These tests pin the :class:`~drive_tool.storage.ScanBatch` contract, the
``scan_children`` method on both adapters, the failed-scan accounting
(:class:`~drive_tool.storage.ScanFailure`), and the loud validation of a
scanner's reported batch. Physical entry order is controlled through a
deterministic test seam (``OrderedScanAdapter``) wherever a particular prefix
matters -- the tests never assume filesystem alphabetical order.
"""

from __future__ import annotations

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path

from drive_tool.local import (
    AdapterClosedError,
    LocalAdapter,
    LocalStorageError,
    UnsupportedPlatformError,
)
from drive_tool.search import find
from drive_tool.storage import (
    MAX_SCAN_ENTRIES,
    ROOT_ID,
    FileMetadata,
    FileRef,
    InMemoryAdapter,
    NotDirectoryError,
    NotFoundError,
    ScanBatch,
    ScanFailure,
    SourceMismatchError,
    StorageError,
)

TEMP_PREFIX = ".drive-tool-tmp-"


def _local_supported() -> bool:
    try:
        with tempfile.TemporaryDirectory() as td:
            a = LocalAdapter("probe", td)
            a.close()
        return True
    except UnsupportedPlatformError:
        return False


_LOCAL_SUPPORTED = _local_supported()


# ---------------------------------------------------------------------------
# Deterministic scan seam
# ---------------------------------------------------------------------------


class _ScanProbe:
    """Counts and scripts the raw calls a ``scan_children`` makes."""

    def __init__(self, order=None):
        self.order = order
        self.next_attempts = 0
        self.yielded = 0
        self.stat_calls = 0
        self.entered = 0
        self.exited = 0
        # name -> exception raised from DirEntry.stat
        self.stat_error: dict[str, BaseException] = {}
        # raise OSError(EIO) from the (N+1)-th next() attempt once N yielded
        self.iterate_error_after: int | None = None
        # raise OSError(EIO) from the scandir iterator's __exit__
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
    """LocalAdapter whose scandir order, stat calls and failures are scripted."""

    def __init__(self, source_id, root, *, order=None, **kwargs):
        self.probe = _ScanProbe(order=order)
        super().__init__(source_id, root, **kwargs)

    def _os_scandir(self, fd):
        return _ProbeScandir(super()._os_scandir(fd), self.probe)


def _write_files(root: Path, names):
    for name in names:
        (root / name).write_text("x")


# ---------------------------------------------------------------------------
# InMemoryAdapter.scan_children
# ---------------------------------------------------------------------------


class InMemoryScanTests(unittest.TestCase):
    def _adapter(self, names):
        return InMemoryAdapter("mem", {name: b"x" for name in names})

    def test_bounded_prefix_sorted_within_prefix_only(self):
        # Insertion order is deliberately not sorted; the scan must take the
        # first ``max_entries`` inserted and sort only those.
        adapter = self._adapter(["c.txt", "a.txt", "b.txt", "d.txt"])
        batch = adapter.scan_children(FileRef("mem", ROOT_ID), max_entries=2)
        self.assertIsInstance(batch, ScanBatch)
        self.assertEqual([m.name for m in batch.items], ["a.txt", "c.txt"])
        self.assertEqual(batch.inspected, 2)
        self.assertFalse(batch.complete)

    def test_prefix_is_insertion_order_not_global_smallest(self):
        adapter = self._adapter(["z.txt", "y.txt", "x.txt", "a.txt", "b.txt"])
        batch = adapter.scan_children(FileRef("mem", ROOT_ID), max_entries=3)
        names = [m.name for m in batch.items]
        self.assertEqual(names, ["x.txt", "y.txt", "z.txt"])
        self.assertNotIn("a.txt", names)
        self.assertEqual(batch.inspected, 3)
        self.assertFalse(batch.complete)

    def test_complete_when_everything_fits(self):
        adapter = self._adapter(["b.txt", "a.txt"])
        batch = adapter.scan_children(FileRef("mem", ROOT_ID), max_entries=5)
        self.assertEqual([m.name for m in batch.items], ["a.txt", "b.txt"])
        self.assertEqual(batch.inspected, 2)
        self.assertTrue(batch.complete)

    def test_exact_budget_in_memory_is_exactly_complete(self):
        # The in-memory fixture knows its length, so exact completeness is
        # allowed here (unlike a live local folder).
        adapter = self._adapter(["a.txt", "b.txt"])
        batch = adapter.scan_children(FileRef("mem", ROOT_ID), max_entries=2)
        self.assertTrue(batch.complete)
        self.assertEqual(batch.inspected, 2)

    def test_empty_root_is_complete(self):
        adapter = self._adapter([])
        batch = adapter.scan_children(FileRef("mem", ROOT_ID), max_entries=4)
        self.assertEqual(batch.items, ())
        self.assertEqual(batch.inspected, 0)
        self.assertTrue(batch.complete)

    def test_items_are_supported_metadata(self):
        adapter = self._adapter(["a.txt"])
        batch = adapter.scan_children(FileRef("mem", ROOT_ID), max_entries=4)
        meta = batch.items[0]
        self.assertIsInstance(meta, FileMetadata)
        self.assertEqual(meta.kind, "file")
        self.assertEqual(meta.ref, FileRef("mem", "a.txt"))

    def test_budget_is_mandatory_and_validated(self):
        adapter = self._adapter(["a.txt"])
        root = FileRef("mem", ROOT_ID)
        with self.assertRaises(TypeError):
            adapter.scan_children(root)  # type: ignore[call-arg]
        for bad in (0, -1, True, False, "5", 1.5, None, MAX_SCAN_ENTRIES + 1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    adapter.scan_children(root, max_entries=bad)  # type: ignore[arg-type]

    def test_source_mismatch_precedes_budget_validation(self):
        adapter = self._adapter(["a.txt"])
        with self.assertRaises(SourceMismatchError):
            adapter.scan_children(FileRef("other", ROOT_ID), max_entries=0)

    def test_unknown_and_non_directory(self):
        adapter = self._adapter(["a.txt"])
        with self.assertRaises(NotFoundError):
            adapter.scan_children(FileRef("mem", "nope"), max_entries=3)
        with self.assertRaises(NotDirectoryError):
            adapter.scan_children(FileRef("mem", "a.txt"), max_entries=3)

    def test_list_children_unchanged_exact_limit(self):
        # Regression guard: the old all-or-nothing contract is untouched.
        adapter = self._adapter(["a.txt", "b.txt", "c.txt"])
        root = FileRef("mem", ROOT_ID)
        self.assertEqual(len(adapter.list_children(root, max_entries=3)), 3)
        from drive_tool.storage import EnumerationLimitError

        with self.assertRaises(EnumerationLimitError):
            adapter.list_children(root, max_entries=2)


# ---------------------------------------------------------------------------
# LocalAdapter.scan_children
# ---------------------------------------------------------------------------


@unittest.skipUnless(_LOCAL_SUPPORTED, "LocalAdapter unsupported on this platform")
class LocalScanTests(unittest.TestCase):
    def _adapter(self, root, order=None, **kwargs):
        return OrderedScanAdapter("src", str(root), order=order, **kwargs)

    def test_counts_all_raw_and_returns_only_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_files(root, ["a.txt", "b.txt"])
            (root / f"{TEMP_PREFIX}abc").write_text("x")
            os.symlink("a.txt", root / "link")
            os.mkfifo(root / "pipe")
            adapter = self._adapter(root)
            try:
                batch = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=10
                )
                self.assertEqual([m.name for m in batch.items], ["a.txt", "b.txt"])
                self.assertEqual(batch.inspected, 5)
                self.assertTrue(batch.complete)
            finally:
                adapter.close()

    def test_exact_budget_is_incomplete_and_never_probes_b_plus_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i}.txt" for i in range(5)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                batch = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=5
                )
                self.assertEqual(len(batch.items), 5)
                self.assertEqual(batch.inspected, 5)
                self.assertFalse(batch.complete)
                # Exactly B next() attempts and exactly B stat calls: entry B+1
                # is never requested.
                self.assertEqual(adapter.probe.next_attempts, 5)
                self.assertEqual(adapter.probe.yielded, 5)
                self.assertEqual(adapter.probe.stat_calls, 5)
            finally:
                adapter.close()

    def test_over_budget_stops_at_b_without_b_plus_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i}.txt" for i in range(6)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                batch = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=5
                )
                got = [m.name for m in batch.items]
                self.assertEqual(got, names[:5])
                self.assertNotIn("f5.txt", got)
                self.assertEqual(batch.inspected, 5)
                self.assertFalse(batch.complete)
                self.assertEqual(adapter.probe.next_attempts, 5)
                self.assertEqual(adapter.probe.yielded, 5)
                self.assertEqual(adapter.probe.stat_calls, 5)
            finally:
                adapter.close()

    def test_under_budget_observes_eof_and_is_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i}.txt" for i in range(3)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                batch = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=10
                )
                self.assertEqual(len(batch.items), 3)
                self.assertEqual(batch.inspected, 3)
                self.assertTrue(batch.complete)
                # N yields then one StopIteration probe to observe EOF.
                self.assertEqual(adapter.probe.yielded, 3)
                self.assertEqual(adapter.probe.next_attempts, 4)
            finally:
                adapter.close()

    def test_mixed_hidden_temp_symlink_fifo_all_charged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "keep.txt").write_text("x")
            os.symlink("keep.txt", root / "s1")
            os.symlink("keep.txt", root / "s2")
            (root / f"{TEMP_PREFIX}1").write_text("x")
            (root / f"{TEMP_PREFIX}2").write_text("x")
            os.mkfifo(root / "pipe")
            order = ["keep.txt", "s1", "s2", f"{TEMP_PREFIX}1", f"{TEMP_PREFIX}2", "pipe"]
            adapter = self._adapter(root, order=order)
            try:
                exact = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=6
                )
                self.assertEqual([m.name for m in exact.items], ["keep.txt"])
                self.assertEqual(exact.inspected, 6)
                self.assertFalse(exact.complete)

                roomy = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=20
                )
                self.assertEqual([m.name for m in roomy.items], ["keep.txt"])
                self.assertEqual(roomy.inspected, 6)
                self.assertTrue(roomy.complete)
            finally:
                adapter.close()

    def test_vanishing_entries_are_charged_but_omitted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ["f0.txt", "f1.txt", "f2.txt"]
            _write_files(root, names)
            for exc in (FileNotFoundError(errno.ENOENT, "gone"), OSError(errno.ENOENT, "gone")):
                adapter = self._adapter(root, order=names)
                adapter.probe.stat_error = {"f1.txt": exc}
                try:
                    batch = adapter.scan_children(
                        FileRef("src", ROOT_ID), max_entries=10
                    )
                    self.assertEqual(
                        [m.name for m in batch.items], ["f0.txt", "f2.txt"]
                    )
                    self.assertEqual(batch.inspected, 3)
                    self.assertTrue(batch.complete)
                finally:
                    adapter.close()

    def test_budget_is_mandatory_and_validated(self):
        with tempfile.TemporaryDirectory() as td:
            adapter = self._adapter(Path(td))
            try:
                ref = FileRef("src", ROOT_ID)
                with self.assertRaises(TypeError):
                    adapter.scan_children(ref)  # type: ignore[call-arg]
                for bad in (0, -1, True, False, "5", 1.5, None, MAX_SCAN_ENTRIES + 1):
                    with self.subTest(bad=bad):
                        with self.assertRaises(ValueError):
                            adapter.scan_children(ref, max_entries=bad)  # type: ignore[arg-type]
            finally:
                adapter.close()

    def test_source_closed_validation_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            adapter = self._adapter(Path(td))
            try:
                # Source mismatch precedes even a bad budget.
                with self.assertRaises(SourceMismatchError):
                    adapter.scan_children(
                        FileRef("other", ROOT_ID), max_entries=0
                    )
                # Bad budget precedes the file_id parse.
                with self.assertRaises(ValueError):
                    adapter.scan_children(FileRef("src", "bad id"), max_entries=0)
            finally:
                adapter.close()

        with tempfile.TemporaryDirectory() as td:
            adapter = self._adapter(Path(td))
            adapter.close()
            with self.assertRaises(AdapterClosedError):
                adapter.scan_children(FileRef("src", ROOT_ID), max_entries=5)

    def test_open_errors_before_iteration_propagate_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "file.txt").write_text("x")
            adapter = self._adapter(root)
            try:
                with self.assertRaises(NotFoundError):
                    adapter.scan_children(FileRef("src", "p/nope"), max_entries=5)
                with self.assertRaises(NotDirectoryError):
                    adapter.scan_children(
                        FileRef("src", "p/file.txt"), max_entries=5
                    )
            finally:
                adapter.close()

    def test_scandir_order_is_the_physical_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ["m.txt", "a.txt", "z.txt", "b.txt"]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            try:
                batch = adapter.scan_children(
                    FileRef("src", ROOT_ID), max_entries=2
                )
                # Physical prefix is m.txt, a.txt -> sorted within the prefix.
                self.assertEqual([m.name for m in batch.items], ["a.txt", "m.txt"])
                self.assertNotIn("z.txt", [m.name for m in batch.items])
            finally:
                adapter.close()


# ---------------------------------------------------------------------------
# Failed-scan accounting
# ---------------------------------------------------------------------------


@unittest.skipUnless(_LOCAL_SUPPORTED, "LocalAdapter unsupported on this platform")
class ScanFailureAccountingTests(unittest.TestCase):
    def _adapter(self, root, order=None):
        return OrderedScanAdapter("src", str(root), order=order)

    def test_stat_oserror_after_prefix_is_scanfailure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ["f0.txt", "f1.txt", "f2.txt"]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            boom = OSError(errno.EIO, "stat boom")
            adapter.probe.stat_error = {"f2.txt": boom}
            try:
                with self.assertRaises(ScanFailure) as cm:
                    adapter.scan_children(FileRef("src", ROOT_ID), max_entries=10)
                self.assertEqual(cm.exception.inspected, 3)
                self.assertIsInstance(cm.exception.error, LocalStorageError)
                self.assertIs(cm.exception.error.__cause__, boom)
            finally:
                adapter.close()

    def test_iteration_oserror_after_prefix_is_scanfailure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i}.txt" for i in range(5)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            adapter.probe.iterate_error_after = 2
            try:
                with self.assertRaises(ScanFailure) as cm:
                    adapter.scan_children(FileRef("src", ROOT_ID), max_entries=10)
                self.assertEqual(cm.exception.inspected, 2)
                self.assertIsInstance(cm.exception.error, LocalStorageError)
            finally:
                adapter.close()

    def test_iteration_oserror_before_any_entry_is_not_scanfailure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_files(root, ["a.txt", "b.txt"])
            adapter = self._adapter(root)
            adapter.probe.iterate_error_after = 0
            try:
                with self.assertRaises(LocalStorageError) as cm:
                    adapter.scan_children(FileRef("src", ROOT_ID), max_entries=10)
                self.assertNotIsInstance(cm.exception, ScanFailure)
            finally:
                adapter.close()

    def test_iterator_close_oserror_after_prefix_is_scanfailure(self):
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

    def test_descriptor_cleanup_failure_is_scanfailure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = ["a.txt", "b.txt", "c.txt"]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            real_close = adapter._os_close
            leaked = {"fd": None}

            def failing_close(fd):
                if leaked["fd"] is None:
                    leaked["fd"] = fd
                    raise OSError(errno.EIO, "close boom")
                return real_close(fd)

            adapter._os_close = failing_close
            try:
                with self.assertRaises(ScanFailure) as cm:
                    adapter.scan_children(FileRef("src", ROOT_ID), max_entries=10)
                self.assertEqual(cm.exception.inspected, 3)
                self.assertIsInstance(cm.exception.error, LocalStorageError)
            finally:
                adapter._os_close = real_close
                if leaked["fd"] is not None:
                    os.close(leaked["fd"])
                adapter.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc/self/fd only")
    def test_no_fd_growth_on_partial_and_failing_scans(self):
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("/proc/self/fd not available")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = [f"f{i}.txt" for i in range(20)]
            _write_files(root, names)
            adapter = self._adapter(root, order=names)
            ref = FileRef("src", ROOT_ID)
            try:
                # Warm up.
                adapter.scan_children(ref, max_entries=3)
                before = len(os.listdir("/proc/self/fd"))
                for _ in range(50):
                    adapter.scan_children(ref, max_entries=3)
                after_partial = len(os.listdir("/proc/self/fd"))
                self.assertEqual(before, after_partial)

                adapter.probe.stat_error = {"f1.txt": OSError(errno.EIO, "boom")}
                for _ in range(50):
                    with self.assertRaises(ScanFailure):
                        adapter.scan_children(ref, max_entries=10)
                adapter.probe.stat_error = {}
                after_fail = len(os.listdir("/proc/self/fd"))
                self.assertEqual(before, after_fail)
            finally:
                adapter.close()


# ---------------------------------------------------------------------------
# Search-side batch validation and capability requirement
# ---------------------------------------------------------------------------


def _meta(file_id, name, kind="file"):
    return FileMetadata(
        ref=FileRef("fake", file_id),
        name=name,
        parent_id=ROOT_ID,
        kind=kind,
        size_bytes=0 if kind == "file" else None,
    )


class _ScriptedAdapter:
    """Adapter whose ``scan_children`` returns or raises exactly what we set."""

    source_id = "fake"

    def __init__(self, script):
        self._script = script
        self.calls = []

    def scan_children(self, parent, *, max_entries):
        self.calls.append((parent.file_id, max_entries))
        result = self._script.get(parent.file_id, ScanBatch((), 0, True))
        if isinstance(result, BaseException):
            raise result
        return result

    def stat(self, ref):
        raise NotImplementedError

    def read_bytes(self, ref):
        raise NotImplementedError


class SearchBatchValidationTests(unittest.TestCase):
    def _find(self, script, **kwargs):
        adapter = _ScriptedAdapter(script)
        return find(adapter, FileRef("fake", ROOT_ID), "x", **kwargs), adapter

    def test_rejects_non_tuple_items(self):
        script = {ROOT_ID: ScanBatch(["a"], 1, True)}  # type: ignore[arg-type]
        with self.assertRaises(StorageError):
            self._find(script)

    def test_rejects_bool_inspected(self):
        script = {ROOT_ID: ScanBatch((), True, True)}  # type: ignore[arg-type]
        with self.assertRaises(StorageError):
            self._find(script)

    def test_rejects_inspected_over_remaining(self):
        script = {ROOT_ID: ScanBatch((), 999, True)}
        with self.assertRaises(StorageError):
            self._find(script, max_entries=10)

    def test_rejects_non_bool_complete(self):
        script = {ROOT_ID: ScanBatch((), 0, 1)}  # type: ignore[arg-type]
        with self.assertRaises(StorageError):
            self._find(script)

    def test_rejects_items_longer_than_inspected(self):
        script = {ROOT_ID: ScanBatch((_meta("a", "a"), _meta("b", "b")), 1, True)}
        with self.assertRaises(StorageError):
            self._find(script)

    def test_rejects_incomplete_batch_that_does_not_exhaust_budget(self):
        script = {ROOT_ID: ScanBatch((), 0, False)}
        with self.assertRaises(StorageError):
            self._find(script, max_entries=10)

    def test_rejects_failure_inspected_out_of_range(self):
        script = {ROOT_ID: ScanFailure(999, LocalStorageError("x"))}
        with self.assertRaises(StorageError):
            self._find(script, max_entries=10)

    def test_root_scanfailure_reraises_original_error_object(self):
        original = LocalStorageError("original root failure")
        script = {ROOT_ID: ScanFailure(2, original)}
        with self.assertRaises(LocalStorageError) as cm:
            self._find(script)
        self.assertIs(cm.exception, original)

    def test_subdir_scanfailure_is_skipped_and_charged(self):
        sub = _meta("p/sub", "sub", kind="directory")
        original = LocalStorageError("sub failure")
        script = {
            ROOT_ID: ScanBatch((sub,), 1, True),
            "p/sub": ScanFailure(2, original),
        }
        result, adapter = self._find(script, max_entries=100)
        self.assertEqual(result.skipped, ("p/sub",))
        self.assertEqual(result.inspected, 3)  # 1 (root) + 2 (failed sub)
        # d035 / F-B compatibility change: a skipped directory now surfaces as
        # incomplete coverage. Previously this asserted `truncated is False`,
        # which encoded the false completeness the review flagged: the caller
        # could not tell the subtree had not been fully searched.
        self.assertTrue(result.truncated)
        self.assertEqual(result.reason, "skipped")
        self.assertEqual([c[0] for c in adapter.calls], [ROOT_ID, "p/sub"])

    def test_no_scans_after_failed_work_exhausts_budget(self):
        sub1 = _meta("p/s1", "s1", kind="directory")
        sub2 = _meta("p/s2", "s2", kind="directory")
        script = {
            ROOT_ID: ScanBatch((sub1, sub2), 2, True),
            "p/s1": ScanFailure(1, LocalStorageError("s1 failure")),
        }
        result, adapter = self._find(script, max_entries=3, max_depth=4)
        self.assertEqual(result.skipped, ("p/s1",))
        self.assertEqual(result.inspected, 3)
        self.assertTrue(result.truncated)
        self.assertEqual(result.reason, "max_entries")
        # s2 is never scanned once the failed scan consumes the budget.
        self.assertEqual([c[0] for c in adapter.calls], [ROOT_ID, "p/s1"])

    def test_argument_validation_precedes_capability_check(self):
        class NoScan:
            source_id = "fake"

            def stat(self, ref):
                raise NotImplementedError

            def read_bytes(self, ref):
                raise NotImplementedError

        from drive_tool.storage import ScanUnsupportedError

        with self.assertRaises(ValueError):
            find(NoScan(), FileRef("fake", ROOT_ID), "")  # type: ignore[arg-type]
        with self.assertRaises(ScanUnsupportedError):
            find(NoScan(), FileRef("fake", ROOT_ID), "x")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
