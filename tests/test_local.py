"""Unit and containment tests for MODULE-003 (drive_tool.local)."""

from __future__ import annotations

import errno
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drive_tool.local import (
    DEFAULT_MAX_READ_BYTES,
    AdapterClosedError,
    LocalAdapter,
    LocalStorageError,
    ReadLimitExceededError,
    UnsafePathError,
    UnsupportedEntryError,
    UnsupportedPlatformError,
)
from drive_tool.storage import (
    ROOT_ID,
    FileMetadata,
    FileRef,
    IsDirectoryError,
    NotDirectoryError,
    NotFoundError,
    SourceMismatchError,
    StorageAdapter,
    StorageError,
)

def _is_supported_posix() -> bool:
    """Return True when the current platform supports LocalAdapter."""
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NONBLOCK")
        and os.open in getattr(os, "supports_dir_fd", set())
        and os.stat in getattr(os, "supports_dir_fd", set())
        and os.stat in getattr(os, "supports_follow_symlinks", set())
        and os.scandir in getattr(os, "supports_fd", set())
    )

class _PosixTempRootTestCase(unittest.TestCase):
    """Base test case providing a canonical symlink-free temporary directory."""

    def setUp(self) -> None:
        if not _is_supported_posix():
            self.skipTest("POSIX dir_fd/O_NOFOLLOW capabilities required")
        # Resolve any OS-level symlinks in the system temp root (e.g. /tmp -> /private/tmp)
        # so the base fixture path has zero symlinks in its ancestor chain from /.
        raw_base = tempfile.mkdtemp(prefix="drive_tool_mod003_")
        self.base_dir = Path(os.path.realpath(raw_base))
        self.root_dir = self.base_dir / "root"
        self.outside_dir = self.base_dir / "outside"
        self.root_dir.mkdir()
        self.outside_dir.mkdir()

    def tearDown(self) -> None:
        if hasattr(self, "base_dir") and self.base_dir.exists():
            shutil.rmtree(self.base_dir, ignore_errors=True)

class TestPlatformCapabilityGate(unittest.TestCase):
    """Tests for explicit platform capability gating (runs on all platforms)."""

    def test_error_hierarchy_extends_storage_error(self) -> None:
        self.assertTrue(issubclass(LocalStorageError, StorageError))
        self.assertTrue(issubclass(UnsafePathError, LocalStorageError))
        self.assertTrue(issubclass(UnsupportedEntryError, LocalStorageError))
        self.assertTrue(issubclass(ReadLimitExceededError, LocalStorageError))
        self.assertTrue(issubclass(AdapterClosedError, LocalStorageError))
        self.assertTrue(issubclass(UnsupportedPlatformError, LocalStorageError))

    def test_non_posix_os_name_raises_unsupported_platform_error(self) -> None:
        with mock.patch("drive_tool.local.os.name", "nt"):
            with self.assertRaises(UnsupportedPlatformError):
                LocalAdapter("local-src", "/tmp")

    def test_missing_o_nofollow_raises_unsupported_platform_error(self) -> None:
        with mock.patch("drive_tool.local.os.O_NOFOLLOW", 0, create=True):
            with self.assertRaises(UnsupportedPlatformError):
                LocalAdapter("local-src", "/tmp")

    def test_missing_dir_fd_support_raises_unsupported_platform_error(self) -> None:
        with mock.patch("drive_tool.local.os.supports_dir_fd", set()):
            with self.assertRaises(UnsupportedPlatformError):
                LocalAdapter("local-src", "/tmp")

    def test_missing_follow_symlinks_support_raises_unsupported_platform_error(
        self,
    ) -> None:
        with mock.patch("drive_tool.local.os.supports_follow_symlinks", set()):
            with self.assertRaises(UnsupportedPlatformError):
                LocalAdapter("local-src", "/tmp")

    def test_missing_scandir_fd_support_raises_unsupported_platform_error(
        self,
    ) -> None:
        with mock.patch("drive_tool.local.os.supports_fd", set()):
            with self.assertRaises(UnsupportedPlatformError):
                LocalAdapter("local-src", "/tmp")

