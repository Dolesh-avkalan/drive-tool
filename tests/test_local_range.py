"""Contract, boundary, containment and cleanup tests for MODULE-005B.

Covers ``LocalAdapter.read_range`` and the frozen ``RangeReadResult`` value
object: option type/bound validation before any I/O, default/explicit window
selection, empty/small/exact/over-window/beyond-EOF windows, a sparse
larger-than-cap file with a tiny high-offset read, short ``pread`` chunks and
``EINTR`` retries, growth/truncation after the initial ``fstat`` with honest
``eof_observed``, source/state/ref precedence, symlink containment, FIFO
rejection without blocking, the missing-``os.pread`` capability gate,
concurrent independent offsets, and open/fstat/read/close fault injection
including parent-close handoff, preserved cause, cleanup priority and
descriptor accounting.

Observational syscall spies (``_SeamSpy``) and controlled fault injection are
clearly distinguished from real-kernel behaviour; the real-kernel containment
tests reuse the same fixtures as ``tests/test_local.py``.
"""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from drive_tool.local import (
    DEFAULT_MAX_READ_BYTES,
    AdapterClosedError,
    LocalAdapter,
    LocalStorageError,
    RangeReadResult,
    ReadLimitExceededError,
    UnsafePathError,
    UnsupportedEntryError,
    UnsupportedPlatformError,
    _DEFAULT_RANGE_LENGTH,
    _RANGE_MAX_BOUND,
)
from drive_tool.storage import (
    ROOT_ID,
    FileRef,
    IsDirectoryError,
    NotFoundError,
    SourceMismatchError,
    StorageError,
)


def _static_posix_prerequisites_met() -> bool:
    """Return True when the static POSIX facilities the adapter needs exist."""
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_NONBLOCK")
        and os.open in getattr(os, "supports_dir_fd", set())
        and os.stat in getattr(os, "supports_dir_fd", set())
        and os.stat in getattr(os, "supports_follow_symlinks", set())
        and os.scandir in getattr(os, "supports_fd", set())
    )


def _runtime_nofollow_enforced() -> bool:
    from drive_tool.local import _no_follow_semantics_available

    return _no_follow_semantics_available()


class _PosixStaticTestCase(unittest.TestCase):
    """Base case gated only on static POSIX facilities."""

    def setUp(self) -> None:
        if not _static_posix_prerequisites_met():
            self.skipTest("static POSIX dir_fd/O_NOFOLLOW facilities required")
        raw_base = tempfile.mkdtemp(prefix="drive_tool_mod005b_")
        self.base_dir = Path(os.path.realpath(raw_base))
        self.root_dir = self.base_dir / "root"
        self.outside_dir = self.base_dir / "outside"
        self.root_dir.mkdir()
        self.outside_dir.mkdir()

    def tearDown(self) -> None:
        if hasattr(self, "base_dir") and self.base_dir.exists():
            shutil.rmtree(self.base_dir, ignore_errors=True)


class _PosixRuntimeTestCase(_PosixStaticTestCase):
    """Base case for operational tests that actually drive the adapter."""

    def setUp(self) -> None:
        super().setUp()
        if not _runtime_nofollow_enforced():
            self.skipTest(
                "adapter unsupported on this kernel: O_NOFOLLOW is not enforced "
                "(runtime probe returned False)"
            )


