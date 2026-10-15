"""MODULE-005 tests: safe local writes (atomic, no-overwrite save and mkdir)."""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
import threading
import unittest

from drive_tool.local import (
    AdapterClosedError,
    EnumerationLimitError,
    LocalAdapter,
    LocalStorageError,
    NotDirectoryError,
    PATH_PREFIX,
    TEMP_PREFIX,
    UnsafePathError,
    UnsupportedEntryError,
    UnsupportedPlatformError,
)
from drive_tool.local_write import (
    DEFAULT_MAX_WRITE_BYTES,
    LocalWriter,
    WriteLimitExceededError,
)
from drive_tool.storage import (
    AlreadyExistsError,
    FileRef,
    IsDirectoryError,
    NotFoundError,
    SourceMismatchError,
    StorageError,
)

_HAS_PROC_FD = os.path.isdir("/proc/self/fd")

def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))

def _no_temp_entries(path: str) -> bool:
    return not any(
        entry.startswith(TEMP_PREFIX) for entry in os.listdir(path)
    )

class BaseLocalWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="drive_tool_lw_")
        self.root = os.path.join(self._tmpdir, "root")
        os.mkdir(self.root)
        self.adapter = LocalAdapter(
            "src", self.root, max_read_bytes=DEFAULT_MAX_WRITE_BYTES
        )
        self.writer = LocalWriter(self.adapter)

    def tearDown(self) -> None:
        try:
            self.adapter.close()
        except Exception:
            pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _root_ref(self) -> FileRef:
        return FileRef(self.adapter.source_id, "root")

    def _ref(self, file_id: str) -> FileRef:
        return FileRef(self.adapter.source_id, file_id)

class TestStorageContract(BaseLocalWriteTest):
    def test_storage_exports_new_errors(self) -> None:
        from drive_tool import storage
        self.assertIn("AlreadyExistsError", storage.__all__)
        self.assertIn("ReadOnlyError", storage.__all__)
        self.assertTrue(
            issubclass(storage.AlreadyExistsError, storage.StorageError)
        )
        self.assertTrue(
            issubclass(storage.ReadOnlyError, storage.StorageError)
        )

class TestWriterInit(BaseLocalWriteTest):
    def test_writer_init_validation(self) -> None:
        with self.assertRaises(TypeError):
            LocalWriter("not an adapter")  # type: ignore[arg-type]
        for bad in (0, -1, True, False, "10", None, 1.5, 10_485_760.0):
            with self.assertRaises(ValueError):
                LocalWriter(self.adapter, max_write_bytes=bad)  # type: ignore[arg-type]

class TestWriteHappyPaths(BaseLocalWriteTest):
    def test_write_and_read_back_root_and_nested(self) -> None:
        writer = self.writer
        root_ref = self._root_ref()
        meta = writer.write_bytes(root_ref, "a.txt", b"hello")
        self.assertEqual(meta.kind, "file")
        self.assertEqual(meta.size_bytes, 5)
        self.assertEqual(meta.name, "a.txt")
        self.assertEqual(meta.ref.file_id, "p/a.txt")
        self.assertEqual(meta.parent_id, "root")
        self.assertEqual(self.adapter.read_bytes(self._ref("p/a.txt")), b"hello")

        os.mkdir(os.path.join(self.root, "sub"))
        sub_ref = self._ref("p/sub")
        meta2 = writer.write_bytes(sub_ref, "b.txt", b"world!")
        self.assertEqual(meta2.ref.file_id, "p/sub/b.txt")
        self.assertEqual(meta2.parent_id, "p/sub")
        self.assertEqual(meta2.size_bytes, 6)
        self.assertEqual(
            self.adapter.read_bytes(self._ref("p/sub/b.txt")), b"world!"
        )

    def test_write_empty_and_exact_max_write_bytes(self) -> None:
        writer = self.writer
        root_ref = self._root_ref()
        writer.write_bytes(root_ref, "empty.bin", b"")
        self.assertEqual(
            self.adapter.read_bytes(self._ref("p/empty.bin")), b""
        )
        payload = b"x" * DEFAULT_MAX_WRITE_BYTES
        writer.write_bytes(root_ref, "max.bin", payload)
        self.assertEqual(
            self.adapter.read_bytes(self._ref("p/max.bin")), payload
        )
        self.assertTrue(_no_temp_entries(self.root))

    def test_write_overwrite_true_creates_and_replaces(self) -> None:
        writer = self.writer
        root_ref = self._root_ref()
        writer.write_bytes(root_ref, "a.txt", b"first", overwrite=True)
        self.assertEqual(self.adapter.read_bytes(self._ref("p/a.txt")), b"first")
        writer.write_bytes(
            root_ref, "a.txt", b"second-longer", overwrite=True
        )
        self.assertEqual(
            self.adapter.read_bytes(self._ref("p/a.txt")), b"second-longer"
        )
        st = self.adapter.stat(self._ref("p/a.txt"))
        self.assertEqual(st.size_bytes, len(b"second-longer"))