class TestConfigurationAndRootValidation(_PosixTempRootTestCase):
    """Tests for constructor argument validation and no-follow root setup."""

    def test_invalid_source_id_raises_value_error(self) -> None:
        for bad_source in ("", None, 123, ["src"]):  # type: ignore[list-item]
            with self.subTest(bad_source=bad_source):
                with self.assertRaises(ValueError):
                    LocalAdapter(bad_source, self.root_dir)  # type: ignore[arg-type]

    def test_invalid_max_read_bytes_raises_value_error(self) -> None:
        for bad_limit in (0, -1, -1024, True, False, "1024", 1.5, None):
            with self.subTest(bad_limit=bad_limit):
                with self.assertRaises(ValueError):
                    LocalAdapter(
                        "local-src",
                        self.root_dir,
                        max_read_bytes=bad_limit,  # type: ignore[arg-type]
                    )

    def test_invalid_root_types_and_non_canonical_roots_rejected(self) -> None:
        for bad_root in (
            123,
            None,
            "",
            "relative/path",
            "./relative",
            f"{self.root_dir}/.",
            f"{self.root_dir}/..",
            f"{self.root_dir}/",
            f"{self.root_dir}//sub",
            f"{self.root_dir}/bad\x00name",
            f"{self.root_dir}/bad\\name",
        ):
            with self.subTest(bad_root=bad_root):
                with self.assertRaises(ValueError):
                    LocalAdapter("local-src", bad_root)  # type: ignore[arg-type]

    def test_nonexistent_and_file_root_rejected(self) -> None:
        missing_root = self.base_dir / "does_not_exist"
        with self.assertRaises(ValueError):
            LocalAdapter("local-src", missing_root)

        file_root = self.base_dir / "regular_file.txt"
        file_root.write_bytes(b"not a dir")
        with self.assertRaises(ValueError):
            LocalAdapter("local-src", file_root)

    def test_symlink_root_and_symlink_ancestor_rejected(self) -> None:
        real_target = self.outside_dir / "real_subdir"
        real_target.mkdir()

        symlink_root = self.base_dir / "symlink_root"
        os.symlink(real_target, symlink_root)

        with self.assertRaises(UnsafePathError):
            LocalAdapter("local-src", symlink_root)

        nested_inside_target = real_target / "nested_dir"
        nested_inside_target.mkdir()
        path_with_symlink_ancestor = symlink_root / "nested_dir"

        with self.assertRaises(UnsafePathError):
            LocalAdapter("local-src", path_with_symlink_ancestor)