class _SeamSpy:
    """Record every call to the adapter's OS seams.

    Used to prove ordering/no-I/O guarantees and to observe ``pread`` requests.
    All recorded calls are delegated to the real seam unless the test replaces
    them, so results remain real-kernel behaviour (the spy is observational).
    """

    _NAMES = (
        "_os_open",
        "_os_fstat",
        "_os_pread",
        "_os_read",
        "_os_close",
        "_os_stat_nofollow",
    )

    def __init__(self, adapter: LocalAdapter) -> None:
        self.a = adapter
        self._real = {n: getattr(adapter, n) for n in self._NAMES}
        self.opens: list = []
        self.fstats: list = []
        self.preads: list = []
        self.reads: list = []
        self.closes: list = []
        self.stats: list = []
        self._stack: list = []

    def __enter__(self) -> "_SeamSpy":
        a = self.a
        self._stack = [
            mock.patch.object(a, "_os_open", side_effect=self._open),
            mock.patch.object(a, "_os_fstat", side_effect=self._fstat),
            mock.patch.object(a, "_os_pread", side_effect=self._pread),
            mock.patch.object(a, "_os_read", side_effect=self._read),
            mock.patch.object(a, "_os_close", side_effect=self._close),
            mock.patch.object(a, "_os_stat_nofollow", side_effect=self._stat),
        ]
        for p in self._stack:
            p.start()
        return self

    def __exit__(self, *exc) -> bool:
        for p in reversed(self._stack):
            p.stop()
        return False

    def _open(self, path, flags, *, dir_fd=None):
        self.opens.append((path, flags, dir_fd))
        return self._real["_os_open"](path, flags, dir_fd=dir_fd)

    def _fstat(self, fd):
        self.fstats.append(fd)
        return self._real["_os_fstat"](fd)

    def _pread(self, fd, length, offset):
        self.preads.append((fd, length, offset))
        return self._real["_os_pread"](fd, length, offset)

    def _read(self, fd, length):
        self.reads.append((fd, length))
        return self._real["_os_read"](fd, length)

    def _close(self, fd):
        self.closes.append(fd)
        return self._real["_os_close"](fd)

    def _stat(self, path, *, dir_fd):
        self.stats.append((path, dir_fd))
        return self._real["_os_stat_nofollow"](path, dir_fd=dir_fd)

    def fs_calls(self) -> int:
        return (
            len(self.opens)
            + len(self.fstats)
            + len(self.preads)
            + len(self.reads)
            + len(self.stats)
        )


class _OwnershipTracker:
    """Lifetime-accurate descriptor accounting (LIFO token per fd number)."""

    def __init__(self) -> None:
        self._next = 0
        self._stacks: dict = {}
        self._info: dict = {}
        self.released: list = []
        self.close_without_open = 0

    def on_open(self, fd, dir_fd):
        token = self._next
        self._next += 1
        self._info[token] = (fd, dir_fd)
        self._stacks.setdefault(fd, []).append(token)
        return token

    def on_close(self, fd):
        stack = self._stacks.get(fd)
        if not stack:
            self.close_without_open += 1
            return None
        token = stack.pop()
        self.released.append(token)
        return token

    def last_token_for(self, fd):
        stack = self._stacks.get(fd)
        return stack[-1] if stack else None

    def release_count(self, token) -> int:
        return self.released.count(token)

    def open_tokens(self) -> list:
        return [t for stack in self._stacks.values() for t in stack]

    def token_for(self, predicate):
        for token, (fd, dir_fd) in self._info.items():
            if predicate(token, fd, dir_fd):
                return token
        return None


def _run_with_timeout(fn, timeout: float = 5.0):
    """Run ``fn`` in a daemon thread; raise if it has not finished in time."""
    box: dict = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
            box["error"] = exc

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise AssertionError("operation did not complete (possible blocking)")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class TestRangeReadResultContract(unittest.TestCase):
    """RangeReadResult is a frozen, exported value object with fixed fields."""

    def test_exported_in_module_all(self) -> None:
        import drive_tool.local as mod

        self.assertIn("RangeReadResult", mod.__all__)
        self.assertIs(mod.RangeReadResult, RangeReadResult)

    def test_fields_and_frozen(self) -> None:
        result = RangeReadResult(
            data=b"abc",
            offset=2,
            observed_size=10,
            observed_mtime_ns=123,
            eof_observed=False,
        )
        self.assertEqual(result.data, b"abc")
        self.assertEqual(result.offset, 2)
        self.assertEqual(result.observed_size, 10)
        self.assertEqual(result.observed_mtime_ns, 123)
        self.assertFalse(result.eof_observed)
        with self.assertRaises(Exception):
            result.offset = 5  # type: ignore[misc]

    def test_len_data_is_returned_byte_count(self) -> None:
        result = RangeReadResult(b"12345", 0, 5, 0, True)
        self.assertEqual(len(result.data), 5)