class TestWriteLimit(BaseLocalWriteTest):
    def test_write_exceeds_max_write_bytes_before_disk(self) -> None:
        writer = LocalWriter(self.adapter, max_write_bytes=10)
        root_ref = self._root_ref()
        with self.assertRaises(WriteLimitExceededError):
            writer.write_bytes(root_ref, "big.bin", b"x" * 11)
        self.assertEqual(os.listdir(self.root), [])

class TestWriteOverwriteFalse(BaseLocalWriteTest):
    def test_write_overwrite_false_existing_file(self) -> None:
        path = os.path.join(self.root, "a.txt")
        with open(path, "wb") as f:
            f.write(b"old")
        with self.assertRaises(AlreadyExistsError):
            self.writer.write_bytes(self._root_ref(), "a.txt", b"new")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"old")
        self.assertTrue(_no_temp_entries(self.root))

    def test_write_overwrite_false_existing_symlink(self) -> None:
        outside = os.path.join(self._tmpdir, "outside")
        os.mkdir(outside)
        target = os.path.join(outside, "victim.txt")
        with open(target, "wb") as f:
            f.write(b"victim")
        os.symlink(target, os.path.join(self.root, "link"))
        with self.assertRaises(AlreadyExistsError):
            self.writer.write_bytes(self._root_ref(), "link", b"new")
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"victim")
        self.assertTrue(_no_temp_entries(self.root))

        # Dangling symlink.
        os.symlink(
            os.path.join(self.root, "nope"),
            os.path.join(self.root, "dangling"),
        )
        with self.assertRaises(AlreadyExistsError):
            self.writer.write_bytes(self._root_ref(), "dangling", b"new")
        self.assertTrue(_no_temp_entries(self.root))

class TestWriteOverwriteTrueRejects(BaseLocalWriteTest):
    def test_write_overwrite_true_rejects_directory_symlink_fifo(self) -> None:
        writer = self.writer
        root_ref = self._root_ref()

        os.mkdir(os.path.join(self.root, "adir"))
        with self.assertRaises(IsDirectoryError):
            writer.write_bytes(root_ref, "adir", b"x", overwrite=True)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "adir")))

        outside = os.path.join(self._tmpdir, "outside")
        os.mkdir(outside)
        os.symlink(
            os.path.join(outside, "target"), os.path.join(self.root, "link")
        )
        with self.assertRaises(UnsafePathError):
            writer.write_bytes(root_ref, "link", b"x", overwrite=True)

        fifo = os.path.join(self.root, "afifo")
        os.mkfifo(fifo, 0o644)
        with self.assertRaises(UnsupportedEntryError):
            writer.write_bytes(root_ref, "afifo", b"x", overwrite=True)

        self.assertTrue(_no_temp_entries(self.root))
        self.assertEqual(os.listdir(outside), [])

class TestNameValidation(BaseLocalWriteTest):
    def test_name_validation_all_invalid_classes(self) -> None:
        writer = self.writer
        root_ref = self._root_ref()
        invalid: list[object] = [
            "",
            ".",
            "..",
            "a/b",
            "a\\b",
            "a\x00b",
            TEMP_PREFIX + "1",
            "a" * 256,
            "\u00e9" * 128,  # 256 UTF-8 bytes
            123,
            b"a",
            None,
            "\ud800",  # lone surrogate, unencodable
        ]
        for bad in invalid:
            with self.assertRaises(UnsafePathError):
                writer.write_bytes(root_ref, bad, b"")  # type: ignore[arg-type]
        self.assertEqual(os.listdir(self.root), [])

        # Exactly 255 UTF-8 bytes is accepted.
        ok = "\u00e9" * 127 + "x"
        writer.write_bytes(root_ref, ok, b"")
        self.assertIn(ok, os.listdir(self.root))