class TestBasicOperationsAndMetadata(_PosixTempRootTestCase):
    """Tests for stat, list_children, read_bytes, parent IDs, and real 'root' file."""

    def test_protocol_conformance_and_defaults(self) -> None:
        with LocalAdapter("local-a", self.root_dir) as adapter:
            self.assertIsInstance(adapter, StorageAdapter)
            self.assertEqual(adapter.source_id, "local-a")
            self.assertEqual(adapter.max_read_bytes, DEFAULT_MAX_READ_BYTES)

    def test_synthetic_root_and_real_top_level_root_filename(self) -> None:
        real_root_file = self.root_dir / "root"
        real_root_file.write_bytes(b"real-root-file-payload")

        with LocalAdapter("local-a", self.root_dir) as adapter:
            synth_meta = adapter.stat(FileRef("local-a", ROOT_ID))
            self.assertEqual(
                synth_meta,
                FileMetadata(
                    ref=FileRef("local-a", "root"),
                    name="",
                    parent_id=None,
                    kind="directory",
                    size_bytes=None,
                ),
            )

            real_meta = adapter.stat(FileRef("local-a", "p/root"))
            self.assertEqual(
                real_meta,
                FileMetadata(
                    ref=FileRef("local-a", "p/root"),
                    name="root",
                    parent_id="root",
                    kind="file",
                    size_bytes=22,
                ),
            )
            self.assertEqual(
                adapter.read_bytes(FileRef("local-a", "p/root")),
                b"real-root-file-payload",
            )

    def test_nested_stat_list_and_read_exact_binary_and_empty_files(self) -> None:
        empty_file = self.root_dir / "empty.bin"
        empty_file.write_bytes(b"")

        notes_dir = self.root_dir / "notes"
        notes_dir.mkdir()
        nested_dir = notes_dir / "2026"
        nested_dir.mkdir()

        binary_payload = bytes(range(256)) + b"\x00\xff\xfe\r\n"
        report_file = nested_dir / "report.bin"
        report_file.write_bytes(binary_payload)

        with LocalAdapter("src-1", self.root_dir) as adapter:
            empty_meta = adapter.stat(FileRef("src-1", "p/empty.bin"))
            self.assertEqual(
                empty_meta,
                FileMetadata(
                    ref=FileRef("src-1", "p/empty.bin"),
                    name="empty.bin",
                    parent_id="root",
                    kind="file",
                    size_bytes=0,
                ),
            )
            self.assertEqual(
                adapter.read_bytes(FileRef("src-1", "p/empty.bin")), b""
            )

            notes_meta = adapter.stat(FileRef("src-1", "p/notes"))
            self.assertEqual(
                notes_meta,
                FileMetadata(
                    ref=FileRef("src-1", "p/notes"),
                    name="notes",
                    parent_id="root",
                    kind="directory",
                    size_bytes=None,
                ),
            )

            nested_dir_meta = adapter.stat(FileRef("src-1", "p/notes/2026"))
            self.assertEqual(
                nested_dir_meta,
                FileMetadata(
                    ref=FileRef("src-1", "p/notes/2026"),
                    name="2026",
                    parent_id="p/notes",
                    kind="directory",
                    size_bytes=None,
                ),
            )

            report_meta = adapter.stat(
                FileRef("src-1", "p/notes/2026/report.bin")
            )
            self.assertEqual(
                report_meta,
                FileMetadata(
                    ref=FileRef("src-1", "p/notes/2026/report.bin"),
                    name="report.bin",
                    parent_id="p/notes/2026",
                    kind="file",
                    size_bytes=len(binary_payload),
                ),
            )
            self.assertEqual(
                adapter.read_bytes(
                    FileRef("src-1", "p/notes/2026/report.bin")
                ),
                binary_payload,
            )

            nested_children = adapter.list_children(
                FileRef("src-1", "p/notes/2026")
            )
            self.assertEqual(nested_children, [report_meta])

    def test_sorted_listing_and_empty_directory(self) -> None:
        (self.root_dir / "zeta.txt").write_bytes(b"z")
        (self.root_dir / "alpha.txt").write_bytes(b"abc")
        (self.root_dir / "middle_dir").mkdir()
        (self.root_dir / "root").write_bytes(b"r")

        with LocalAdapter("src-1", self.root_dir) as adapter:
            # Empty subdirectory returns empty list
            self.assertEqual(
                adapter.list_children(FileRef("src-1", "p/middle_dir")), []
            )

            # Root listing is sorted by basename and repeatable across calls
            first_listing = adapter.list_children(FileRef("src-1", ROOT_ID))
            second_listing = adapter.list_children(FileRef("src-1", ROOT_ID))
            self.assertEqual(first_listing, second_listing)
            self.assertEqual(
                [m.name for m in first_listing],
                ["alpha.txt", "middle_dir", "root", "zeta.txt"],
            )
            self.assertEqual(
                [m.ref.file_id for m in first_listing],
                ["p/alpha.txt", "p/middle_dir", "p/root", "p/zeta.txt"],
            )

