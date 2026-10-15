"""Tests for bounded breadth-first search (MODULE-004)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from drive_tool.local import (
    AdapterClosedError,
    LocalAdapter,
    UnsupportedPlatformError,
)
from drive_tool.search import FindResult, find
from drive_tool.storage import (
    ROOT_ID,
    EnumerationLimitError,
    FileMetadata,
    FileRef,
    NotDirectoryError,
    NotFoundError,
    ScanBatch,
    ScanUnsupportedError,
    SourceMismatchError,
    StorageError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local_supported() -> bool:
    try:
        with tempfile.TemporaryDirectory() as td:
            a = LocalAdapter("probe", td)
            a.close()
        return True
    except UnsupportedPlatformError:
        return False

_LOCAL_SUPPORTED = _local_supported()

def _make_local(root, **kwargs) -> LocalAdapter:
    return LocalAdapter("src", str(root), **kwargs)

def _file(file_id, name, parent_id, kind="file", size=0, source_id="fake"):
    return FileMetadata(
        ref=FileRef(source_id, file_id),
        name=name,
        parent_id=parent_id,
        kind=kind,
        size_bytes=size if kind == "file" else None,
    )

class FakeAdapter:
    """Minimal StorageAdapter for search tests.

    ``children_by_id`` maps a ``file_id`` to its list of ``FileMetadata``.
    ``scan_children`` (the method ``find`` requires) returns a bounded prefix:
    when ``enforce_limit`` is true and a folder holds more than the requested
    ``max_entries`` it returns that many items with ``complete=False``; when
    ``enforce_limit`` is false it returns every item with ``inspected`` set to
    the full length, an over-budget report a caller must reject. ``raise_on``
    maps a ``file_id`` to an exception instance that both listing methods raise
    instead of returning. Every call is recorded in ``calls`` as
    ``(file_id, max_entries)``.
    """

    def __init__(
        self, source_id, children_by_id, *, enforce_limit=True, raise_on=None
    ):
        self.source_id = source_id
        self._children = {k: list(v) for k, v in children_by_id.items()}
        self._enforce_limit = enforce_limit
        self._raise_on = dict(raise_on or {})
        self.calls = []

    def list_children(self, parent, *, max_entries=None):
        self.calls.append((parent.file_id, max_entries))
        if parent.source_id != self.source_id:
            raise SourceMismatchError(
                f"source mismatch: {parent.source_id!r} != {self.source_id!r}"
            )
        if parent.file_id in self._raise_on:
            raise self._raise_on[parent.file_id]
        items = list(self._children.get(parent.file_id, []))
        if (
            self._enforce_limit
            and max_entries is not None
            and len(items) > max_entries
        ):
            raise EnumerationLimitError(max_entries)
        return items

    def scan_children(self, parent, *, max_entries):
        self.calls.append((parent.file_id, max_entries))
        if parent.source_id != self.source_id:
            raise SourceMismatchError(
                f"source mismatch: {parent.source_id!r} != {self.source_id!r}"
            )
        if parent.file_id in self._raise_on:
            raise self._raise_on[parent.file_id]
        items = list(self._children.get(parent.file_id, []))
        if self._enforce_limit and len(items) > max_entries:
            return ScanBatch(
                items=tuple(items[:max_entries]),
                inspected=max_entries,
                complete=False,
            )
        return ScanBatch(items=tuple(items), inspected=len(items), complete=True)

    def stat(self, ref):
        raise NotImplementedError

    def read_bytes(self, ref):
        raise NotImplementedError

# ---------------------------------------------------------------------------
# LocalAdapter-backed tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(_LOCAL_SUPPORTED, "LocalAdapter unsupported on this platform")
class FindLocalAdapterTests(unittest.TestCase):
    def test_matches_files_and_dirs_case_modes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Report.txt").write_text("x")
            (root / "report_dir").mkdir()
            (root / "other.txt").write_text("x")
            adapter = _make_local(root)
            try:
                ref = FileRef("src", ROOT_ID)
                r = find(adapter, ref, "report")
                self.assertEqual(
                    sorted(m.name for m in r.items), ["Report.txt", "report_dir"]
                )
                self.assertFalse(r.truncated)
                self.assertIsNone(r.reason)

                r = find(adapter, ref, "report", case_sensitive=True)
                self.assertEqual([m.name for m in r.items], ["report_dir"])
            finally:
                adapter.close()

    def test_bfs_walk_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "root_match_a.txt").write_text("x")
            (root / "root_match_b.txt").write_text("x")
            (root / "a_dir").mkdir()
            (root / "b_dir").mkdir()
            (root / "a_dir" / "deep_a_match.txt").write_text("x")
            (root / "b_dir" / "deep_b_match.txt").write_text("x")
            (root / "a_dir" / "z_dir").mkdir()
            (root / "b_dir" / "c_dir").mkdir()
            (root / "a_dir" / "z_dir" / "z_match.txt").write_text("x")
            (root / "b_dir" / "c_dir" / "c_match.txt").write_text("x")
            adapter = _make_local(root)
            try:
                r = find(adapter, FileRef("src", ROOT_ID), "match")
                self.assertEqual(
                    [m.name for m in r.items],
                    [
                        "root_match_a.txt",
                        "root_match_b.txt",
                        "deep_a_match.txt",
                        "deep_b_match.txt",
                        "c_match.txt",
                        "z_match.txt",
                    ],
                )
                self.assertFalse(r.truncated)
                self.assertIsNone(r.reason)
            finally:
                adapter.close()

    def test_max_depth_zero_and_deeper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "top.txt").write_text("x")
            (root / "d1").mkdir()
            (root / "d1" / "mid.txt").write_text("x")
            (root / "d1" / "d2").mkdir()
            (root / "d1" / "d2" / "deep.txt").write_text("x")
            adapter = _make_local(root)
            try:
                ref = FileRef("src", ROOT_ID)
                r0 = find(adapter, ref, "txt", max_depth=0)
                self.assertEqual([m.name for m in r0.items], ["top.txt"])
                self.assertTrue(r0.truncated)
                self.assertEqual(r0.reason, "max_depth")

                r1 = find(adapter, ref, "txt", max_depth=1)
                self.assertEqual(
                    sorted(m.name for m in r1.items), ["mid.txt", "top.txt"]
                )
                self.assertTrue(r1.truncated)
                self.assertEqual(r1.reason, "max_depth")

                r3 = find(adapter, ref, "txt", max_depth=3)
                self.assertEqual(
                    sorted(m.name for m in r3.items),
                    ["deep.txt", "mid.txt", "top.txt"],
                )
                self.assertFalse(r3.truncated)
                self.assertIsNone(r3.reason)
            finally:
                adapter.close()

    def test_max_depth_zero_only_files_not_truncated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("x")
            (root / "b.txt").write_text("x")
            adapter = _make_local(root)
            try:
                r = find(adapter, FileRef("src", ROOT_ID), "txt", max_depth=0)
                self.assertEqual(
                    sorted(m.name for m in r.items), ["a.txt", "b.txt"]
                )
                self.assertFalse(r.truncated)
                self.assertIsNone(r.reason)
            finally:
                adapter.close()

    def test_max_results_truncation_vs_exact_last_entry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "m1.txt").write_text("x")
            (root / "m2.txt").write_text("x")
            (root / "m3.txt").write_text("x")
            adapter = _make_local(root)
            try:
                ref = FileRef("src", ROOT_ID)
                r = find(adapter, ref, "m", max_results=2)
                self.assertEqual(len(r.items), 2)
                self.assertTrue(r.truncated)
                self.assertEqual(r.reason, "max_results")
                # The whole 3-entry root scan is charged even though matching
                # stopped after the second match.
                self.assertEqual(r.inspected, 3)

                r = find(adapter, ref, "m", max_results=3)
                self.assertEqual(len(r.items), 3)
                self.assertFalse(r.truncated)
                self.assertIsNone(r.reason)
                self.assertEqual(r.inspected, 3)
            finally:
                adapter.close()

    def test_max_results_on_last_entry_at_max_depth(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "sub_match").mkdir()
            adapter = _make_local(root)
            try:
                r = find(
                    adapter,
                    FileRef("src", ROOT_ID),
                    "match",
                    max_results=1,
                    max_depth=0,
                )
                self.assertEqual(len(r.items), 1)
                self.assertEqual(r.items[0].name, "sub_match")
                self.assertTrue(r.truncated)
                self.assertEqual(r.reason, "max_depth")
                self.assertEqual(r.inspected, 1)
            finally:
                adapter.close()

    def test_max_entries_budget_covering_whole_tree_is_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("x")
            (root / "b.txt").write_text("x")
            adapter = _make_local(root)
            try:
                # Budget strictly greater than the folder size: EOF is observed
                # within budget, so the walk is genuinely complete.
                r = find(
                    adapter,
                    FileRef("src", ROOT_ID),
                    "txt",
                    max_entries=3,
                    max_depth=2,
                )
                self.assertEqual(len(r.items), 2)
                self.assertFalse(r.truncated)
                self.assertIsNone(r.reason)
                self.assertEqual(r.inspected, 2)
            finally:
                adapter.close()

    def test_local_exact_budget_is_conservatively_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("x")
            (root / "b.txt").write_text("x")
            adapter = _make_local(root)
            try:
                # A local folder holding exactly the budget is deliberately
                # incomplete: entry B+1 is never probed, so EOF is unknown.
                r = find(
                    adapter,
                    FileRef("src", ROOT_ID),
                    "txt",
                    max_entries=2,
                    max_depth=2,
                )
                self.assertEqual(len(r.items), 2)
                self.assertTrue(r.truncated)
                self.assertEqual(r.reason, "max_entries")
                self.assertEqual(r.inspected, 2)
            finally:
                adapter.close()

    def test_max_entries_exhausted_with_dirs_remaining(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("x")
            (root / "sub").mkdir()
            (root / "sub" / "deep.txt").write_text("x")
            adapter = _make_local(root)
            try:
                r = find(
                    adapter,
                    FileRef("src", ROOT_ID),
                    "txt",
                    max_entries=2,
                    max_depth=2,
                )
                self.assertEqual(r.inspected, 2)
                self.assertTrue(r.truncated)
                self.assertEqual(r.reason, "max_entries")
            finally:
                adapter.close()

    def test_empty_tree(self):
        with tempfile.TemporaryDirectory() as td:
            adapter = _make_local(Path(td))
            try:
                r = find(adapter, FileRef("src", ROOT_ID), "x")
                self.assertEqual(r.items, ())
                self.assertFalse(r.truncated)
                self.assertIsNone(r.reason)
                self.assertEqual(r.inspected, 0)
                self.assertEqual(r.skipped, ())
            finally:
                adapter.close()

    def test_starting_parent_errors_propagate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "f.txt").write_text("x")
            adapter = _make_local(root)
            try:
                with self.assertRaises(NotFoundError):
                    find(adapter, FileRef("src", "p/nope"), "x")
                with self.assertRaises(NotDirectoryError):
                    find(adapter, FileRef("src", "p/f.txt"), "x")
                with self.assertRaises(SourceMismatchError):
                    find(adapter, FileRef("other", ROOT_ID), "x")
            finally:
                adapter.close()

        with tempfile.TemporaryDirectory() as td:
            adapter = _make_local(Path(td))
            adapter.close()
            with self.assertRaises(AdapterClosedError):
                find(adapter, FileRef("src", ROOT_ID), "x")

# ---------------------------------------------------------------------------
# Fake-adapter-backed tests (error injection and budget behaviour)
# ---------------------------------------------------------------------------

class FindFakeAdapterTests(unittest.TestCase):
    def test_large_folder_stops_early_via_adapter_limit(self):
        children = [
            _file(f"f{i}.txt", f"f{i}.txt", ROOT_ID) for i in range(2000)
        ]
        fake = FakeAdapter("fake", {ROOT_ID: children})
        r = find(fake, FileRef("fake", ROOT_ID), "zzz", max_entries=100)
        self.assertTrue(r.truncated)
        self.assertEqual(r.reason, "max_entries")
        # The whole bounded batch (the full remaining budget) is charged.
        self.assertEqual(r.inspected, 100)
        # The adapter received the remaining budget and nothing more.
        self.assertEqual(fake.calls, [(ROOT_ID, 100)])

    def test_over_budget_report_is_rejected_loudly(self):
        # A scanner that ignores its budget and reports more raw work than it
        # was allowed is rejected, never clamped into an accepted result.
        children = [_file(f"f{i}.txt", f"f{i}.txt", ROOT_ID) for i in range(50)]
        fake = FakeAdapter("fake", {ROOT_ID: children}, enforce_limit=False)
        with self.assertRaises(StorageError):
            find(fake, FileRef("fake", ROOT_ID), "zzz", max_entries=10)

    def test_subdirectory_storage_error_skipped(self):
        bad_id = "p/bad"
        good_id = "p/good"
        root_children = [
            _file(bad_id, "bad", ROOT_ID, kind="directory"),
            _file(good_id, "good", ROOT_ID, kind="directory"),
        ]
        good_children = [_file("p/good/x_match.txt", "x_match.txt", good_id)]
        fake = FakeAdapter(
            "fake",
            {ROOT_ID: root_children, good_id: good_children},
            raise_on={bad_id: NotFoundError("gone")},
        )
        r = find(fake, FileRef("fake", ROOT_ID), "match")
        self.assertEqual([m.name for m in r.items], ["x_match.txt"])
        self.assertEqual(r.skipped, (bad_id,))
        self.assertFalse(r.truncated)
        self.assertIsNone(r.reason)

    def test_subdirectory_partial_scan_truncates_not_skipped(self):
        a_id = "p/a"
        b_id = "p/b"
        root_children = [
            _file(a_id, "a", ROOT_ID, kind="directory"),
            _file(b_id, "b", ROOT_ID, kind="directory"),
        ]
        a_children = [
            _file(f"p/a/f{i}.txt", f"f{i}.txt", a_id) for i in range(50)
        ]
        fake = FakeAdapter(
            "fake", {ROOT_ID: root_children, a_id: a_children}
        )
        r = find(fake, FileRef("fake", ROOT_ID), "zzz", max_entries=10, max_depth=4)
        self.assertTrue(r.truncated)
        self.assertEqual(r.reason, "max_entries")
        self.assertEqual(r.skipped, ())
        # 'a' hit its budget mid-folder (a partial, not a failed, scan) and 'b'
        # was never reached: the walk is truncated, not skipped.
        self.assertEqual(r.inspected, 10)
        self.assertEqual([c[0] for c in fake.calls], [ROOT_ID, a_id])
        # 'a' was asked for the remaining budget (10 - 2 inspected).
        self.assertEqual(fake.calls[1], (a_id, 8))

    def test_argument_validation_before_adapter(self):
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

        spy = SpyAdapter()
        ref = FileRef("spy", ROOT_ID)
        bad_kwargs = [
            {"name_contains": ""},
            {"name_contains": None},
            {"name_contains": 123},
            {"name_contains": "x", "max_results": 0},
            {"name_contains": "x", "max_results": 101},
            {"name_contains": "x", "max_results": True},
            {"name_contains": "x", "max_results": 1.5},
            {"name_contains": "x", "max_entries": 0},
            {"name_contains": "x", "max_entries": 100_001},
            {"name_contains": "x", "max_entries": True},
            {"name_contains": "x", "max_depth": -1},
            {"name_contains": "x", "max_depth": 17},
            {"name_contains": "x", "max_depth": True},
            {"name_contains": "x", "case_sensitive": 1},
            {"name_contains": "x", "case_sensitive": "yes"},
        ]
        for kwargs in bad_kwargs:
            with self.assertRaises(ValueError, msg=f"kwargs={kwargs}"):
                find(spy, ref, **kwargs)
        self.assertEqual(calls, [])

if __name__ == "__main__":
    unittest.main()