class TestParentValidation(BaseLocalWriteTest):
    def test_parent_validation_and_containment(self) -> None:
        writer = self.writer

        with self.assertRaises(NotFoundError):
            writer.write_bytes(self._ref("p/missing"), "a", b"x")

        with open(os.path.join(self.root, "afile"), "wb") as f:
            f.write(b"x")
        with self.assertRaises(NotDirectoryError):
            writer.write_bytes(self._ref("p/afile"), "a", b"x")

        os.mkfifo(os.path.join(self.root, "afifo"), 0o644)
        with self.assertRaises(UnsupportedEntryError):
            writer.write_bytes(self._ref("p/afifo"), "a", b"x")

        with self.assertRaises(UnsafePathError):
            writer.write_bytes(
                self._ref("p/" + TEMP_PREFIX + "dir"), "a", b"x"
            )

        outside = os.path.join(self._tmpdir, "outside")
        os.mkdir(outside)
        os.symlink(outside, os.path.join(self.root, "final_link"))
        with self.assertRaises(UnsafePathError):
            writer.write_bytes(self._ref("p/final_link"), "a", b"x")

        os.mkdir(os.path.join(self.root, "sub"))
        os.symlink(outside, os.path.join(self.root, "sub", "mid_link"))
        with self.assertRaises(UnsafePathError):
            writer.write_bytes(self._ref("p/sub/mid_link"), "a", b"x")

        self.assertEqual(os.listdir(outside), [])

class TestPrecedence(BaseLocalWriteTest):
    def test_precedence_source_mismatch_then_closed_then_args(self) -> None:
        writer = self.writer

        wrong = FileRef("wrong-source", "root")
        with self.assertRaises(SourceMismatchError):
            writer.write_bytes(wrong, "", b"")

        tmpdir = tempfile.mkdtemp(prefix="drive_tool_lw_closed_")
        try:
            root = os.path.join(tmpdir, "root")
            os.mkdir(root)
            a = LocalAdapter("src2", root)
            w = LocalWriter(a)
            a.close()
            with self.assertRaises(AdapterClosedError):
                w.write_bytes(FileRef("src2", "root"), "", b"")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        root_ref = self._root_ref()
        with self.assertRaises(ValueError):
            writer.write_bytes(root_ref, "a", "not bytes")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            writer.write_bytes(root_ref, "a", bytearray(b"x"))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            writer.write_bytes(root_ref, "a", b"", overwrite="yes")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            writer.mkdir(root_ref, "a", exist_ok="yes")  # type: ignore[arg-type]