class TestPrecedenceAndMalformedIds(_PosixTempRootTestCase):
    """Tests for source mismatch precedence, missing entries, and malformed IDs."""

    def test_source_mismatch_precedence_across_all_methods_and_states(
        self,
    ) -> None:
        adapter = LocalAdapter("expected-source", self.root_dir)
        try:
            bad_refs = [
                FileRef("wrong-source", ROOT_ID),
                FileRef("wrong-source", "p/missing.txt"),
                FileRef("wrong-source", ""),
                FileRef("wrong-source", "../escape"),
                FileRef("wrong-source", "/etc/passwd"),
                FileRef("wrong-source", "p/a//b"),
                FileRef("wrong-source", "p/bad\x00id"),
                FileRef("wrong-source", "p/bad\\id"),
            ]
            for bad_ref in bad_refs:
                with self.subTest(state="open", ref=bad_ref):
                    with self.assertRaises(SourceMismatchError):
                        adapter.stat(bad_ref)
                    with self.assertRaises(SourceMismatchError):
                        adapter.list_children(bad_ref)
                    with self.assertRaises(SourceMismatchError):
                        adapter.read_bytes(bad_ref)
        finally:
            adapter.close()

        # Even after close(), source mismatch still takes precedence over AdapterClosedError.
        for bad_ref in bad_refs:
            with self.subTest(state="closed", ref=bad_ref):
                with self.assertRaises(SourceMismatchError):
                    adapter.stat(bad_ref)
                with self.assertRaises(SourceMismatchError):
                    adapter.list_children(bad_ref)
                with self.assertRaises(SourceMismatchError):
                    adapter.read_bytes(bad_ref)

    def test_non_fileref_raises_type_error(self) -> None:
        with LocalAdapter("src", self.root_dir) as adapter:
            for invalid in ("root", None, 42):
                with self.assertRaises(TypeError):
                    adapter.stat(invalid)  # type: ignore[arg-type]
                with self.assertRaises(TypeError):
                    adapter.list_children(invalid)  # type: ignore[arg-type]
                with self.assertRaises(TypeError):
                    adapter.read_bytes(invalid)  # type: ignore[arg-type]

    def test_missing_entries_and_file_directory_type_errors(self) -> None:
        (self.root_dir / "file.txt").write_bytes(b"hello")
        (self.root_dir / "subdir").mkdir()

        with LocalAdapter("src", self.root_dir) as adapter:
            for missing_id in ("p/missing.txt", "p/subdir/missing.txt", "p/no_dir/a.txt"):
                ref = FileRef("src", missing_id)
                with self.assertRaises(NotFoundError):
                    adapter.stat(ref)
                with self.assertRaises(NotFoundError):
                    adapter.list_children(ref)
                with self.assertRaises(NotFoundError):
                    adapter.read_bytes(ref)

            # read_bytes on synthetic root or real directory raises IsDirectoryError
            with self.assertRaises(IsDirectoryError):
                adapter.read_bytes(FileRef("src", ROOT_ID))
            with self.assertRaises(IsDirectoryError):
                adapter.read_bytes(FileRef("src", "p/subdir"))

            # list_children on regular file raises NotDirectoryError
            with self.assertRaises(NotDirectoryError):
                adapter.list_children(FileRef("src", "p/file.txt"))

            # Traversing through a regular file as an intermediate component raises NotDirectoryError
            with self.assertRaises(NotDirectoryError):
                adapter.stat(FileRef("src", "p/file.txt/child"))
            with self.assertRaises(NotDirectoryError):
                adapter.list_children(FileRef("src", "p/file.txt/child"))
            with self.assertRaises(NotDirectoryError):
                adapter.read_bytes(FileRef("src", "p/file.txt/child"))

    def test_all_malformed_id_classes_rejected_with_unsafe_path_error(
        self,
    ) -> None:
        malformed_ids: list[object] = [
            "",
            "p",
            "p/",
            "file.txt",
            "root/",
            "/root",
            "/etc/passwd",
            "/p/file.txt",
            "p//file.txt",
            "p/a//b",
            "p/a/",
            "p/.",
            "p/..",
            "p/./a",
            "p/../a",
            "p/a/./b",
            "p/a/../b",
            "p/a\\b",
            "p/a\x00b",
            None,
            123,
        ]
        with LocalAdapter("src", self.root_dir) as adapter:
            for bad_id in malformed_ids:
                ref = FileRef("src", bad_id)  # type: ignore[arg-type]
                with self.subTest(bad_id=bad_id):
                    with self.assertRaises(UnsafePathError):
                        adapter.stat(ref)
                    with self.assertRaises(UnsafePathError):
                        adapter.list_children(ref)
                    with self.assertRaises(UnsafePathError):
                        adapter.read_bytes(ref)