class TestRangeOptionValidation(_PosixRuntimeTestCase):
    """Option types/bounds fail before any syscall for this operation."""

    def _adapter(self) -> LocalAdapter:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        return LocalAdapter("s", self.root_dir, max_read_bytes=8)

    def test_offset_type_errors(self) -> None:
        a = self._adapter()
        try:
            for bad in (True, False, 1.0, "0", None, b"0"):
                with self.assertRaises(TypeError):
                    a.read_range(FileRef("s", "p/f.bin"), offset=bad)
        finally:
            a.close()

    def test_offset_range_errors(self) -> None:
        a = self._adapter()
        try:
            for bad in (-1, -100, _RANGE_MAX_BOUND + 1, 2**63):
                with self.assertRaises(ValueError):
                    a.read_range(FileRef("s", "p/f.bin"), offset=bad)
        finally:
            a.close()

    def test_length_type_errors(self) -> None:
        a = self._adapter()
        try:
            for bad in (True, False, 1.5, "2", b"2"):
                with self.assertRaises(TypeError):
                    a.read_range(FileRef("s", "p/f.bin"), length=bad)
        finally:
            a.close()

    def test_length_range_errors(self) -> None:
        a = self._adapter()
        try:
            for bad in (0, -1, 9, 10**6):  # max_read_bytes is 8
                with self.assertRaises(ValueError):
                    a.read_range(FileRef("s", "p/f.bin"), length=bad)
        finally:
            a.close()

    def test_length_equal_to_cap_is_accepted(self) -> None:
        a = self._adapter()
        try:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=8)
            self.assertEqual(res.data, b"01234567")
        finally:
            a.close()

    def test_offset_plus_length_over_int64_bound_rejected(self) -> None:
        a = self._adapter()
        try:
            with self.assertRaises(ValueError):
                a.read_range(
                    FileRef("s", "p/f.bin"), offset=_RANGE_MAX_BOUND, length=1
                )
            with self.assertRaises(ValueError):
                a.read_range(
                    FileRef("s", "p/f.bin"),
                    offset=_RANGE_MAX_BOUND - 1,
                    length=2,
                )
        finally:
            a.close()

    def test_offset_near_bound_with_small_length_is_allowed(self) -> None:
        a = self._adapter()
        try:
            res = a.read_range(
                FileRef("s", "p/f.bin"), offset=_RANGE_MAX_BOUND - 1, length=1
            )
            self.assertEqual(res.data, b"")
            self.assertTrue(res.eof_observed)
        finally:
            a.close()

    def test_invalid_options_make_no_syscalls(self) -> None:
        a = self._adapter()
        try:
            with _SeamSpy(a) as spy:
                with self.assertRaises(TypeError):
                    a.read_range(FileRef("s", "p/f.bin"), offset=True)
                with self.assertRaises(ValueError):
                    a.read_range(FileRef("s", "p/f.bin"), offset=-1)
                with self.assertRaises(TypeError):
                    a.read_range(FileRef("s", "p/f.bin"), length="x")
                with self.assertRaises(ValueError):
                    a.read_range(FileRef("s", "p/f.bin"), length=0)
                with self.assertRaises(ValueError):
                    a.read_range(
                        FileRef("s", "p/f.bin"), offset=_RANGE_MAX_BOUND, length=1
                    )
                self.assertEqual(spy.fs_calls(), 0)
        finally:
            a.close()

    def test_source_mismatch_precedes_option_validation(self) -> None:
        a = self._adapter()
        try:
            with _SeamSpy(a) as spy:
                with self.assertRaises(SourceMismatchError):
                    a.read_range(FileRef("other", "p/f.bin"), offset=-1)
                self.assertEqual(spy.fs_calls(), 0)
        finally:
            a.close()

    def test_closed_adapter_precedes_option_validation(self) -> None:
        a = self._adapter()
        a.close()
        with self.assertRaises(AdapterClosedError):
            a.read_range(FileRef("s", "p/f.bin"), offset=-1)

    def test_non_fileref_raises_type_error(self) -> None:
        a = self._adapter()
        try:
            with self.assertRaises(TypeError):
                a.read_range("p/f.bin")  # type: ignore[arg-type]
        finally:
            a.close()

    def test_malformed_id_precedes_option_validation(self) -> None:
        # Ref checking matches read_bytes: a malformed id is rejected before
        # options are examined.
        a = self._adapter()
        try:
            with self.assertRaises(UnsafePathError):
                a.read_range(FileRef("s", "p/../escape"), offset=-1)
        finally:
            a.close()

    def test_root_id_rejection_matches_read_bytes(self) -> None:
        a = self._adapter()
        try:
            with self.assertRaises(IsDirectoryError):
                a.read_range(FileRef("s", ROOT_ID))
        finally:
            a.close()


