"""Edge-case tests for bounded breadth-first search (dt2-cases-1)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from drive_tool.local import (
    LocalAdapter,
    UnsupportedPlatformError,
)
from drive_tool.search import find
from drive_tool.storage import (
    ROOT_ID,
    FileMetadata,
    FileRef,
    ScanBatch,
    ScanFailure,
    StorageError,
)

# ---------------------------------------------------------------------------
# Helpers (same shape as tests/test_search.py)
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
    ``scan_children`` returns a bounded prefix: when a folder holds more than
    the requested ``max_entries`` it returns that many items with
    ``complete=False``. ``raise_on`` maps a ``file_id`` to an exception
    instance that ``scan_children`` raises instead of returning. ``calls``
    records every scan as ``(file_id, max_entries)``.
    """

    def __init__(self, source_id, children_by_id, *, raise_on=None):
        self.source_id = source_id
        self._children = {k: list(v) for k, v in children_by_id.items()}
        self._raise_on = dict(raise_on or {})
        self.calls = []

    def list_children(self, parent, *, max_entries=None):
        raise NotImplementedError

    def scan_children(self, parent, *, max_entries):
        self.calls.append((parent.file_id, max_entries))
        if parent.file_id in self._raise_on:
            raise self._raise_on[parent.file_id]
        items = list(self._children.get(parent.file_id, []))
        if len(items) > max_entries:
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
# CASE 1: empty name_contains
# ---------------------------------------------------------------------------

class EmptyNeedleTests(unittest.TestCase):
    def test_empty_name_contains_raises_value_error(self):
        fake = FakeAdapter("fake", {ROOT_ID: []})
        with self.assertRaises(ValueError) as ctx:
            find(fake, FileRef("fake", ROOT_ID), "")
        self.assertEqual(
            str(ctx.exception), "name_contains must be a non-empty str"
        )
        # Validation happens before the adapter is touched.
        self.assertEqual(fake.calls, [])

# ---------------------------------------------------------------------------
# CASE 2: ScanFailure at the root (depth 0) re-raises failure.error
# ---------------------------------------------------------------------------

class RootScanFailureTests(unittest.TestCase):
    def test_root_scan_failure_reraises_exact_error_object(self):
        error = StorageError("root blew up")
        failure = ScanFailure(inspected=0, error=error)
        fake = FakeAdapter(
            "fake", {ROOT_ID: []}, raise_on={ROOT_ID: failure}
        )
        with self.assertRaises(StorageError) as ctx:
            find(fake, FileRef("fake", ROOT_ID), "x")
        self.assertIs(ctx.exception, error)

# ---------------------------------------------------------------------------
# CASE 3: ScanFailure at depth > 0 is skipped, charges inspected
# ---------------------------------------------------------------------------

class SubdirScanFailureTests(unittest.TestCase):
    def test_subdir_scan_failure_is_skipped_and_charges_inspected(self):
        bad_id = "p/bad"
        good_id = "p/good"
        root_children = [
            _file(bad_id, "bad", ROOT_ID, kind="directory"),
            _file(good_id, "good", ROOT_ID, kind="directory"),
        ]
        good_children = [_file("p/good/x_match.txt", "x_match.txt", good_id)]
        failure = ScanFailure(
            inspected=3, error=StorageError("partial scan failed")
        )
        fake = FakeAdapter(
            "fake",
            {ROOT_ID: root_children, good_id: good_children},
            raise_on={bad_id: failure},
        )
        r = find(fake, FileRef("fake", ROOT_ID), "match")
        self.assertEqual([m.name for m in r.items], ["x_match.txt"])
        self.assertEqual(r.skipped, (bad_id,))
        self.assertTrue(r.truncated)
        self.assertEqual(r.reason, "skipped")
        # Root scan charged 2, then the failed subdirectory's inspected is
        # charged on top; the good subdirectory holds 1 entry.
        self.assertEqual(r.inspected, 2 + 3 + 1)

# ---------------------------------------------------------------------------
# CASE 4: exactly max_results on last entry of complete batch, no queued dirs
# ---------------------------------------------------------------------------

class ExactMaxResultsAtEndTests(unittest.TestCase):
    def test_exact_max_results_on_last_entry_is_not_truncated(self):
        children = [
            _file("f1", "m1.txt", ROOT_ID),
            _file("f2", "m2.txt", ROOT_ID),
        ]
        fake = FakeAdapter("fake", {ROOT_ID: children})
        r = find(fake, FileRef("fake", ROOT_ID), "m", max_results=2)
        self.assertEqual([m.name for m in r.items], ["m1.txt", "m2.txt"])
        self.assertFalse(r.truncated)
        self.assertIsNone(r.reason)
        self.assertEqual(r.inspected, 2)
        self.assertEqual(r.skipped, ())

# ---------------------------------------------------------------------------
# CASE 5: max_depth=0 with subdirectories present
# ---------------------------------------------------------------------------

@unittest.skipUnless(_LOCAL_SUPPORTED, "LocalAdapter unsupported on this platform")
class MaxDepthZeroSubdirsTests(unittest.TestCase):
    def test_max_depth_zero_with_subdirs_truncates_max_depth(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "top.txt").write_text("x")
            (root / "sub1").mkdir()
            (root / "sub1" / "deep.txt").write_text("x")
            (root / "sub2").mkdir()
            adapter = _make_local(root)
            try:
                r = find(
                    adapter, FileRef("src", ROOT_ID), "txt", max_depth=0
                )
                self.assertEqual([m.name for m in r.items], ["top.txt"])
                self.assertTrue(r.truncated)
                self.assertEqual(r.reason, "max_depth")
                self.assertEqual(r.skipped, ())
            finally:
                adapter.close()

if __name__ == "__main__":
    unittest.main()