class TestContainmentSymlinksAndAnchoredRoot(_PosixTempRootTestCase):
    """Tests for symlink rejection, anchored root rename, and adversarial swaps."""

    def test_final_and_intermediate_symlinks_rejected_and_omitted_from_listing(
        self,
    ) -> None:
        outside_secret = self.outside_dir / "secret.txt"
        outside_secret.write_bytes(b"TOP_SECRET_OUTSIDE")
        outside_sub = self.outside_dir / "sub"
        outside_sub.mkdir()
        (outside_sub / "deep.txt").write_bytes(b"DEEP_OUTSIDE")

        inside_file = self.root_dir / "safe.txt"
        inside_file.write_bytes(b"safe")
        inside_dir = self.root_dir / "real_dir"
        inside_dir.mkdir()
        (inside_dir / "inside.txt").write_bytes(b"inside")

        # Create symlinks inside root pointing outside, inside, and broken
        os.symlink(outside_secret, self.root_dir / "sym_outside_file")
        os.symlink(self.outside_dir, self.root_dir / "sym_outside_dir")
        os.symlink(inside_file, self.root_dir / "sym_inside_file")
        os.symlink(
            self.root_dir / "nonexistent_target", self.root_dir / "sym_broken"
        )
        os.symlink(outside_sub, inside_dir / "sym_nested_dir")

        with LocalAdapter("src", self.root_dir) as adapter:
            # Direct stat/read on symlinks raises UnsafePathError
            for sym_id in (
                "p/sym_outside_file",
                "p/sym_outside_dir",
                "p/sym_inside_file",
                "p/sym_broken",
                "p/real_dir/sym_nested_dir",
            ):
                ref = FileRef("src", sym_id)
                with self.subTest(sym_id=sym_id):
                    with self.assertRaises(UnsafePathError):
                        adapter.stat(ref)
                    with self.assertRaises(UnsafePathError):
                        adapter.read_bytes(ref)
                    with self.assertRaises(UnsafePathError):
                        adapter.list_children(ref)

            # Intermediate traversal through symlinks raises UnsafePathError
            for traversal_id in (
                "p/sym_outside_dir/secret.txt",
                "p/sym_outside_dir/sub/deep.txt",
                "p/real_dir/sym_nested_dir/deep.txt",
            ):
                ref = FileRef("src", traversal_id)
                with self.subTest(traversal_id=traversal_id):
                    with self.assertRaises(UnsafePathError):
                        adapter.stat(ref)
                    with self.assertRaises(UnsafePathError):
                        adapter.read_bytes(ref)
                    with self.assertRaises(UnsafePathError):
                        adapter.list_children(ref)

            # Directory listing omits all symlinks and returns only real supported entries
            root_children = adapter.list_children(FileRef("src", ROOT_ID))
            self.assertEqual(
                [m.name for m in root_children], ["real_dir", "safe.txt"]
            )
            nested_children = adapter.list_children(
                FileRef("src", "p/real_dir")
            )
            self.assertEqual([m.name for m in nested_children], ["inside.txt"])

    def test_disappearing_entry_during_listing_is_skipped(self) -> None:
        (self.root_dir / "keep.txt").write_bytes(b"keep")
        vanish_path = self.root_dir / "vanish.txt"
        vanish_path.write_bytes(b"vanish")

        with LocalAdapter("src", self.root_dir) as adapter:
            orig_scandir = adapter._os_scandir

            class _DisappearingEntryWrapper:
                def __init__(self, inner_entry: os.DirEntry[str]) -> None:
                    self._inner = inner_entry
                    self.name = inner_entry.name

                def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
                    if self.name == "vanish.txt":
                        raise FileNotFoundError(
                            errno.ENOENT, "No such file or directory", self.name
                        )
                    return self._inner.stat(follow_symlinks=follow_symlinks)

            class _ScandirCtxWrapper:
                def __init__(self, inner_ctx) -> None:
                    self._inner_ctx = inner_ctx

                def __enter__(self):
                    entries = self._inner_ctx.__enter__()
                    return [_DisappearingEntryWrapper(e) for e in entries]

                def __exit__(self, exc_type, exc_val, exc_tb):
                    return self._inner_ctx.__exit__(exc_type, exc_val, exc_tb)

            with mock.patch.object(
                adapter,
                "_os_scandir",
                side_effect=lambda fd: _ScandirCtxWrapper(orig_scandir(fd)),
            ):
                listing = adapter.list_children(FileRef("src", ROOT_ID))
                self.assertEqual([m.name for m in listing], ["keep.txt"])

    def test_root_renamed_and_replaced_by_outside_symlink_stays_anchored(
        self,
    ) -> None:
        (self.root_dir / "anchored.txt").write_bytes(b"ORIGINAL_DATA")
        (self.outside_dir / "anchored.txt").write_bytes(b"OUTSIDE_FORGED_DATA")
        (self.outside_dir / "outside_only.txt").write_bytes(b"OUTSIDE_ONLY")

        with LocalAdapter("src", self.root_dir) as adapter:
            moved_root = self.base_dir / "root_moved"
            os.rename(self.root_dir, moved_root)
            os.symlink(self.outside_dir, self.root_dir)

            # Old adapter remains anchored to the originally opened directory descriptor
            self.assertEqual(
                adapter.read_bytes(FileRef("src", "p/anchored.txt")),
                b"ORIGINAL_DATA",
            )
            listing = adapter.list_children(FileRef("src", ROOT_ID))
            self.assertEqual([m.name for m in listing], ["anchored.txt"])
            with self.assertRaises(NotFoundError):
                adapter.stat(FileRef("src", "p/outside_only.txt"))

    def test_adversarial_symlink_swap_between_stat_and_read_and_mid_traversal(
        self,
    ) -> None:
        """Deterministic mechanism proof that symlink swaps cannot escape root.

        Part 1: Target file is replaced by a symlink to an outside secret file
        after `stat` succeeds and immediately before `read_bytes` opens it.
        `O_NOFOLLOW` on `openat` atomically rejects the swapped symlink.

        Part 2: Intermediate directory `sub` is opened via anchored `openat`,
        then `sub` is renamed on disk and replaced at its old path by a symlink
        to `outside_dir` right before `target.txt` is opened inside `parent_fd`.
        Because `openat` uses `dir_fd=parent_fd`, it stays inside the original
        directory and ignores the newly placed symlink at the old path.
        """
        sub_dir = self.root_dir / "sub"
        sub_dir.mkdir()
        target_file = sub_dir / "target.txt"
        target_file.write_bytes(b"SAFE_PAYLOAD")

        outside_secret = self.outside_dir / "target.txt"
        outside_secret.write_bytes(b"OUTSIDE_SECRET")

        with LocalAdapter("src", self.root_dir) as adapter:
            # Part 1: Swap target_file with a symlink to outside_secret right before open
            meta = adapter.stat(FileRef("src", "p/sub/target.txt"))
            self.assertEqual(meta.size_bytes, len(b"SAFE_PAYLOAD"))

            orig_open = adapter._os_open

            def swap_target_before_file_open(
                path: str, flags: int, *, dir_fd: int | None = None
            ) -> int:
                if path == "target.txt":
                    tmp_link = sub_dir / "target.txt.lnk"
                    if tmp_link.exists() or tmp_link.is_symlink():
                        tmp_link.unlink()
                    os.symlink(outside_secret, tmp_link)
                    os.replace(tmp_link, target_file)
                return orig_open(path, flags, dir_fd=dir_fd)

            with mock.patch.object(
                adapter, "_os_open", side_effect=swap_target_before_file_open
            ):
                with self.assertRaises(UnsafePathError):
                    adapter.read_bytes(FileRef("src", "p/sub/target.txt"))

            # Restore safe target_file for Part 2
            target_file.unlink()
            target_file.write_bytes(b"SAFE_PAYLOAD_2")

            # Part 2: Mid-traversal rename of `sub` + replacement with symlink to outside_dir
            moved_sub = self.root_dir / "sub_moved"

            def swap_parent_dir_after_parent_open(
                path: str, flags: int, *, dir_fd: int | None = None
            ) -> int:
                if path == "target.txt":
                    os.rename(sub_dir, moved_sub)
                    os.symlink(self.outside_dir, sub_dir)
                return orig_open(path, flags, dir_fd=dir_fd)

            with mock.patch.object(
                adapter,
                "_os_open",
                side_effect=swap_parent_dir_after_parent_open,
            ):
                data = adapter.read_bytes(FileRef("src", "p/sub/target.txt"))
                self.assertEqual(data, b"SAFE_PAYLOAD_2")

