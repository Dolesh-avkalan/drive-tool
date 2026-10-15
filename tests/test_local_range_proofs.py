"""Adversarial proofs for MODULE-005B ``LocalAdapter.read_range``.

These tests exist to prove the *guards* the brief requires, using observational
syscall spies and controlled fault injection (never real credentials or real
roots). They are deliberately distinct from the contract tests in
``tests/test_local_range.py``:

* the window is never exceeded and never probed past (no lookahead);
* ``read_range`` never falls back to ``read_bytes``-and-slice or a prefix load;
* a short nonempty ``pread`` never stops the loop early;
* an exactly-filled window is never reported as EOF;
* the target is opened with ``O_NOFOLLOW`` and a swapped symlink cannot escape;
* every acquired descriptor is released exactly once and a lost-fd regression
  is caught;
* concurrent calls each open their own descriptor.
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
    LocalAdapter,
    LocalStorageError,
    UnsafePathError,
)
from drive_tool.storage import FileRef, NotFoundError


def _static_posix_prerequisites_met() -> bool:
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


class _PosixRuntimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not _static_posix_prerequisites_met():
            self.skipTest("static POSIX dir_fd/O_NOFOLLOW facilities required")
        if not _runtime_nofollow_enforced():
            self.skipTest(
                "adapter unsupported on this kernel: O_NOFOLLOW not enforced"
            )
        raw_base = tempfile.mkdtemp(prefix="drive_tool_mod005b_proof_")
        self.base_dir = Path(os.path.realpath(raw_base))
        self.root_dir = self.base_dir / "root"
        self.outside_dir = self.base_dir / "outside"
        self.root_dir.mkdir()
        self.outside_dir.mkdir()

    def tearDown(self) -> None:
        if hasattr(self, "base_dir") and self.base_dir.exists():
            shutil.rmtree(self.base_dir, ignore_errors=True)


class _PreadRecorder:
    """Wrap ``_os_pread`` to record requests and returned byte counts."""

    def __init__(self, adapter: LocalAdapter) -> None:
        self.a = adapter
        self.real = adapter._os_pread
        self.requests: list = []  # (length, offset)
        self.returned: list = []  # len(chunk) per call

    def __enter__(self) -> "_PreadRecorder":
        self._patch = mock.patch.object(self.a, "_os_pread", side_effect=self._call)
        self._patch.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._patch.stop()
        return False

    def _call(self, fd, length, offset):
        chunk = self.real(fd, length, offset)
        self.requests.append((length, offset))
        self.returned.append(len(chunk))
        return chunk

    @property
    def total_returned(self) -> int:
        return sum(self.returned)


class _SeamSpy:
    """Record every call to the adapter's OS seams (observational)."""

    _NAMES = ("_os_open", "_os_fstat", "_os_pread", "_os_read", "_os_close")

    def __init__(self, adapter: LocalAdapter) -> None:
        self.a = adapter
        self._real = {n: getattr(adapter, n) for n in self._NAMES}
        self.opens: list = []
        self.reads: list = []
        self._stack: list = []

    def __enter__(self) -> "_SeamSpy":
        self._stack = [
            mock.patch.object(self.a, "_os_open", side_effect=self._open),
            mock.patch.object(self.a, "_os_read", side_effect=self._read),
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

    def _read(self, fd, length):
        self.reads.append((fd, length))
        return self._real["_os_read"](fd, length)


class _OwnershipTracker:
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

    def open_tokens(self) -> list:
        return [t for stack in self._stacks.values() for t in stack]


class TestNoLookaheadProofs(_PosixRuntimeTestCase):
    """The window is never exceeded and never probed past."""

    def test_total_returned_bytes_never_exceed_window(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"a" * 300_000)
        with LocalAdapter("s", self.root_dir) as a:
            with _PreadRecorder(a) as rec:
                res = a.read_range(FileRef("s", "p/f.bin"), length=200_000)
            self.assertEqual(rec.total_returned, 200_000)
            self.assertEqual(len(res.data), 200_000)
            self.assertTrue(all(ln <= 65_536 for ln, _ in rec.requests))
            # Requested offsets never advance past the requested window end.
            for ln, off in rec.requests:
                self.assertLessEqual(off + ln, 200_000)

    def test_no_pread_after_eof_is_observed(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            with _PreadRecorder(a) as rec:
                a.read_range(FileRef("s", "p/f.bin"), offset=0, length=50)
            # 10 bytes then one empty pread; no call after the empty one.
            self.assertEqual(rec.returned[-1], 0)
            self.assertEqual(rec.returned.count(0), 1)

    def test_never_reads_before_requested_offset(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"x" * 100_000)
        with LocalAdapter("s", self.root_dir) as a:
            with _PreadRecorder(a) as rec:
                a.read_range(FileRef("s", "p/f.bin"), offset=90_000, length=100)
            self.assertEqual(rec.requests[0], (100, 90_000))
            self.assertTrue(all(off >= 90_000 for _, off in rec.requests))

    def test_request_never_unbounded(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"a" * 500_000)
        with LocalAdapter("s", self.root_dir) as a:
            with _PreadRecorder(a) as rec:
                a.read_range(FileRef("s", "p/f.bin"), length=500_000)
            self.assertTrue(rec.requests)
            self.assertTrue(all(1 <= ln <= 65_536 for ln, _ in rec.requests))


class TestNoReadBytesFallbackProofs(_PosixRuntimeTestCase):
    """read_range must not use read_bytes-and-slice or load the prefix."""

    def test_read_seam_never_invoked(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            with _SeamSpy(a) as spy:

                def forbidden(fd, length):
                    raise AssertionError("_os_read (read_bytes seam) was called")

                with mock.patch.object(a, "_os_read", side_effect=forbidden):
                    res = a.read_range(FileRef("s", "p/f.bin"), offset=4, length=3)
            self.assertEqual(res.data, b"456")
            self.assertEqual(spy.reads, [])


class TestShortReadProofs(_PosixRuntimeTestCase):
    """A short nonempty pread must not stop the loop early."""

    def test_one_byte_chunks_fill_the_whole_window(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"abcdefghij")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            real = a._os_pread
            calls = {"n": 0}

            def one(fd, length, offset):
                calls["n"] += 1
                return real(fd, min(1, length), offset)

            with mock.patch.object(a, "_os_pread", side_effect=one):
                res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=6)
            self.assertEqual(res.data, b"abcdef")
            self.assertEqual(calls["n"], 6)  # continued after each short read


class TestExactWindowEofProofs(_PosixRuntimeTestCase):
    """An exactly-filled window is never reported as EOF."""

    def test_exact_fill_is_not_eof_even_though_size_matches(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=10)
            self.assertEqual(res.observed_size, 10)
            self.assertEqual(len(res.data), 10)
            self.assertFalse(res.eof_observed)

    def test_offset_beyond_eof_is_not_rejected(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            res = a.read_range(FileRef("s", "p/f.bin"), offset=50_000, length=4)
            self.assertEqual(res.data, b"")
            self.assertTrue(res.eof_observed)


class TestNoFollowProofs(_PosixRuntimeTestCase):
    """The target open carries O_NOFOLLOW; a swapped symlink cannot escape."""

    def test_target_open_uses_no_follow_flags(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"data")
        with LocalAdapter("s", self.root_dir) as a:
            with _SeamSpy(a) as spy:
                a.read_range(FileRef("s", "p/f.bin"))
            target_opens = [f for (p, f, _dfd) in spy.opens if p == "f.bin"]
            self.assertEqual(len(target_opens), 1)
            flags = target_opens[0]
            # O_RDONLY is 0 on Linux, so it cannot be asserted by bitmask; the
            # meaningful guards are O_NOFOLLOW present and O_DIRECTORY absent.
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertFalse(flags & getattr(os, "O_DIRECTORY", 0))
            self.assertTrue(flags & os.O_NONBLOCK)

    def test_swapped_symlink_cannot_escape(self) -> None:
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


class TestDescriptorOwnershipProofs(_PosixRuntimeTestCase):
    """Every acquired descriptor is released exactly once."""

    def _run_tracked(self, ops) -> None:
        (self.root_dir / "a").mkdir()
        (self.root_dir / "a" / "f.bin").write_bytes(b"abcdefghij")
        os.symlink(self.outside_dir, self.root_dir / "a" / "sym")
        a = LocalAdapter("s", self.root_dir, max_read_bytes=100)
        tracker = _OwnershipTracker()
        real_open = a._os_open
        real_close = a._os_close

        def open_hook(path, flags, *, dir_fd=None):
            fd = real_open(path, flags, dir_fd=dir_fd)
            tracker.on_open(fd, dir_fd)
            return fd

        def close_hook(fd):
            tracker.on_close(fd)
            real_close(fd)

        a._os_open = open_hook
        a._os_close = close_hook
        try:
            for op in ops:
                op(a)
            self.assertEqual(tracker.close_without_open, 0)
            self.assertEqual(tracker.open_tokens(), [])
        finally:
            a._os_open = real_open
            a._os_close = real_close
            a.close()

    def test_success_and_failure_release_every_fd_once(self) -> None:
        def good(a):
            a.read_range(FileRef("s", "p/a/f.bin"), offset=1, length=3)

        def missing(a):
            with self.assertRaises(NotFoundError):
                a.read_range(FileRef("s", "p/a/missing.bin"))

        def symlink(a):
            with self.assertRaises(UnsafePathError):
                a.read_range(FileRef("s", "p/a/sym/escape"))

        def badopt(a):
            with self.assertRaises(ValueError):
                a.read_range(FileRef("s", "p/a/f.bin"), length=0)

        self._run_tracked([good, missing, symlink, badopt, good, good])

    def test_linux_proc_self_fd_stable(self) -> None:
        proc_fd = Path("/proc/self/fd")
        if not proc_fd.is_dir():
            self.skipTest("/proc/self/fd not available")
        (self.root_dir / "f.bin").write_bytes(b"payload")
        baseline = len(os.listdir(proc_fd))
        a = LocalAdapter("s", self.root_dir, max_read_bytes=100)
        try:
            for _ in range(50):
                a.read_range(FileRef("s", "p/f.bin"))
                a.read_range(FileRef("s", "p/f.bin"), offset=2, length=3)
            self.assertEqual(len(os.listdir(proc_fd)), baseline + 1)
        finally:
            a.close()
        self.assertEqual(len(os.listdir(proc_fd)), baseline)


class TestConcurrencyProofs(_PosixRuntimeTestCase):
    """Concurrent calls each open their own descriptor and stay independent."""

    def test_each_concurrent_call_opens_its_own_descriptor(self) -> None:
        payload = bytes(range(256)) * 4
        (self.root_dir / "c.bin").write_bytes(payload)
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            real_open = a._os_open
            target_opens = {"n": 0}
            lock = threading.Lock()

            def counting_open(path, flags, *, dir_fd=None):
                if path == "c.bin":
                    with lock:
                        target_opens["n"] += 1
                return real_open(path, flags, dir_fd=dir_fd)

            windows = [(0, 8), (64, 8), (128, 8), (192, 8), (256, 8)]
            results: dict = {}
            errors: list = []
            barrier = threading.Barrier(len(windows))

            def worker(off, ln):
                try:
                    barrier.wait(timeout=5)
                    results[off] = a.read_range(
                        FileRef("s", "p/c.bin"), offset=off, length=ln
                    ).data
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            with mock.patch.object(a, "_os_open", side_effect=counting_open):
                threads = [
                    threading.Thread(target=worker, args=(o, ln))
                    for o, ln in windows
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(target_opens["n"], len(windows))
            for off, ln in windows:
                self.assertEqual(results[off], payload[off : off + ln])


class TestGrowthHonestyProofs(_PosixRuntimeTestCase):
    """eof_observed reflects pread, not the initial fstat."""

    def test_growth_after_fstat_is_visible_and_eof_from_pread(self) -> None:
        (self.root_dir / "grow.bin").write_bytes(b"")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            real = a._os_pread
            seq = {"n": 0}

            def growing(fd, length, offset):
                seq["n"] += 1
                if seq["n"] == 1:
                    return b"xyz"
                return real(fd, length, offset)

            with mock.patch.object(a, "_os_pread", side_effect=growing):
                res = a.read_range(FileRef("s", "p/grow.bin"), offset=0, length=20)
            self.assertEqual(res.data, b"xyz")
            self.assertEqual(res.observed_size, 0)
            self.assertTrue(res.eof_observed)

    def test_short_nonempty_read_is_not_treated_as_eof(self) -> None:
        (self.root_dir / "f.bin").write_bytes(b"0123456789")
        with LocalAdapter("s", self.root_dir, max_read_bytes=100) as a:
            real = a._os_pread

            def two(fd, length, offset):
                return real(fd, min(2, length), offset)

            with mock.patch.object(a, "_os_pread", side_effect=two):
                res = a.read_range(FileRef("s", "p/f.bin"), offset=0, length=6)
            self.assertEqual(res.data, b"012345")
            self.assertFalse(res.eof_observed)  # short reads are not EOF


if __name__ == "__main__":
    unittest.main()