class TestRangeWindowSelection(_PosixRuntimeTestCase):
    """Default and explicit window selection."""

    def test_default_window_is_min_262144_and_cap(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"x")
        self.assertEqual(_DEFAULT_RANGE_LENGTH, 262_144)

        def _total_requested(adapter: LocalAdapter) -> int:
            # A full-returning pread makes the loop run exactly the effective
            # window (bounded by the per-call cap), so the summed requests equal
            # the selected window.
            total = 0

            def full(fd, length, offset):
                nonlocal total
                total += length
                return b"z" * length

            with mock.patch.object(adapter, "_os_pread", side_effect=full):
                adapter.read_range(FileRef("s", "p/f.bin"))
            return total

        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            self.assertEqual(_total_requested(a), 100)
        with LocalAdapter("s", self.root_dir) as a:
            self.assertEqual(_total_requested(a), _DEFAULT_RANGE_LENGTH)

    def test_explicit_window_honoured(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"abcdefghij")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            with _SeamSpy(a) as spy:
                a.read_range(FileRef("s", "p/f.bin"), offset=2, length=3)
                self.assertEqual(spy.preads, [(spy.preads[0][0], 3, 2)])

    def test_window_chunked_at_most_65536(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"a" * 200_000)
        with LocalAdapter("s", self.root_dir) as a:
            with _SeamSpy(a) as spy:
                res = a.read_range(FileRef("s", "p/f.bin"), length=200_000)
                self.assertEqual(len(res.data), 200_000)
                lengths = [p[1] for p in spy.preads]
                self.assertEqual(lengths, [65_536, 65_536, 65_536, 3_392])
                self.assertTrue(all(1 <= ln <= 65_536 for ln in lengths))
                # Offsets advance by the collected bytes, starting at 0.
                offsets = [p[2] for p in spy.preads]
                self.assertEqual(offsets, [0, 65_536, 131_072, 196_608])


class TestRangeContentWindows(_PosixRuntimeTestCase):
    """Empty/small/exact/over-window/beyond-EOF content windows."""

    def test_empty_file(self) -> None:
        (self.root_dir / "empty.bin").write_bytes(b"")
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/empty.bin"))
            self.assertEqual(res.data, b"")
            self.assertTrue(res.eof_observed)
            self.assertEqual(res.observed_size, 0)
            self.assertEqual(res.offset, 0)

    def test_small_file_returns_all_and_observes_eof(self) -> None:
        (self.root_dir / "s.bin").write_bytes(b"hello")
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/s.bin"))
            self.assertEqual(res.data, b"hello")
            self.assertTrue(res.eof_observed)
            self.assertEqual(res.observed_size, 5)

    def test_over_window_returns_exactly_length(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=4)
            self.assertEqual(res.data, b"0123")
            self.assertFalse(res.eof_observed)
            self.assertEqual(res.offset, 0)

    def test_middle_window(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=3, length=4)
            self.assertEqual(res.data, b"3456")
            self.assertEqual(res.offset, 3)
            self.assertFalse(res.eof_observed)

    def test_exact_window_that_fills_request_is_not_eof(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=10)
            self.assertEqual(res.data, b"0123456789")
            self.assertFalse(res.eof_observed)

    def test_window_one_past_end_is_eof(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=11)
            self.assertEqual(res.data, b"0123456789")
            self.assertTrue(res.eof_observed)

    def test_offset_equal_to_size_is_eof(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=10, length=5)
            self.assertEqual(res.data, b"")
            self.assertTrue(res.eof_observed)

    def test_offset_beyond_eof_is_eof(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=1000, length=5)
            self.assertEqual(res.data, b"")
            self.assertTrue(res.eof_observed)
            self.assertEqual(res.observed_size, 10)


class TestRangeSparseAndLargerThanCap(_PosixRuntimeTestCase):
    """A sparse larger-than-cap file supports a tiny high-offset range read."""

    def _make_sparse(self) -> None:
        big = self.root_dir / "big.bin"
        with open(big, "wb") as f:
            f.truncate(1_048_576)  # 1 MiB logical, sparse
            f.seek(1_000_000)
            f.write(b"TAIL")

    def test_tiny_high_offset_read_on_larger_than_cap_file(self) -> None:
        self._make_sparse()
        with LocalAdapter("s", self.root_dir, max_read_bytes=16) as a:
            res = a.read_range(FileRef("s", "p/big.bin"), offset=1_000_000, length=4)
            self.assertEqual(res.data, b"TAIL")
            self.assertFalse(res.eof_observed)
            self.assertEqual(res.observed_size, 1_048_576)
            # A zero byte inside the sparse hole is observable.
            hole = a.read_range(FileRef("s", "p/big.bin"), offset=999_999, length=1)
            self.assertEqual(hole.data, b"\x00")

    def test_whole_read_still_denied_for_larger_than_cap_file(self) -> None:
        self._make_sparse()
        with LocalAdapter("s", self.root_dir, max_read_bytes=16) as a:
            with self.assertRaises(ReadLimitExceededError):
                a.read_bytes(FileRef("s", "p/big.bin"))
            # A range window larger than the per-call cap is still rejected.
            with self.assertRaises(ValueError):
                a.read_range(FileRef("s", "p/big.bin"), length=17)


