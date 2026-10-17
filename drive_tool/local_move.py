"""Safe local moves: no-overwrite rename and a hidden trash (MODULE-006).

This module implements :class:`LocalMover`, a move-side wrapper around an open
:class:`drive_tool.local.LocalAdapter`. It complements
:class:`drive_tool.local_write.LocalWriter`: files can be published under a new
name or moved into a hidden trash directory, but nothing is ever permanently
deleted, overwritten, or moved as a directory.

Containment is identical to the writer: both parents are reached with the
adapter's anchored, no-follow directory walk, and publication is an atomic
no-replace ``link`` followed by an ``unlink`` of the source name. A crash
between the two syscalls leaves two names for one inode (no data is lost);
nothing here removes data otherwise.

The source file is opened ``O_RDONLY | O_NOFOLLOW`` and ``fstat``ed so its
type and size are taken from a descriptor, not from a path: a name swapped for
a symlink or directory between the check and the link cannot redirect the
move. After linking, the destination inode is compared against the opened
source descriptor before the source name is unlinked.

Threat boundaries (see MODULE-003): hard links, bind mounts, submounts, and
adversarial provisioning of the root directory itself remain operator
responsibilities. Python 3.11+, standard library only, POSIX only.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
from datetime import datetime, timezone

from drive_tool.local import (
    TEMP_PREFIX,
    LocalAdapter,
    LocalStorageError,
    NotDirectoryError,
    UnsafePathError,
    UnsupportedEntryError,
    _dir_open_flags,
    _file_open_flags,
    _parse_file_id,
)
from drive_tool.local_write import _check_writer_platform_capabilities
from drive_tool.storage import (
    AlreadyExistsError,
    FileMetadata,
    FileRef,
    IsDirectoryError,
    NotFoundError,
)

__all__ = [
    "TRASH_DIR",
    "LocalMover",
]

#: Hidden trash directory created directly under the adapter root.
TRASH_DIR = TEMP_PREFIX + "trash"

_MAX_NAME_BYTES = 255
_TRASH_MKDIR_MODE = 0o700

class LocalMover:
    """Move-side wrapper around an open :class:`LocalAdapter`.

    Every method checks source mismatch first, then closed adapter, then its
    arguments. Only regular files are moved: directories raise
    :class:`IsDirectoryError`, symlinks and special entries raise
    :class:`UnsupportedEntryError`. Nothing is ever overwritten and nothing is
    ever permanently deleted.
    """

    def __init__(self, adapter: LocalAdapter) -> None:
        if not isinstance(adapter, LocalAdapter):
            raise TypeError(
                f"adapter must be a LocalAdapter, got {type(adapter).__name__}"
            )
        _check_writer_platform_capabilities()
        self._adapter = adapter

    # -- OS seams (test injection) -------------------------------------------

    def _os_open(
        self,
        path: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        return os.open(path, flags, mode, dir_fd=dir_fd)

    def _os_link(
        self,
        src: str,
        dst: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool = False,
    ) -> None:
        os.link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    def _os_unlink(self, path: str, *, dir_fd: int) -> None:
        os.unlink(path, dir_fd=dir_fd)

    def _os_mkdir(
        self, path: str, mode: int = _TRASH_MKDIR_MODE, *, dir_fd: int
    ) -> None:
        os.mkdir(path, mode, dir_fd=dir_fd)

    # -- small helpers --------------------------------------------------------

    def _reject_temp_in_parent(self, parts: tuple[str, ...]) -> None:
        for part in parts:
            if part.startswith(TEMP_PREFIX):
                raise UnsafePathError(
                    f"parent path component {part!r} must not start with "
                    f"{TEMP_PREFIX!r}"
                )

    def _validate_name(self, name: object) -> str:
        if not isinstance(name, str):
            raise UnsafePathError(
                f"name must be a str, got {type(name).__name__}"
            )
        if name == "":
            raise UnsafePathError("name must be non-empty")
        if name in (".", ".."):
            raise UnsafePathError(f"name {name!r} is not a valid component")
        if "/" in name or "\\" in name or "\x00" in name:
            raise UnsafePathError(
                f"name {name!r} must not contain '/', backslash, or NUL"
            )
        if name.startswith(TEMP_PREFIX):
            raise UnsafePathError(
                f"name {name!r} must not start with reserved prefix "
                f"{TEMP_PREFIX!r}"
            )
        try:
            encoded = name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise UnsafePathError(
                f"name must be valid UTF-8: {exc}"
            ) from exc
        if len(encoded) > _MAX_NAME_BYTES:
            raise UnsafePathError(
                f"name is {len(encoded)} UTF-8 bytes, exceeds "
                f"{_MAX_NAME_BYTES}"
            )
        return name

    def _open_parent_dir(
        self,
        root_fd: int,
        parts: tuple[str, ...],
        file_id: str,
        owned: list[tuple[int, str]],
    ) -> int:
        """Open and return a directory fd for ``file_id``'s parent.

        The fd is appended to ``owned`` the moment it is acquired so the
        caller's single ``finally`` releases it exactly once.
        """
        if not parts:
            try:
                parent_fd = self._os_open(
                    ".", _dir_open_flags(), dir_fd=root_fd
                )
            except OSError as exc:
                raise LocalStorageError(
                    f"failed to open anchored root for {file_id!r}: {exc}"
                ) from exc
            owned.append((parent_fd, f"parent directory of {file_id!r}"))
            try:
                st = self._adapter._os_fstat(parent_fd)
            except OSError as exc:
                raise LocalStorageError(
                    f"failed to fstat parent of {file_id!r}: {exc}"
                ) from exc
            if not stat.S_ISDIR(st.st_mode):
                raise LocalStorageError(
                    f"anchored root for {file_id!r} is not a directory"
                )
            return parent_fd

        traversed_fd = self._adapter._traverse_parent_dir(
            root_fd, parts[:-1], file_id
        )
        if traversed_fd != root_fd:
            owned.append(
                (traversed_fd, f"intermediate parent of {file_id!r}")
            )

        target_name = parts[-1]
        try:
            parent_fd = self._os_open(
                target_name, _dir_open_flags(), dir_fd=traversed_fd
            )
        except OSError as exc:
            raise self._adapter._classify_component_open_error(
                target_name,
                traversed_fd,
                exc,
                file_id,
                is_intermediate=False,
                for_directory_op=True,
            ) from exc
        owned.append((parent_fd, f"parent directory {file_id!r}"))

        try:
            st = self._adapter._os_fstat(parent_fd)
        except OSError as exc:
            raise LocalStorageError(
                f"failed to fstat parent directory {file_id!r}: {exc}"
            ) from exc
        if not stat.S_ISDIR(st.st_mode):
            if stat.S_ISLNK(st.st_mode):
                raise UnsafePathError(
                    f"parent directory {file_id!r} is a symbolic link"
                )
            if stat.S_ISREG(st.st_mode):
                raise NotDirectoryError(
                    f"parent {file_id!r} is not a directory"
                )
            raise UnsupportedEntryError(
                f"parent {file_id!r} is not a supported directory"
            )
        return parent_fd

    def _open_regular_file(
        self,
        parent_fd: int,
        name: str,
        file_id: str,
        owned: list[tuple[int, str]],
    ) -> tuple[int, os.stat_result]:
        """Open ``name`` no-follow and require a regular file, returning fd+stat.

        The returned descriptor is appended to ``owned`` immediately after
        ``open`` succeeds, so a failing ``fstat`` or a non-regular-file mode
        check can never leak it (the caller's ``finally`` releases it). The
        size and type come from the *opened descriptor* (not from a path
        ``stat``), so they describe exactly the inode that the subsequent
        ``os.link`` will publish.
        """
        try:
            file_fd = self._os_open(name, _file_open_flags(), dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise NotFoundError(
                    f"entry {file_id!r} not found"
                ) from exc
            if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)):
                raise UnsupportedEntryError(
                    f"entry {file_id!r} is a symbolic link, not a regular file"
                ) from exc
            if exc.errno == errno.EISDIR:
                raise IsDirectoryError(
                    f"{file_id!r} is a directory, not a file"
                ) from exc
            raise LocalStorageError(
                f"failed to open source {file_id!r}: {exc}"
            ) from exc

        owned.append((file_fd, f"source file {file_id!r}"))

        try:
            st = self._adapter._os_fstat(file_fd)
        except OSError as exc:
            raise LocalStorageError(
                f"failed to fstat opened source {file_id!r}: {exc}"
            ) from exc

        if stat.S_ISLNK(st.st_mode):
            raise UnsupportedEntryError(
                f"entry {file_id!r} is a symbolic link, not a regular file"
            )
        if stat.S_ISDIR(st.st_mode):
            raise IsDirectoryError(
                f"{file_id!r} is a directory, not a file"
            )
        if not stat.S_ISREG(st.st_mode):
            raise UnsupportedEntryError(
                f"entry {file_id!r} is not a regular file "
                f"(mode {oct(st.st_mode)})"
            )
        return file_fd, st

    def _publish_no_overwrite(
        self,
        src_name: str,
        dst_name: str,
        *,
        src_fd: int,
        dst_fd: int,
        src_st: os.stat_result,
    ) -> None:
        """Link ``src_name`` to ``dst_name``, verify the inode, unlink source.

        The destination is stat'ed no-follow after the link and required to be
        the same ``(st_dev, st_ino)`` as the opened source descriptor. On a
        mismatch the just-created destination link is removed and a
        :class:`LocalStorageError` is raised, so a concurrent swap can never
        cause the wrong entry to be unlinked.
        """
        try:
            self._os_link(
                src_name,
                dst_name,
                src_dir_fd=src_fd,
                dst_dir_fd=dst_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise AlreadyExistsError(
                    f"entry {dst_name!r} already exists"
                ) from exc
            if exc.errno == errno.EXDEV:
                raise LocalStorageError(
                    f"cannot link across filesystems for {dst_name!r}: {exc}"
                ) from exc
            raise LocalStorageError(
                f"failed to link {src_name!r} to {dst_name!r}: {exc}"
            ) from exc

        try:
            dst_st = self._adapter._os_stat_nofollow(dst_name, dir_fd=dst_fd)
        except OSError as exc:
            self._unlink_quietly(dst_name, dst_fd)
            raise LocalStorageError(
                f"linked {dst_name!r} but failed to verify it: {exc}"
            ) from exc

        if (dst_st.st_dev, dst_st.st_ino) != (src_st.st_dev, src_st.st_ino):
            self._unlink_quietly(dst_name, dst_fd)
            raise LocalStorageError(
                f"source {src_name!r} changed while moving; "
                f"linked {dst_name!r} was removed and the move aborted"
            )

        try:
            self._os_unlink(src_name, dir_fd=src_fd)
        except OSError as exc:
            raise LocalStorageError(
                f"linked {dst_name!r} but failed to unlink source "
                f"{src_name!r}: both names now exist ({exc})"
            ) from exc

    def _unlink_quietly(self, name: str, parent_fd: int) -> None:
        try:
            self._os_unlink(name, dir_fd=parent_fd)
        except OSError:
            pass

    # -- public API ------------------------------------------------------------

    def move(
        self,
        source: FileRef,
        dest_parent: FileRef,
        new_name: str | None = None,
    ) -> FileMetadata:
        """Move regular file ``source`` under ``dest_parent``, never overwriting.

        ``new_name`` defaults to the source's own name. The move is published
        with an atomic no-replace ``link`` and then the source name is
        unlinked. If the unlink fails, both names now exist and no data is
        lost; a :class:`LocalStorageError` reports that state.
        """
        root_fd = self._adapter._check_source_and_state(source)
        self._adapter._check_source_and_state(dest_parent)

        source_parts = _parse_file_id(source.file_id)
        if not source_parts:
            raise IsDirectoryError(
                f"{source.file_id!r} is a directory, not a file"
            )
        self._reject_temp_in_parent(source_parts)

        dest_parts = _parse_file_id(dest_parent.file_id)
        self._reject_temp_in_parent(dest_parts)

        if new_name is None:
            dest_name = self._validate_name(source_parts[-1])
        else:
            dest_name = self._validate_name(new_name)

        owned: list[tuple[int, str]] = []
        try:
            source_fd = self._open_parent_dir(
                root_fd, source_parts[:-1], source.file_id, owned
            )
            src_name = source_parts[-1]
            file_fd, src_st = self._open_regular_file(
                source_fd, src_name, source.file_id, owned
            )

            if dest_parts == source_parts[:-1]:
                dest_fd = source_fd
            else:
                dest_fd = self._open_parent_dir(
                    root_fd, dest_parts, dest_parent.file_id, owned
                )

            self._publish_no_overwrite(
                src_name,
                dest_name,
                src_fd=source_fd,
                dst_fd=dest_fd,
                src_st=src_st,
            )

            return self._adapter._build_metadata(
                (*dest_parts, dest_name),
                kind="file",
                size_bytes=src_st.st_size,
            )
        finally:
            self._adapter._release_owned(owned)

    def trash(self, source: FileRef) -> FileMetadata:
        """Move regular file ``source`` into the hidden trash under the root.

        The trash directory is created on demand with mode ``0o700`` and
        opened no-follow. Trashed names are
        ``<UTC %Y%m%dT%H%M%S>-<8 hex>-<original name>``, with the original
        name shortened so the whole name stays within 255 UTF-8 bytes. Nothing
        is ever permanently deleted.
        """
        root_fd = self._adapter._check_source_and_state(source)

        source_parts = _parse_file_id(source.file_id)
        if not source_parts:
            raise IsDirectoryError(
                f"{source.file_id!r} is a directory, not a file"
            )
        self._reject_temp_in_parent(source_parts)

        owned: list[tuple[int, str]] = []
        try:
            source_fd = self._open_parent_dir(
                root_fd, source_parts[:-1], source.file_id, owned
            )
            src_name = source_parts[-1]
            file_fd, src_st = self._open_regular_file(
                source_fd, src_name, source.file_id, owned
            )

            trash_fd = self._open_trash_dir(root_fd, owned)
            dest_name = self._make_trash_name(src_name)

            self._publish_no_overwrite(
                src_name,
                dest_name,
                src_fd=source_fd,
                dst_fd=trash_fd,
                src_st=src_st,
            )

            return self._adapter._build_metadata(
                (TRASH_DIR, dest_name),
                kind="file",
                size_bytes=src_st.st_size,
            )
        finally:
            self._adapter._release_owned(owned)

    # -- trash helpers ---------------------------------------------------------

    def _open_trash_dir(
        self, root_fd: int, owned: list[tuple[int, str]]
    ) -> int:
        """Create (tolerating EEXIST) and no-follow-open the trash directory."""
        try:
            self._os_mkdir(TRASH_DIR, _TRASH_MKDIR_MODE, dir_fd=root_fd)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise LocalStorageError(
                    f"failed to create trash directory: {exc}"
                ) from exc

        try:
            trash_fd = self._os_open(
                TRASH_DIR, _dir_open_flags(), dir_fd=root_fd
            )
        except OSError as exc:
            if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)):
                raise UnsafePathError(
                    f"trash directory {TRASH_DIR!r} is a symbolic link"
                ) from exc
            raise LocalStorageError(
                f"failed to open trash directory: {exc}"
            ) from exc
        owned.append((trash_fd, "trash directory"))

        try:
            st = self._adapter._os_fstat(trash_fd)
        except OSError as exc:
            raise LocalStorageError(
                f"failed to fstat trash directory: {exc}"
            ) from exc
        if not stat.S_ISDIR(st.st_mode):
            if stat.S_ISLNK(st.st_mode):
                raise UnsafePathError(
                    f"trash directory {TRASH_DIR!r} is a symbolic link"
                )
            raise UnsupportedEntryError(
                f"trash path {TRASH_DIR!r} is not a directory"
            )
        return trash_fd

    def _make_trash_name(self, original: str) -> str:
        """Build a bounded, unique trash name for ``original``."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        token = secrets.token_hex(4)
        prefix = f"{stamp}-{token}-"
        budget = _MAX_NAME_BYTES - len(prefix.encode("utf-8"))
        if budget < 1:
            raise LocalStorageError(
                f"trash name prefix {prefix!r} leaves no room for a name"
            )

        encoded = original.encode("utf-8")
        if len(encoded) > budget:
            # Cut on a UTF-8 boundary so the truncated name still encodes.
            encoded = encoded[:budget]
            while encoded:
                try:
                    encoded.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    encoded = encoded[:-1]
            if not encoded:
                raise LocalStorageError(
                    f"cannot derive a trash name from {original!r}"
                )
        return prefix + encoded.decode("utf-8")