class TestBoundedReads(_PosixTempRootTestCase):
    """Tests for max_read_bytes threshold enforcement and concurrent file growth."""

    def test_read_limit_below_exact_and_above_threshold(self) -> None:
        (self.root_dir / "below.bin").write_bytes(b"a" * 15)
        (self.root_dir / "exact.bin").write_bytes(b"b" * 16)
        (self.root_dir / "above.bin").write_bytes(b"c" * 17)

        with LocalAdapter("src", self.root_dir, max_read_bytes=16) as adapter:
            self.assertEqual(
                adapter.read_bytes(FileRef("src", "p/below.bin")), b"a" * 15
            )
            self.assertEqual(
                adapter.read_bytes(FileRef("src", "p/exact.bin")), b"b" * 16
            )

            with mock.patch.object(
                adapter, "_os_read", wraps=adapter._os_read
            ) as spy_read:
                with self.assertRaises(ReadLimitExceededError):
                    adapter.read_bytes(FileRef("src", "p/above.bin"))
                # Rejected immediately by fstat check before any os.read call
                spy_read.assert_not_called()

    def test_simulated_file_growth_after_fstat_reads_at_most_limit_plus_one(
        self,
    ) -> None:
        """Proves a file growing after fstat cannot bypass max_read_bytes."""
        growing_file = self.root_dir / "grow.bin"
        growing_file.write_bytes(b"0123456789")  # 10 bytes initially

        with LocalAdapter("src", self.root_dir, max_read_bytes=16) as adapter:
            orig_fstat = adapter._os_fstat
            orig_read = adapter._os_read
            bytes_read_from_os = 0

            def fstat_then_append(fd: int) -> os.stat_result:
                st = orig_fstat(fd)
                # Append 100 extra bytes to the underlying file after fstat succeeds
                with open(growing_file, "ab") as writer:
                    writer.write(b"X" * 100)
                return st

            def counting_read(fd: int, length: int) -> bytes:
                nonlocal bytes_read_from_os
                chunk = orig_read(fd, length)
                bytes_read_from_os += len(chunk)
                return chunk

            with (
                mock.patch.object(
                    adapter, "_os_fstat", side_effect=fstat_then_append
                ),
                mock.patch.object(
                    adapter, "_os_read", side_effect=counting_read
                ),
            ):
                with self.assertRaises(ReadLimitExceededError):
                    adapter.read_bytes(FileRef("src", "p/grow.bin"))

            # At most max_read_bytes + 1 (17) bytes were read from the descriptor
            self.assertEqual(bytes_read_from_os, 17)

    def test_multi_chunk_streaming_growth_seam(self) -> None:
        """Proves chunked reading caps total bytes requested at max_read_bytes + 1."""
        small_file = self.root_dir / "stream.bin"
        small_file.write_bytes(b"init")

        with LocalAdapter("src", self.root_dir, max_read_bytes=16) as adapter:
            requested_lengths: list[int] = []
            total_returned = 0

            def fake_streaming_read(fd: int, length: int) -> bytes:
                nonlocal total_returned
                requested_lengths.append(length)
                # Simulate small 6-byte reads from an endlessly growing file
                chunk = b"z" * min(6, length)
                total_returned += len(chunk)
                return chunk

            with mock.patch.object(
                adapter, "_os_read", side_effect=fake_streaming_read
            ):
                with self.assertRaises(ReadLimitExceededError):
                    adapter.read_bytes(FileRef("src", "p/stream.bin"))

            # 6 + 6 + 5 = 17 bytes total (max_read_bytes + 1)
            self.assertEqual(total_returned, 17)
            self.assertEqual(requested_lengths, [17, 11, 5])