class TestRangeShortPreadAndEintr(_PosixRuntimeTestCase):
    """Short nonempty preads continue; EINTR retries; no zero-length pread."""

    def test_short_nonempty_reads_continue_to_fill_window(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"abcdefghij")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            real = a._os_pread
            calls: list = []

            def one_byte(fd, length, offset):
                calls.append((length, offset))
                return real(fd, min(1, length), offset)

            with mock.patch.object(a, "_os_pread", side_effect=one_byte):
                res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=5)
            self.assertEqual(res.data, b"abcde")
            self.assertEqual(len(calls), 5)  # one byte per call, never stopping early
            self.assertTrue(all(ln >= 1 for ln, _ in calls))

    def test_eintr_is_retried(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"abcdefghij")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            real = a._os_pread
            state = {"n": 0}

            def flaky(fd, length, offset):
                state["n"] += 1
                if state["n"] <= 2:
                    raise OSError(errno.EINTR, "interrupted")
                return real(fd, length, offset)

            with mock.patch.object(a, "_os_pread", side_effect=flaky):
                res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=4)
            self.assertEqual(res.data, b"abcd")
            self.assertGreaterEqual(state["n"], 3)

    def test_never_issues_zero_length_pread(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"abcdefghij")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            with _SeamSpy(a) as spy:
                a.read_range(FileRef("s", "p/f.bin"), offset=0, length=6)
                self.assertTrue(spy.preads)
                self.assertTrue(all(ln >= 1 for _, ln, _ in spy.preads))

    def test_no_lookahead_past_requested_window(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            with _SeamSpy(a) as spy:
                a.read_range(FileRef("s", "p/f.bin"), offset=3, length=4)
                # Exactly one 4-byte pread at offset 3; no probe past the window.
                self.assertEqual(len(spy.preads), 1)
                self.assertEqual(spy.preads[0][1:], (4, 3))

    def test_read_range_never_uses_the_read_seam(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"abcdefghij")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            def forbidden(fd, length):
                raise AssertionError("read_range must not call _os_read")

            with mock.patch.object(a, "_os_read", side_effect=forbidden):
                res = a.read_range(FileRef("s", "p/f.bin"), offset=2, length=3)
            self.assertEqual(res.data, b"cde")


class TestRangeEofHonesty(_PosixRuntimeTestCase):
    """eof_observed reflects pread results, not stale metadata."""

    def test_growth_after_fstat_can_yield_data(self) -> None:
        # Initial fstat observes size 0, but the file grows before the read.
        (self.root_dir / "grow.bin").write_bytes(b"")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            real = a._os_pread
            seq = {"n": 0}

            def growing(fd, length, offset):
                seq["n"] += 1
                if seq["n"] == 1:
                    return b"abc"
                return real(fd, length, offset)

            with mock.patch.object(a, "_os_pread", side_effect=growing):
                res = a.read_range(FileRef("s", "p/grow.bin"), offset=0, length=10)
            self.assertEqual(res.data, b"abc")
            self.assertTrue(res.eof_observed)
            self.assertEqual(res.observed_size, 0)  # observation only

    def test_truncation_after_fstat_yields_empty_and_eof(self) -> None:
        (self.root_dir / "t.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            def emptied(fd, length, offset):
                return b""

            with mock.patch.object(a, "_os_pread", side_effect=emptied):
                res = a.read_range(FileRef("s", "p/t.bin"), offset=0, length=5)
            self.assertEqual(res.data, b"")
            self.assertTrue(res.eof_observed)
            self.assertEqual(res.observed_size, 10)  # stale observation retained

    def test_eof_not_derived_from_observed_size(self) -> None:
        # File is 10 bytes; a 10-byte window exactly fills it but is not EOF.
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=10)
            self.assertEqual(res.observed_size, 10)
            self.assertFalse(res.eof_observed)


class TestRangeSourceStateRef(_PosixRuntimeTestCase):
    """Source/state/ref and entry-class rejection mirrors read_bytes."""

    def setUp(self) -> None:
        super().setUp()
        (self.root_dir / "f.bin").write_bytes(b"data")
        (self.root_dir / "dir").mkdir()
        (self.root_dir / "dir" / "inner.bin").write_bytes(b"inner")

    def test_source_mismatch(self) -> None:
        with LocalAdapter("s", self.root_dir) as a:
            with self.assertRaises(SourceMismatchError):
                a.read_range(FileRef("other", "p/f.bin"))

    def test_closed_adapter(self) -> None:
        a = LocalAdapter("s", self.root_dir)
        a.close()
        with self.assertRaises(AdapterClosedError):
            a.read_range(FileRef("s", "p/f.bin"))

    def test_root_is_directory(self) -> None:
        with LocalAdapter("s", self.root_dir) as a:
            with self.assertRaises(IsDirectoryError):
                a.read_range(FileRef("s", ROOT_ID))

    def test_directory_target_is_directory_error(self) -> None:
        with LocalAdapter("s", self.root_dir) as a:
            with self.assertRaises(IsDirectoryError):
                a.read_range(FileRef("s", "p/dir"))

    def test_missing_file_not_found(self) -> None:
        with LocalAdapter("s", self.root_dir) as a:
            with self.assertRaises(NotFoundError):
                a.read_range(FileRef("s", "p/missing.bin"))

    def test_invalid_ids_rejected(self) -> None:
        with LocalAdapter("s", self.root_dir) as a:
            for bad in ("f.bin", "p/", "p/a/../b", "p//b", "/p/f.bin", "p/f\x00"):
                with self.assertRaises(UnsafePathError):
                    a.read_range(FileRef("s", bad))

    def test_nested_file(self) -> None:
        with LocalAdapter("s", self.root_dir) as a:
            res = a.read_range(FileRef("s", "p/dir/inner.bin"))
            self.assertEqual(res.data, b"inner")

    def test_intermediate_component_is_file_not_directory(self) -> None:
        with LocalAdapter("s", self.root_dir) as a:
            from drive_tool.storage import NotDirectoryError

            with self.assertRaises(NotDirectoryError):
                a.read_range(FileRef("s", "p/f.bin/child"))


class TestRangeSymlinkContainment(_PosixRuntimeTestCase):
    """Symlinks at final/intermediate components are rejected; root stays anchored."""

    def test_final_symlink_rejected(self) -> None:
        secret = self.outside_dir / "secret.txt"
        secret.write_bytes(b"secret")
        os.symlink(secret, self.root_dir / "link")
        with LocalAdapter("s", self.root_dir) as a:
            with self.assertRaises(UnsafePathError):
                a.read_range(FileRef("s", "p/link"))

    def test_intermediate_symlink_rejected(self) -> None:
        inside = self.root_dir / "real"
        inside.mkdir()
        (inside / "f.bin").write_bytes(b"x")
        os.symlink(self.outside_dir, self.root_dir / "mid")
        with LocalAdapter("s", self.root_dir) as a:
            with self.assertRaises(UnsafePathError):
                a.read_range(FileRef("s", "p/mid/secret.txt"))

    def test_root_renamed_and_replaced_by_outside_symlink_stays_anchored(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"anchored")
        with LocalAdapter("s", self.root_dir) as a:
            os.rename(self.root_dir, self.base_dir / "moved_root")
            os.symlink(self.outside_dir, self.root_dir)
            try:
                res = a.read_range(FileRef("s", "p/f.bin"))
                self.assertEqual(res.data, b"anchored")
            finally:
                os.unlink(self.root_dir)

    def test_adversarial_swap_target_for_symlink_before_open(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"real")
        secret = self.outside_dir / "secret.txt"
        secret.write_bytes(b"secret")
        with LocalAdapter("s", self.root_dir) as a:
            real_open = a._os_open

            def swap(path, flags, *, dir_fd=None):
                if path == "f.bin":
                    os.unlink(self.root_dir / "f.bin")
                    os.symlink(secret, self.root_dir / "f.bin")
                return real_open(path, flags, dir_fd=dir_fd)

            with mock.patch.object(a, "_os_open", side_effect=swap):
                with self.assertRaises(UnsafePathError):
                    a.read_range(FileRef("s", "p/f.bin"))


class TestRangeSpecialEntries(_PosixRuntimeTestCase):
    """FIFOs are rejected without blocking."""

    def test_fifo_rejected_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("os.mkfifo not available")
        os.mkfifo(self.root_dir / "pipe")
        with LocalAdapter("s", self.root_dir) as a:
            with self.assertRaises(UnsupportedEntryError):
                _run_with_timeout(
                    lambda: a.read_range(FileRef("s", "p/pipe"))
                )


class TestRangePreadCapability(_PosixRuntimeTestCase):
    """A missing os.pread fails closed without breaking construction/read_bytes."""

    def test_missing_pread_raises_unsupported_platform_error(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"data")
        with LocalAdapter("s", self.root_dir) as a:
            with _SeamSpy(a) as spy:
                with mock.patch("drive_tool.local.os.pread", None, create=True):
                    with self.assertRaises(UnsupportedPlatformError):
                        a.read_range(FileRef("s", "p/f.bin"))
                self.assertEqual(spy.fs_calls(), 0)

    def test_read_bytes_and_construction_still_work_without_pread(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"data")
        with mock.patch("drive_tool.local.os.pread", None, create=True):
            with LocalAdapter("s", self.root_dir) as a:
                self.assertEqual(a.read_bytes(FileRef("s", "p/f.bin")), b"data")
                self.assertEqual(a.stat(FileRef("s", "p/f.bin")).size_bytes, 4)


class TestRangeConcurrency(_PosixRuntimeTestCase):
    """Independent per-call descriptors support concurrent range reads."""

    def test_concurrent_reads_with_independent_offsets(self) -> None:
        payload = bytes(range(256)) * 4  # 1024 bytes
        (self.root_dir / "c.bin").write_bytes(payload)
        windows = [(0, 10), (100, 20), (500, 50), (1000, 24), (255, 1)]
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            results: dict = {}
            errors: list = []
            barrier = threading.Barrier(len(windows))

            def worker(off, ln):
                try:
                    barrier.wait(timeout=5)
                    results[(off, ln)] = a.read_range(
                        FileRef("s", "p/c.bin"), offset=off, length=ln
                    ).data
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [
                threading.Thread(target=worker, args=(o, ln)) for o, ln in windows
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(errors, [])
            for off, ln in windows:
                self.assertEqual(results[(off, ln)], payload[off : off + ln])

    def test_repeated_calls_do_not_disturb_each_other(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            first = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=4).data
            second = a.read_range(FileRef("s", "p/f.bin"), offset=6, length=4).data
            third = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=4).data
            self.assertEqual((first, second, third), (b"0123", b"6789", b"0123"))


class TestRangeFaultInjectionAndCleanup(_PosixRuntimeTestCase):
    """Open/fstat/read/close failures, preserved cause, priority, fd accounting."""

    def _tree(self) -> None:
        (self.root_dir / "a").mkdir()
        (self.root_dir / "a" / "file.txt").write_bytes(b"AAAA")

    def test_target_open_failure_wrapped_with_cause(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"data")
        with LocalAdapter("s", self.root_dir) as a:
            real = a._os_open

            def failing(path, flags, *, dir_fd=None):
                if path == "f.bin":
                    raise OSError(errno.EIO, "injected open failure")
                return real(path, flags, dir_fd=dir_fd)

            with mock.patch.object(a, "_os_open", side_effect=failing):
                with self.assertRaises(LocalStorageError) as ctx:
                    a.read_range(FileRef("s", "p/f.bin"))
            self.assertIsInstance(ctx.exception.__cause__, OSError)
            self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)

    def test_fstat_failure_wrapped_with_cause(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"data")
        with LocalAdapter("s", self.root_dir) as a:
            real = a._os_fstat

            def failing(fd):
                if fd != a._root_fd:
                    raise OSError(errno.EIO, "injected fstat failure")
                return real(fd)

            with mock.patch.object(a, "_os_fstat", side_effect=failing):
                with self.assertRaises(LocalStorageError) as ctx:
                    a.read_range(FileRef("s", "p/f.bin"))
            self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)

    def test_pread_failure_wrapped_with_cause(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"data")
        with LocalAdapter("s", self.root_dir) as a:
            def failing(fd, length, offset):
                raise OSError(errno.EIO, "injected pread failure")

            with mock.patch.object(a, "_os_pread", side_effect=failing):
                with self.assertRaises(LocalStorageError) as ctx:
                    a.read_range(FileRef("s", "p/f.bin"))
            self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)

    def test_pread_failure_after_partial_data_is_not_a_partial_success(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            real = a._os_pread
            seq = {"n": 0}

            def partial_then_fail(fd, length, offset):
                seq["n"] += 1
                if seq["n"] == 1:
                    return real(fd, min(2, length), offset)
                raise OSError(errno.EIO, "injected mid-read failure")

            with mock.patch.object(a, "_os_pread", side_effect=partial_then_fail):
                with self.assertRaises(LocalStorageError):
                    a.read_range(FileRef("s", "p/f.bin"), offset=0, length=6)

    def test_success_path_close_failure_wrapped(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"data")
        with LocalAdapter("s", self.root_dir) as a:
            real = a._os_close

            def failing(fd):
                if fd != a._root_fd:
                    real(fd)  # Linux releases the fd regardless
                    raise OSError(errno.EIO, "injected close failure")
                real(fd)

            with mock.patch.object(a, "_os_close", side_effect=failing):
                with self.assertRaises(LocalStorageError) as ctx:
                    a.read_range(FileRef("s", "p/f.bin"))
            self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)

    def test_primary_pread_error_wins_over_close_failure(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"data")
        with LocalAdapter("s", self.root_dir) as a:
            real_close = a._os_close

            def close_failing(fd):
                if fd != a._root_fd:
                    real_close(fd)
                    raise OSError(errno.EIO, "injected close failure")
                real_close(fd)

            def pread_failing(fd, length, offset):
                raise OSError(errno.EIO, "injected pread failure")

            with (
                mock.patch.object(a, "_os_pread", side_effect=pread_failing),
                mock.patch.object(a, "_os_close", side_effect=close_failing),
            ):
                with self.assertRaises(LocalStorageError) as ctx:
                    a.read_range(FileRef("s", "p/f.bin"))
            # The read failure is the primary error; the close failure is suppressed.
            self.assertIn("reading range", str(ctx.exception))
            self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)

    def test_parent_handoff_close_failure_releases_child(self) -> None:
        self._tree()
        a = LocalAdapter("s", self.root_dir, max_read_bytes=100)
        tracker = _OwnershipTracker()
        parent_box: dict = {}
        real_open = a._os_open
        real_close = a._os_close
        real_trav = a._traverse_parent_dir

        def trav(rfd, dir_parts, file_id):
            fd = real_trav(rfd, dir_parts, file_id)
            parent_box["fd"] = fd
            parent_box["token"] = tracker.last_token_for(fd)
            return fd

        def open_hook(path, flags, *, dir_fd=None):
            fd = real_open(path, flags, dir_fd=dir_fd)
            tracker.on_open(fd, dir_fd)
            return fd

        def close_hook(fd):
            tracker.on_close(fd)
            real_close(fd)
            if parent_box.get("fd") == fd:
                raise OSError(errno.EIO, "injected parent-handoff close failure")

        a._traverse_parent_dir = trav
        a._os_open = open_hook
        a._os_close = close_hook
        exc = None
        try:
            try:
                a.read_range(FileRef("s", "p/a/file.txt"))
            except LocalStorageError as e:
                exc = e
        finally:
            a._traverse_parent_dir = real_trav
            a._os_open = real_open
            a._os_close = real_close
        try:
            self.assertIsNotNone(exc, "close failure must surface")
            self.assertIsInstance(exc.__cause__, OSError)
            self.assertEqual(exc.__cause__.errno, errno.EIO)
            parent_fd = parent_box["fd"]
            child_token = tracker.token_for(
                lambda t, fd, dfd: dfd == parent_fd and fd != parent_fd
            )
            self.assertIsNotNone(child_token, "child fd must have been acquired")
            self.assertEqual(tracker.release_count(child_token), 1)
            self.assertEqual(tracker.close_without_open, 0)
            self.assertEqual(tracker.open_tokens(), [])
            # Root ownership intact; adapter still usable.
            self.assertEqual(
                a.read_range(FileRef("s", "p/a/file.txt")).data, b"AAAA"
            )
        finally:
            a.close()

    def test_no_fd_growth_across_success_and_failures(self) -> None:
        proc_fd = Path("/proc/self/fd")
        if not proc_fd.is_dir():
            self.skipTest("/proc/self/fd not available")
        self._tree()
        os.symlink(self.outside_dir, self.root_dir / "sym")
        baseline = len(os.listdir(proc_fd))
        a = LocalAdapter("s", self.root_dir, max_read_bytes=100)
        try:
            self.assertEqual(len(os.listdir(proc_fd)), baseline + 1)
            for _ in range(25):
                a.read_range(FileRef("s", "p/a/file.txt"))
                a.read_range(FileRef("s", "p/a/file.txt"), offset=1, length=2)
                with self.assertRaises(NotFoundError):
                    a.read_range(FileRef("s", "p/a/missing.txt"))
                with self.assertRaises(UnsafePathError):
                    a.read_range(FileRef("s", "p/sym/escape"))
                with self.assertRaises(IsDirectoryError):
                    a.read_range(FileRef("s", "p/a"))
                with self.assertRaises(ValueError):
                    a.read_range(FileRef("s", "p/a/file.txt"), length=0)
            self.assertEqual(len(os.listdir(proc_fd)), baseline + 1)
        finally:
            a.close()
        self.assertEqual(len(os.listdir(proc_fd)), baseline)


if __name__ == "__main__":
    unittest.main()