class TestSeams(BaseLocalWriteTest):
    def test_seam_write_handles_short_writes_and_eintr(self) -> None:
        class ShortWriter(LocalWriter):
            def __init__(self, *a, **kw) -> None:
                super().__init__(*a, **kw)
                self.calls = 0
                self.observed = bytearray()

            def _os_write(self, fd, data):  # type: ignore[override]
                self.calls += 1
                if self.calls == 1:
                    raise OSError(errno.EINTR, "interrupted")
                chunk = bytes(data[:3])
                self.observed.extend(chunk)
                return os.write(fd, chunk)

        w = ShortWriter(self.adapter)
        payload = b"abcdefghij"
        w.write_bytes(self._root_ref(), "short.bin", payload)
        self.assertEqual(
            self.adapter.read_bytes(self._ref("p/short.bin")), payload
        )
        self.assertEqual(bytes(w.observed), payload)
        self.assertTrue(_no_temp_entries(self.root))

    @unittest.skipUnless(_HAS_PROC_FD, "requires /proc/self/fd")
    def test_seam_write_zero_return_or_midway_oserror_cleans_up(self) -> None:
        class ZeroWriter(LocalWriter):
            def _os_write(self, fd, data):  # type: ignore[override]
                return 0

        fd_before = _fd_count()
        zw = ZeroWriter(self.adapter)
        with self.assertRaises(LocalStorageError):
            zw.write_bytes(self._root_ref(), "z.bin", b"abc")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(_fd_count(), fd_before)

        class MidwayWriter(LocalWriter):
            def __init__(self, *a, **kw) -> None:
                super().__init__(*a, **kw)
                self.n = 0

            def _os_write(self, fd, data):  # type: ignore[override]
                self.n += 1
                if self.n == 1:
                    return os.write(fd, bytes(data[:3]))
                raise OSError(errno.ENOSPC, "no space")

        fd_before = _fd_count()
        mw = MidwayWriter(self.adapter)
        with self.assertRaises(LocalStorageError):
            mw.write_bytes(self._root_ref(), "m.bin", b"abcdef")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(_fd_count(), fd_before)

    @unittest.skipUnless(_HAS_PROC_FD, "requires /proc/self/fd")
    def test_seam_file_fsync_failure_cleans_up(self) -> None:
        class FsyncFailWriter(LocalWriter):
            def _os_fsync(self, fd):  # type: ignore[override]
                st = os.fstat(fd)
                if stat.S_ISREG(st.st_mode):
                    raise OSError(errno.EIO, "simulated fsync failure")
                return os.fsync(fd)

        fd_before = _fd_count()
        w = FsyncFailWriter(self.adapter)
        with self.assertRaises(LocalStorageError):
            w.write_bytes(self._root_ref(), "a.bin", b"x")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(_fd_count(), fd_before)

    @unittest.skipUnless(_HAS_PROC_FD, "requires /proc/self/fd")
    def test_seam_temp_close_failure_before_publish_cleans_up(self) -> None:
        class CloseFailWriter(LocalWriter):
            def _os_close(self, fd):  # type: ignore[override]
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise OSError(errno.EIO, "simulated close failure")

        fd_before = _fd_count()
        w = CloseFailWriter(self.adapter)
        with self.assertRaises(LocalStorageError):
            w.write_bytes(self._root_ref(), "a.bin", b"x")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(_fd_count(), fd_before)

    @unittest.skipUnless(_HAS_PROC_FD, "requires /proc/self/fd")
    def test_seam_link_failures_eexist_and_eperm_clean_up(self) -> None:
        class LinkFailWriter(LocalWriter):
            def __init__(self, *a, exc: OSError | None = None, **kw) -> None:
                super().__init__(*a, **kw)
                self.exc = exc

            def _os_link(  # type: ignore[override]
                self,
                src,
                dst,
                *,
                src_dir_fd,
                dst_dir_fd,
                follow_symlinks=False,
            ):
                assert self.exc is not None
                raise self.exc

        fd_before = _fd_count()
        w1 = LinkFailWriter(self.adapter, exc=OSError(errno.EEXIST, "exists"))
        with self.assertRaises(AlreadyExistsError):
            w1.write_bytes(self._root_ref(), "a.bin", b"x")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(_fd_count(), fd_before)

        w2 = LinkFailWriter(self.adapter, exc=OSError(errno.EPERM, "no link"))
        with self.assertRaises(UnsupportedPlatformError):
            w2.write_bytes(self._root_ref(), "b.bin", b"x")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(_fd_count(), fd_before)

    @unittest.skipUnless(_HAS_PROC_FD, "requires /proc/self/fd")
    def test_seam_rename_failure_cleans_up(self) -> None:
        class RenameFailWriter(LocalWriter):
            def __init__(self, *a, exc: OSError | None = None, **kw) -> None:
                super().__init__(*a, **kw)
                self.exc = exc

            def _os_rename(  # type: ignore[override]
                self, src, dst, *, src_dir_fd, dst_dir_fd
            ):
                assert self.exc is not None
                raise self.exc

        fd_before = _fd_count()
        w1 = RenameFailWriter(self.adapter, exc=OSError(errno.EIO, "io"))
        with self.assertRaises(LocalStorageError):
            w1.write_bytes(
                self._root_ref(), "a.bin", b"x", overwrite=True
            )
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(_fd_count(), fd_before)

        w2 = RenameFailWriter(self.adapter, exc=OSError(errno.EISDIR, "dir"))
        with self.assertRaises(IsDirectoryError):
            w2.write_bytes(
                self._root_ref(), "b.bin", b"x", overwrite=True
            )
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(_fd_count(), fd_before)

    @unittest.skipUnless(_HAS_PROC_FD, "requires /proc/self/fd")
    def test_seam_dir_fsync_failure_does_not_double_unlink(self) -> None:
        class DirFsyncFailCounting(LocalWriter):
            def __init__(self, *a, **kw) -> None:
                super().__init__(*a, **kw)
                self.unlink_calls: list[str] = []

            def _os_fsync(self, fd):  # type: ignore[override]
                st = os.fstat(fd)
                if stat.S_ISDIR(st.st_mode):
                    raise OSError(errno.EIO, "simulated dir fsync failure")
                return os.fsync(fd)

            def _os_unlink(self, path, *, dir_fd):  # type: ignore[override]
                self.unlink_calls.append(path)
                return os.unlink(path, dir_fd=dir_fd)

        fd_before = _fd_count()

        w1 = DirFsyncFailCounting(self.adapter)
        with self.assertRaises(LocalStorageError):
            w1.write_bytes(self._root_ref(), "a.bin", b"x")
        self.assertEqual(len(w1.unlink_calls), 1)
        self.assertEqual(os.listdir(self.root), [])

        w2 = DirFsyncFailCounting(self.adapter)
        with self.assertRaises(LocalStorageError):
            w2.write_bytes(
                self._root_ref(), "b.bin", b"x", overwrite=True
            )
        self.assertEqual(len(w2.unlink_calls), 0)
        entries = os.listdir(self.root)
        self.assertIn("b.bin", entries)
        self.assertFalse(any(e.startswith(TEMP_PREFIX) for e in entries))

        self.assertEqual(_fd_count(), fd_before)

    def test_mutation_guard_temp_open_flags_and_mode(self) -> None:
        class OpenSpyWriter(LocalWriter):
            def __init__(self, *a, **kw) -> None:
                super().__init__(*a, **kw)
                self.opens: list[tuple[str, int, int]] = []

            def _os_open(  # type: ignore[override]
                self,
                path,
                flags,
                mode=0o777,
                *,
                dir_fd=None,
            ):
                self.opens.append((path, flags, mode))
                return os.open(path, flags, mode, dir_fd=dir_fd)

        w = OpenSpyWriter(self.adapter)
        w.write_bytes(self._root_ref(), "a.bin", b"payload")

        temp_opens = [o for o in w.opens if o[0].startswith(TEMP_PREFIX)]
        self.assertEqual(len(temp_opens), 1)
        _, flags, mode = temp_opens[0]
        self.assertTrue(flags & os.O_WRONLY)
        self.assertTrue(flags & os.O_CREAT)
        self.assertTrue(flags & os.O_EXCL)
        self.assertTrue(flags & os.O_NOFOLLOW)
        self.assertEqual(mode, 0o644)

    def test_mutation_guard_fsync_called_on_file_and_dir_fds(self) -> None:
        class FsyncSpyWriter(LocalWriter):
            def __init__(self, *a, **kw) -> None:
                super().__init__(*a, **kw)
                self.modes: list[int] = []

            def _os_fsync(self, fd):  # type: ignore[override]
                self.modes.append(os.fstat(fd).st_mode)
                return os.fsync(fd)

        w = FsyncSpyWriter(self.adapter)
        w.write_bytes(self._root_ref(), "a.bin", b"x")
        self.assertTrue(stat.S_ISREG(w.modes[0]))
        self.assertTrue(stat.S_ISDIR(w.modes[1]))
        self.assertEqual(len(w.modes), 2)

        w2 = FsyncSpyWriter(self.adapter)
        w2.mkdir(self._root_ref(), "newdir")
        self.assertEqual(len(w2.modes), 1)
        self.assertTrue(stat.S_ISDIR(w2.modes[0]))

