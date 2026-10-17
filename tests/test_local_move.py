"""MODULE-006 tests: safe local moves (no-overwrite move and hidden trash)."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
import unittest
from unittest import mock

from drive_tool.local import (
    TEMP_PREFIX,
    LocalAdapter,
    LocalStorageError,
    UnsafePathError,
    UnsupportedEntryError,
)
from drive_tool.local_move import TRASH_DIR, LocalMover
from drive_tool.local_write import DEFAULT_MAX_WRITE_BYTES, LocalWriter
from drive_tool.storage import (
    AlreadyExistsError,
    FileRef,
    IsDirectoryError,
    NotDirectoryError,
    SourceMismatchError,
)

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

class BaseLocalMoveTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="drive_tool_lm_")
        self.root = os.path.join(self._tmpdir, "root")
        os.mkdir(self.root)
        self.adapter = LocalAdapter(
            "src", self.root, max_read_bytes=DEFAULT_MAX_WRITE_BYTES
        )
        self.writer = LocalWriter(self.adapter)
        self.mover = LocalMover(self.adapter)

    def tearDown(self) -> None:
        try:
            self.adapter.close()
        except Exception:
            pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _ref(self, file_id: str) -> FileRef:
        return FileRef(self.adapter.source_id, file_id)

    def _root_ref(self) -> FileRef:
        return self._ref("root")

    def _write(self, parent_id: str, name: str, data: bytes) -> FileRef:
        parent = self._root_ref() if parent_id == "root" else self._ref(
            "p/" + parent_id
        )
        meta = self.writer.write_bytes(parent, name, data)
        return meta.ref

class TestMoverInit(BaseLocalMoveTest):
    def test_mover_init_validation(self) -> None:
        with self.assertRaises(TypeError):
            LocalMover("not an adapter")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            LocalMover(None)  # type: ignore[arg-type]

class TestMoveRename(BaseLocalMoveTest):
    def test_move_with_rename_in_same_folder(self) -> None:
        src = self._write("root", "a.txt", b"hello")
        meta = self.mover.move(src, self._root_ref(), "b.txt")
        self.assertEqual(meta.ref.file_id, "p/b.txt")
        self.assertEqual(meta.name, "b.txt")
        self.assertEqual(meta.parent_id, "root")
        self.assertEqual(meta.kind, "file")
        self.assertEqual(meta.size_bytes, 5)
        self.assertFalse(os.path.exists(os.path.join(self.root, "a.txt")))
        self.assertEqual(
            self.adapter.read_bytes(self._ref("p/b.txt")), b"hello"
        )

    def test_move_without_new_name_keeps_name_into_other_folder(self) -> None:
        os.mkdir(os.path.join(self.root, "sub"))
        src = self._write("root", "a.txt", b"payload")
        sub_ref = self._ref("p/sub")
        meta = self.mover.move(src, sub_ref)
        self.assertEqual(meta.ref.file_id, "p/sub/a.txt")
        self.assertEqual(meta.name, "a.txt")
        self.assertEqual(meta.parent_id, "p/sub")
        self.assertEqual(
            self.adapter.read_bytes(self._ref("p/sub/a.txt")), b"payload"
        )
        self.assertFalse(os.path.exists(os.path.join(self.root, "a.txt")))

class TestMoveAcrossFolders(BaseLocalMoveTest):
    def test_move_into_another_folder_bytes_unchanged_source_gone(self) -> None:
        os.mkdir(os.path.join(self.root, "dest"))
        payload = b"\x00binary\xffdata" * 100
        src = self._write("root", "blob.bin", payload)
        dest_ref = self._ref("p/dest")
        meta = self.mover.move(src, dest_ref, "moved.bin")
        self.assertEqual(meta.ref.file_id, "p/dest/moved.bin")
        self.assertEqual(meta.size_bytes, len(payload))
        self.assertFalse(os.path.exists(os.path.join(self.root, "blob.bin")))
        self.assertEqual(
            self.adapter.read_bytes(self._ref("p/dest/moved.bin")), payload
        )

class TestMoveNoOverwrite(BaseLocalMoveTest):
    def test_target_exists_raises_and_both_files_untouched(self) -> None:
        src = self._write("root", "a.txt", b"source-bytes")
        dst_path = os.path.join(self.root, "b.txt")
        with open(dst_path, "wb") as f:
            f.write(b"target-bytes")
        with self.assertRaises(AlreadyExistsError):
            self.mover.move(src, self._root_ref(), "b.txt")
        with open(os.path.join(self.root, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"source-bytes")
        with open(dst_path, "rb") as f:
            self.assertEqual(f.read(), b"target-bytes")
        self.assertFalse(
            any(e.startswith(TEMP_PREFIX) for e in os.listdir(self.root))
        )

    def test_target_exists_in_other_folder(self) -> None:
        os.mkdir(os.path.join(self.root, "dest"))
        src = self._write("root", "a.txt", b"source")
        dst_path = os.path.join(self.root, "dest", "a.txt")
        with open(dst_path, "wb") as f:
            f.write(b"target")
        with self.assertRaises(AlreadyExistsError):
            self.mover.move(src, self._ref("p/dest"))
        with open(os.path.join(self.root, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"source")
        with open(dst_path, "rb") as f:
            self.assertEqual(f.read(), b"target")

class TestMoveRejectsNonFiles(BaseLocalMoveTest):
    def test_directory_source_raises_is_directory_error(self) -> None:
        os.mkdir(os.path.join(self.root, "adir"))
        with self.assertRaises(IsDirectoryError):
            self.mover.move(self._ref("p/adir"), self._root_ref(), "x")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "adir")))

    def test_symlink_source_raises_unsupported_entry_error(self) -> None:
        outside = os.path.join(self._tmpdir, "outside")
        os.mkdir(outside)
        target = os.path.join(outside, "victim.txt")
        with open(target, "wb") as f:
            f.write(b"victim")
        os.symlink(target, os.path.join(self.root, "link"))
        with self.assertRaises(UnsupportedEntryError):
            self.mover.move(self._ref("p/link"), self._root_ref(), "x")
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"victim")
        self.assertTrue(os.path.islink(os.path.join(self.root, "link")))

    def test_fifo_source_raises_unsupported_entry_error(self) -> None:
        os.mkfifo(os.path.join(self.root, "afifo"), 0o644)
        with self.assertRaises(UnsupportedEntryError):
            self.mover.move(self._ref("p/afifo"), self._root_ref(), "x")
        self.assertTrue(os.path.exists(os.path.join(self.root, "afifo")))

class TestMoveRejectsTempDest(BaseLocalMoveTest):
    def test_temp_prefix_in_dest_parent_is_rejected(self) -> None:
        src = self._write("root", "a.txt", b"x")
        bad_parent = self._ref("p/" + TEMP_PREFIX + "dir")
        with self.assertRaises(UnsafePathError):
            self.mover.move(src, bad_parent, "b.txt")
        self.assertTrue(os.path.exists(os.path.join(self.root, "a.txt")))

class TestMoveWrongSource(BaseLocalMoveTest):
    def test_wrong_source_id_raises_same_error_as_writer(self) -> None:
        src = self._write("root", "a.txt", b"x")
        wrong = FileRef("wrong-source", "root")
        with self.assertRaises(SourceMismatchError):
            self.mover.move(wrong, self._root_ref(), "b.txt")
        # The writer raises the same error class for the same mismatch.
        with self.assertRaises(SourceMismatchError):
            self.writer.write_bytes(wrong, "y.txt", b"y")
        self.assertTrue(os.path.exists(os.path.join(self.root, "a.txt")))

class TestTrash(BaseLocalMoveTest):
    def test_trash_file_gone_from_folder_present_in_trash_same_bytes(
        self,
    ) -> None:
        payload = b"trash-me-payload"
        src = self._write("root", "a.txt", payload)
        meta = self.mover.trash(src)
        self.assertEqual(meta.parent_id, "p/" + TRASH_DIR)
        self.assertEqual(meta.kind, "file")
        self.assertEqual(meta.size_bytes, len(payload))
        self.assertFalse(os.path.exists(os.path.join(self.root, "a.txt")))

        trash_path = os.path.join(self.root, TRASH_DIR)
        self.assertTrue(os.path.isdir(trash_path))
        entries = os.listdir(trash_path)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].endswith("-a.txt"))
        self.assertEqual(meta.ref.file_id, "p/" + TRASH_DIR + "/" + entries[0])
        with open(os.path.join(trash_path, entries[0]), "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_trashing_two_files_with_same_name_keeps_both(self) -> None:
        os.mkdir(os.path.join(self.root, "d1"))
        os.mkdir(os.path.join(self.root, "d2"))
        first = self._write("d1", "same.txt", b"first-bytes")
        second = self._write("d2", "same.txt", b"second-bytes")
        self.mover.trash(first)
        self.mover.trash(second)
        trash_path = os.path.join(self.root, TRASH_DIR)
        entries = os.listdir(trash_path)
        self.assertEqual(len(entries), 2)
        contents = set()
        for entry in entries:
            self.assertTrue(entry.endswith("-same.txt"))
            with open(os.path.join(trash_path, entry), "rb") as f:
                contents.add(f.read())
        self.assertEqual(contents, {b"first-bytes", b"second-bytes"})

    def test_trash_rejects_directory_and_symlink(self) -> None:
        os.mkdir(os.path.join(self.root, "adir"))
        with self.assertRaises(IsDirectoryError):
            self.mover.trash(self._ref("p/adir"))
        outside = os.path.join(self._tmpdir, "outside")
        os.mkdir(outside)
        target = os.path.join(outside, "victim.txt")
        with open(target, "wb") as f:
            f.write(b"victim")
        os.symlink(target, os.path.join(self.root, "link"))
        with self.assertRaises(UnsupportedEntryError):
            self.mover.trash(self._ref("p/link"))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "adir")))

class TestMoveUnlinkSourceFailure(BaseLocalMoveTest):
    """The link succeeds but the source unlink fails: no data is lost."""

    def test_unlink_failure_reports_both_names_exist(self) -> None:
        payload = b"survives-a-failed-unlink"
        src = self._write("root", "a.txt", payload)

        def failing_unlink(path: str, *, dir_fd: int) -> None:
            raise OSError(errno.EIO, "injected unlink failure")

        with mock.patch.object(self.mover, "_os_unlink", failing_unlink):
            with self.assertRaises(LocalStorageError) as ctx:
                self.mover.move(src, self._root_ref(), "b.txt")
        self.assertIn("both names now exist", str(ctx.exception))

        # Both names exist and hold the same bytes.
        self.assertTrue(os.path.exists(os.path.join(self.root, "a.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "b.txt")))
        with open(os.path.join(self.root, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), payload)
        with open(os.path.join(self.root, "b.txt"), "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_trash_unlink_failure_keeps_source_and_trash_copy(self) -> None:
        payload = b"trash-survives"
        src = self._write("root", "t.txt", payload)

        def failing_unlink(path: str, *, dir_fd: int) -> None:
            raise OSError(errno.EIO, "injected unlink failure")

        with mock.patch.object(self.mover, "_os_unlink", failing_unlink):
            with self.assertRaises(LocalStorageError):
                self.mover.trash(src)

        self.assertTrue(os.path.exists(os.path.join(self.root, "t.txt")))
        trash_path = os.path.join(self.root, TRASH_DIR)
        entries = os.listdir(trash_path)
        self.assertEqual(len(entries), 1)
        with open(os.path.join(trash_path, entries[0]), "rb") as f:
            self.assertEqual(f.read(), payload)

class TestMoveInodeVerifyMismatch(BaseLocalMoveTest):
    """After the link, the destination inode differs: link removed, source kept."""

    def _patch_dst_stat(self, src_st: os.stat_result) -> None:
        """Make the destination stat report a different (st_dev, st_ino)."""

        def forged_stat(path: str, *, dir_fd: int) -> os.stat_result:
            fields = list(src_st)
            fields[1] = src_st.st_ino + 1  # st_ino
            return os.stat_result(fields)

        self.adapter._os_stat_nofollow = forged_stat  # type: ignore[method-assign]

    def test_mismatched_inode_removes_link_and_keeps_source(self) -> None:
        payload = b"original-bytes"
        src = self._write("root", "a.txt", payload)
        src_real = os.stat(os.path.join(self.root, "a.txt"))
        self._patch_dst_stat(src_real)

        with self.assertRaises(LocalStorageError) as ctx:
            self.mover.move(src, self._root_ref(), "b.txt")
        self.assertIn("changed while moving", str(ctx.exception))

        # The just-created destination link was removed; source is untouched.
        self.assertTrue(os.path.exists(os.path.join(self.root, "a.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "b.txt")))
        with open(os.path.join(self.root, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_trash_mismatched_inode_removes_link_and_keeps_source(self) -> None:
        payload = b"trash-original"
        src = self._write("root", "t.txt", payload)
        src_real = os.stat(os.path.join(self.root, "t.txt"))
        self._patch_dst_stat(src_real)

        with self.assertRaises(LocalStorageError):
            self.mover.trash(src)

        self.assertTrue(os.path.exists(os.path.join(self.root, "t.txt")))
        trash_path = os.path.join(self.root, TRASH_DIR)
        self.assertEqual(os.listdir(trash_path), [])
        with open(os.path.join(self.root, "t.txt"), "rb") as f:
            self.assertEqual(f.read(), payload)

class TestMoveNameValidation(BaseLocalMoveTest):
    def _check_rejected(
        self, new_name: object, expected=UnsafePathError
    ) -> None:
        src = self._write("root", "a.txt", b"x")
        with self.assertRaises(expected):
            self.mover.move(src, self._root_ref(), new_name)  # type: ignore[arg-type]
        self.assertTrue(os.path.exists(os.path.join(self.root, "a.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "b.txt")))

    def test_empty_name_rejected(self) -> None:
        self._check_rejected("")

    def test_dot_name_rejected(self) -> None:
        self._check_rejected(".")

    def test_dotdot_name_rejected(self) -> None:
        self._check_rejected("..")

    def test_slash_name_rejected(self) -> None:
        self._check_rejected("a/b")

    def test_nul_name_rejected(self) -> None:
        self._check_rejected("a\x00b")

    def test_temp_prefix_name_rejected(self) -> None:
        self._check_rejected(TEMP_PREFIX + "abc")

    def test_over_255_byte_name_rejected(self) -> None:
        self._check_rejected("a" * 256)

    def test_non_str_name_rejected(self) -> None:
        self._check_rejected(123, expected=UnsafePathError)

class TestMoveDestParentIsFile(BaseLocalMoveTest):
    def test_dest_parent_is_file_raises_not_directory_error(self) -> None:
        src = self._write("root", "a.txt", b"x")
        # "afile" exists as a regular file; its p/ id is used as a parent.
        self._write("root", "afile", b"not-a-dir")
        with self.assertRaises(NotDirectoryError):
            self.mover.move(src, self._ref("p/afile"), "b.txt")
        self.assertTrue(os.path.exists(os.path.join(self.root, "a.txt")))

    def test_source_parent_is_file_raises_not_directory_error(self) -> None:
        self._write("root", "afile", b"not-a-dir")
        bad_source = self._ref("p/afile/child")
        with self.assertRaises(NotDirectoryError):
            self.mover.move(bad_source, self._root_ref(), "b.txt")

class TestTrashDirIsSymlink(BaseLocalMoveTest):
    def test_trash_dir_symlink_rejected_nothing_moved(self) -> None:
        outside = os.path.join(self._tmpdir, "outside-trash")
        os.mkdir(outside)
        os.symlink(outside, os.path.join(self.root, TRASH_DIR))
        src = self._write("root", "a.txt", b"keep")

        with self.assertRaises(UnsafePathError):
            self.mover.trash(src)

        self.assertTrue(os.path.exists(os.path.join(self.root, "a.txt")))
        self.assertTrue(os.path.islink(os.path.join(self.root, TRASH_DIR)))
        self.assertEqual(os.listdir(outside), [])

class TestTrashLongMultibyteName(BaseLocalMoveTest):
    def test_long_multibyte_name_truncated_to_255_bytes_valid_utf8(self) -> None:
        # Each emoji is 4 UTF-8 bytes. 63 emoji = 252 bytes, which is <= the
        # writer's 255-byte limit (so it can be created) but > the trash-name
        # budget of 230 bytes (255 - the 25-byte prefix), so the truncation
        # loop in _make_trash_name must actually run.
        char = "\U0001F600"
        long_name = char * 63
        self.assertEqual(len(long_name.encode("utf-8")), 252)
        self.assertLessEqual(len(long_name.encode("utf-8")), 255)
        self.assertGreater(len(long_name.encode("utf-8")), 230)

        src = self._write("root", long_name, b"payload")
        meta = self.mover.trash(src)

        entry = meta.ref.file_id.split("/")[-1]
        encoded = entry.encode("utf-8")
        self.assertLessEqual(len(encoded), 255)
        # Genuinely truncated: shorter than prefix + original.
        prefix_len = len(entry) - len(
            entry.split("-", 2)[-1]
        )
        self.assertLess(
            len(encoded),
            len(long_name.encode("utf-8")) + prefix_len,
        )
        # Valid UTF-8: no split code point at the cut.
        self.assertEqual(encoded.decode("utf-8"), entry)

        trash_path = os.path.join(self.root, TRASH_DIR)
        self.assertEqual(os.listdir(trash_path), [entry])
        with open(os.path.join(trash_path, entry), "rb") as f:
            self.assertEqual(f.read(), b"payload")
        self.assertFalse(os.path.exists(os.path.join(self.root, long_name)))

@unittest.skipIf(_IS_ROOT, "permission semantics differ for root")
class TestMovePermissionDenied(BaseLocalMoveTest):
    def test_move_into_unwritable_dest_leaves_source_intact(self) -> None:
        os.mkdir(os.path.join(self.root, "locked"))
        locked = os.path.join(self.root, "locked")
        src = self._write("root", "a.txt", b"keep-me")
        os.chmod(locked, 0o500)
        try:
            with self.assertRaises((LocalStorageError, OSError)):
                self.mover.move(src, self._ref("p/locked"), "a.txt")
            with open(os.path.join(self.root, "a.txt"), "rb") as f:
                self.assertEqual(f.read(), b"keep-me")
            self.assertEqual(os.listdir(locked), [])
        finally:
            os.chmod(locked, 0o700)

if __name__ == "__main__":
    unittest.main()