class TestSpecialEntriesAndNonblockingFifo(_PosixTempRootTestCase):
    """Tests that FIFOs and sockets are rejected without hanging and omitted from listing."""

    def test_fifo_rejected_without_blocking_in_bounded_subprocess(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("os.mkfifo is not available on this platform")

        fifo_path = self.root_dir / "test_pipe.fifo"
        try:
            os.mkfifo(fifo_path)
        except OSError as exc:
            self.skipTest(f"filesystem does not support mkfifo: {exc}")

        (self.root_dir / "normal.txt").write_bytes(b"ok")

        # Run in a child process with a strict timeout to guarantee no hang occurs
        repo_root = str(Path(__file__).resolve().parents[1])
        script = (
            "import sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
            "from drive_tool.local import LocalAdapter, UnsupportedEntryError\n"
            "from drive_tool.storage import FileRef, ROOT_ID\n"
            f"with LocalAdapter('src', {str(self.root_dir)!r}) as adapter:\n"
            "    children = adapter.list_children(FileRef('src', ROOT_ID))\n"
            "    assert [c.name for c in children] == ['normal.txt'], children\n"
            "    for fn in (adapter.stat, adapter.read_bytes, adapter.list_children):\n"
            "        try:\n"
            "            fn(FileRef('src', 'p/test_pipe.fifo'))\n"
            "        except UnsupportedEntryError:\n"
            "            pass\n"
            "        else:\n"
            "            raise AssertionError('Expected UnsupportedEntryError')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"FIFO subprocess failed: stdout={proc.stdout!r}, stderr={proc.stderr!r}",
        )

    def test_unix_socket_rejected_and_omitted_from_listing(self) -> None:
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("AF_UNIX sockets not available on this platform")

        # Use a short path inside root_dir for AF_UNIX sun_path length limits
        sock_path = self.root_dir / "s.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            try:
                srv.bind(str(sock_path))
            except OSError as exc:
                self.skipTest(f"cannot bind AF_UNIX socket in temp root: {exc}")

            (self.root_dir / "regular.txt").write_bytes(b"data")
            with LocalAdapter("src", self.root_dir) as adapter:
                with self.assertRaises(UnsupportedEntryError):
                    adapter.stat(FileRef("src", "p/s.sock"))
                with self.assertRaises(UnsupportedEntryError):
                    adapter.read_bytes(FileRef("src", "p/s.sock"))
                with self.assertRaises(UnsupportedEntryError):
                    adapter.list_children(FileRef("src", "p/s.sock"))

                listing = adapter.list_children(FileRef("src", ROOT_ID))
                self.assertEqual([m.name for m in listing], ["regular.txt"])
        finally:
            srv.close()

class TestDescriptorLifetimeAndClose(_PosixTempRootTestCase):
    """Tests for close(), context manager cleanup, and descriptor leak prevention."""

    def test_close_idempotency_and_operations_after_close(self) -> None:
        (self.root_dir / "file.txt").write_bytes(b"data")
        adapter = LocalAdapter("src", self.root_dir)
        adapter.close()
        adapter.close()  # Idempotent second call

        for method in (adapter.stat, adapter.list_children, adapter.read_bytes):
            with self.assertRaises(AdapterClosedError):
                method(FileRef("src", ROOT_ID))

        with self.assertRaises(AdapterClosedError):
            with adapter:
                pass

    def test_deterministic_descriptor_accounting_across_success_and_failures(
        self,
    ) -> None:
        sub = self.root_dir / "a" / "b"
        sub.mkdir(parents=True)
        (sub / "file.txt").write_bytes(b"hello world")
        (sub / "big.txt").write_bytes(b"x" * 100)
        os.symlink(self.outside_dir, sub / "sym")

        active_fds: set[int] = set()
        orig_open = os.open
        orig_close = os.close
        orig_scandir = os.scandir

        class _TrackedScandirWrapper:
            def __init__(self, fd: int) -> None:
                self._fd = fd
                self._inner = orig_scandir(fd)

            def __enter__(self):
                return self._inner.__enter__()

            def __exit__(self, exc_type, exc_val, exc_tb):
                try:
                    return self._inner.__exit__(exc_type, exc_val, exc_tb)
                finally:
                    active_fds.discard(self._fd)

        def tracked_open(
            path: str, flags: int, *, dir_fd: int | None = None
        ) -> int:
            fd = orig_open(path, flags, dir_fd=dir_fd)
            active_fds.add(fd)
            return fd

        def tracked_close(fd: int) -> None:
            active_fds.discard(fd)
            orig_close(fd)

        with (
            mock.patch.object(LocalAdapter, "_os_open", side_effect=tracked_open),
            mock.patch.object(
                LocalAdapter, "_os_close", side_effect=tracked_close
            ),
            mock.patch.object(
                LocalAdapter,
                "_os_scandir",
                side_effect=_TrackedScandirWrapper,
            ),
        ):
            # Failed init with symlink root leaves zero leaked descriptors
            sym_root = self.base_dir / "bad_sym_root"
            os.symlink(self.root_dir, sym_root)
            with self.assertRaises(UnsafePathError):
                LocalAdapter("src", sym_root)
            self.assertEqual(len(active_fds), 0)

            with LocalAdapter(
                "src", self.root_dir, max_read_bytes=20
            ) as adapter:
                # Only the single anchored root descriptor is retained
                self.assertEqual(len(active_fds), 1)

                for _ in range(10):
                    adapter.stat(FileRef("src", ROOT_ID))
                    adapter.stat(FileRef("src", "p/a/b/file.txt"))
                    adapter.list_children(FileRef("src", ROOT_ID))
                    adapter.list_children(FileRef("src", "p/a/b"))
                    adapter.read_bytes(FileRef("src", "p/a/b/file.txt"))

                    with self.assertRaises(NotFoundError):
                        adapter.read_bytes(FileRef("src", "p/a/b/missing.txt"))
                    with self.assertRaises(UnsafePathError):
                        adapter.stat(FileRef("src", "p/a/b/sym/escape"))
                    with self.assertRaises(ReadLimitExceededError):
                        adapter.read_bytes(FileRef("src", "p/a/b/big.txt"))

                    self.assertEqual(len(active_fds), 1)

            # After exiting the context manager, all descriptors are closed
            self.assertEqual(len(active_fds), 0)

    def test_linux_proc_self_fd_bounded_count(self) -> None:
        proc_fd = Path("/proc/self/fd")
        if not proc_fd.is_dir():
            self.skipTest("/proc/self/fd not available on this platform")

        sub = self.root_dir / "nested"
        sub.mkdir()
        (sub / "data.txt").write_bytes(b"payload")

        baseline_fds = len(os.listdir(proc_fd))
        adapter = LocalAdapter("src", self.root_dir)
        try:
            self.assertEqual(len(os.listdir(proc_fd)), baseline_fds + 1)
            for _ in range(25):
                adapter.stat(FileRef("src", "p/nested/data.txt"))
                adapter.list_children(FileRef("src", "p/nested"))
                adapter.read_bytes(FileRef("src", "p/nested/data.txt"))
                with self.assertRaises(NotFoundError):
                    adapter.stat(FileRef("src", "p/nested/missing"))
            self.assertEqual(len(os.listdir(proc_fd)), baseline_fds + 1)
        finally:
            adapter.close()

        self.assertEqual(len(os.listdir(proc_fd)), baseline_fds)

if __name__ == "__main__":
    unittest.main()