class TestRace(BaseLocalWriteTest):
    def test_concurrent_two_writers_race_overwrite_false(self) -> None:
        writer = self.writer
        root_ref = self._root_ref()
        for i in range(20):
            name = f"race_{i}.bin"
            payloads = (b"A" * 64, b"B" * 128)
            barrier = threading.Barrier(2)
            results: list[tuple[str, bytes | None]] = [("pending", None), ("pending", None)]

            def worker(idx: int) -> None:
                barrier.wait()
                try:
                    writer.write_bytes(
                        root_ref, name, payloads[idx], overwrite=False
                    )
                    results[idx] = ("ok", payloads[idx])
                except AlreadyExistsError:
                    results[idx] = ("exists", None)

            t1 = threading.Thread(target=worker, args=(0,))
            t2 = threading.Thread(target=worker, args=(1,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            ok = [r for r in results if r[0] == "ok"]
            exists = [r for r in results if r[0] == "exists"]
            self.assertEqual(len(ok), 1)
            self.assertEqual(len(exists), 1)
            with open(os.path.join(self.root, name), "rb") as f:
                self.assertEqual(f.read(), ok[0][1])
        self.assertTrue(_no_temp_entries(self.root))

class TestListingTempFiles(BaseLocalWriteTest):
    def test_list_children_hides_temp_prefix_but_counts_toward_limit(self) -> None:
        with open(
            os.path.join(self.root, TEMP_PREFIX + "stale"), "wb"
        ) as f:
            f.write(b"x")
        with open(os.path.join(self.root, "visible.txt"), "wb") as f:
            f.write(b"y")

        root_ref = self._root_ref()
        children = self.adapter.list_children(root_ref)
        self.assertEqual([c.name for c in children], ["visible.txt"])

        children2 = self.adapter.list_children(root_ref, max_entries=2)
        self.assertEqual([c.name for c in children2], ["visible.txt"])

        with self.assertRaises(EnumerationLimitError):
            self.adapter.list_children(root_ref, max_entries=1)

class TestMkdir(BaseLocalWriteTest):
    def test_mkdir_create_and_exist_ok_matrix(self) -> None:
        writer = self.writer
        root_ref = self._root_ref()

        class FsyncSpy(LocalWriter):
            def __init__(self, *a, **kw) -> None:
                super().__init__(*a, **kw)
                self.fsync_count = 0

            def _os_fsync(self, fd):  # type: ignore[override]
                self.fsync_count += 1
                return os.fsync(fd)

        spy = FsyncSpy(self.adapter)

        # create
        meta = spy.mkdir(root_ref, "subdir")
        self.assertEqual(meta.kind, "directory")
        self.assertEqual(meta.name, "subdir")
        self.assertEqual(meta.ref.file_id, PATH_PREFIX + "subdir")
        self.assertEqual(meta.parent_id, "root")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "subdir")))
        self.assertEqual(spy.fsync_count, 1)

        # exist_ok=False on existing -> AlreadyExistsError
        with self.assertRaises(AlreadyExistsError):
            writer.mkdir(root_ref, "subdir")

        # exist_ok=True on existing directory -> FileMetadata, no fsync
        before = spy.fsync_count
        meta2 = spy.mkdir(root_ref, "subdir", exist_ok=True)
        self.assertEqual(meta2.kind, "directory")
        self.assertEqual(meta2.ref.file_id, PATH_PREFIX + "subdir")
        self.assertEqual(spy.fsync_count, before)

        # exist_ok=True on a regular file -> AlreadyExistsError
        with open(os.path.join(self.root, "afile"), "wb") as f:
            f.write(b"x")
        with self.assertRaises(AlreadyExistsError):
            writer.mkdir(root_ref, "afile", exist_ok=True)
        with self.assertRaises(AlreadyExistsError):
            writer.mkdir(root_ref, "afile")

        # exist_ok on a FIFO -> AlreadyExistsError
        os.mkfifo(os.path.join(self.root, "afifo"), 0o644)
        with self.assertRaises(AlreadyExistsError):
            writer.mkdir(root_ref, "afifo", exist_ok=True)

        # symlink to directory -> UnsafePathError (never accepted)
        os.symlink(
            os.path.join(self.root, "subdir"),
            os.path.join(self.root, "linkdir"),
        )
        with self.assertRaises(UnsafePathError):
            writer.mkdir(root_ref, "linkdir")
        with self.assertRaises(UnsafePathError):
            writer.mkdir(root_ref, "linkdir", exist_ok=True)

        # dangling symlink -> UnsafePathError
        os.symlink(
            os.path.join(self.root, "nowhere"),
            os.path.join(self.root, "dangling"),
        )
        with self.assertRaises(UnsafePathError):
            writer.mkdir(root_ref, "dangling")
        with self.assertRaises(UnsafePathError):
            writer.mkdir(root_ref, "dangling", exist_ok=True)

if __name__ == "__main__":
    unittest.main()
