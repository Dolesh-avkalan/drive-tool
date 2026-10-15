"""Safe local writes: atomic no-overwrite save and mkdir (MODULE-005).

This module implements :class:`LocalWriter`, a write-side wrapper around an
open :class:`drive_tool.local.LocalAdapter`. Writes are strictly contained to
the adapter's anchored root directory and use anchored, no-follow opens.
Publication is an atomic no-replace ``link`` by default; ``overwrite=True``
uses an atomic ``rename`` after a no-follow target check.

Threat boundaries (see MODULE-003): hard links, bind mounts, submounts, and
adversarial provisioning of the root directory itself remain operator
responsibilities. Data is fully buffered as ``bytes`` in memory; streaming
writes are out of scope. A crash between syscalls can leave a temp file
behind; stale temp-file cleanup is out of scope. Python 3.11+, standard
library only, POSIX only.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat

from drive_tool.local import (
    LocalAdapter,
    LocalStorageError,
    NotDirectoryError,
    TEMP_PREFIX,
    UnsafePathError,
    UnsupportedEntryError,
    UnsupportedPlatformError,
    _dir_open_flags,
    _parse_file_id,
)
from drive_tool.storage import (
    AlreadyExistsError,
    FileMetadata,
    FileRef,
    IsDirectoryError,
    NotFoundError,
)

__all__ = [
    "DEFAULT_MAX_WRITE_BYTES",
    "TEMP_PREFIX",
    "WriteLimitExceededError",
    "LocalWriter",
]

#: Default upper bound (10 MiB) for :meth:`LocalWriter.write_bytes`.
DEFAULT_MAX_WRITE_BYTES = 10_485_760

_MAX_NAME_BYTES = 255
_TEMP_NAME_RETRIES = 3
_TEMP_OPEN_MODE = 0o644
_MKDIR_MODE = 0o755

class WriteLimitExceededError(LocalStorageError):
    """Raised when the ``write_bytes`` payload exceeds ``max_write_bytes``."""

def _check_writer_platform_capabilities() -> None:
    """Fail closed when the runtime lacks write-related POSIX features."""
    for flag_name in ("O_WRONLY", "O_CREAT", "O_EXCL", "O_NOFOLLOW", "O_CLOEXEC"):
        if not isinstance(getattr(os, flag_name, None), int):
            raise UnsupportedPlatformError(
                f"LocalWriter requires os.{flag_name} support on this platform"
            )
    for func_name in (
        "open",
        "close",
        "fstat",
        "stat",
        "write",
        "fsync",
        "link",
        "rename",
        "unlink",
        "mkdir",
    ):
        if not hasattr(os, func_name):
            raise UnsupportedPlatformError(
                f"LocalWriter requires os.{func_name} support on this platform"
            )
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    for fn in (os.link, os.rename, os.unlink, os.mkdir):
        if fn not in supports_dir_fd:
            raise UnsupportedPlatformError(
                f"LocalWriter requires dir_fd support for os.{fn.__name__}"
            )
    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", set())
    if os.link not in supports_follow_symlinks:
        raise UnsupportedPlatformError(
            "LocalWriter requires follow_symlinks=False support for os.link"
        )

class LocalWriter:
    """Write-side wrapper around an open :class:`LocalAdapter`.

    A bare adapter stays read-only; a caller that wants to write must build a
    writer, which reuses the adapter's containment helpers so no path logic is
    duplicated. Every method checks source mismatch first, then closed
    adapter, then its arguments. The writer never raises
    :class:`drive_tool.storage.ReadOnlyError`; that error is reserved for
    later policy layers.
    """

    def __init__(
        self,
        adapter: LocalAdapter,
        *,
        max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES,
    ) -> None:
        if not isinstance(adapter, LocalAdapter):
            raise TypeError(
                f"adapter must be a LocalAdapter, got {type(adapter).__name__}"
            )
        if (
            isinstance(max_write_bytes, bool)
            or not isinstance(max_write_bytes, int)
            or max_write_bytes < 1
        ):
            raise ValueError("max_write_bytes must be a positive integer")

        _check_writer_platform_capabilities()
        self._adapter = adapter
        self._max_write_bytes = max_write_bytes

    @property
    def max_write_bytes(self) -> int:
        """The maximum number of bytes permitted for a single write."""
        return self._max_write_bytes

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

    def _os_close(self, fd: int) -> None:
        os.close(fd)

    def _os_write(self, fd: int, data) -> int:
        return os.write(fd, data)

    def _os_fsync(self, fd: int) -> None:
        os.fsync(fd)

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

    def _os_rename(
        self,
        src: str,
        dst: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        os.rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def _os_unlink(self, path: str, *, dir_fd: int) -> None:
        os.unlink(path, dir_fd=dir_fd)

    def _os_mkdir(self, path: str, mode: int = _MKDIR_MODE, *, dir_fd: int) -> None:
        os.mkdir(path, mode, dir_fd=dir_fd)

    # -- small helpers --------------------------------------------------------

    def _close_quietly(self, fd: int) -> None:
        try:
            self._os_close(fd)
        except OSError:
            pass

    def _unlink_quietly(self, name: str, parent_fd: int) -> None:
        try:
            self._os_unlink(name, dir_fd=parent_fd)
        except OSError:
            pass

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

    # -- write helpers ---------------------------------------------------------

    def _create_temp_file(self, parent_fd: int) -> tuple[str, int]:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        last_exc: OSError | None = None
        for _ in range(_TEMP_NAME_RETRIES):
            candidate = TEMP_PREFIX + secrets.token_hex(8)
            try:
                fd = self._os_open(
                    candidate, flags, _TEMP_OPEN_MODE, dir_fd=parent_fd
                )
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    last_exc = exc
                    continue
                raise LocalStorageError(
                    f"failed to create temporary file: {exc}"
                ) from exc
            return candidate, fd
        raise LocalStorageError(
            f"failed to create temporary file after "
            f"{_TEMP_NAME_RETRIES} attempts: {last_exc}"
        )

    def _write_all(self, fd: int, data: bytes) -> None:
        view = memoryview(data)
        total = len(view)
        offset = 0
        while offset < total:
            try:
                written = self._os_write(fd, view[offset:])
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                raise LocalStorageError(
                    f"failed while writing temporary file: {exc}"
                ) from exc
            if written <= 0:
                raise LocalStorageError(
                    "write returned no bytes while data remained"
                )
            offset += written

    def _check_overwrite_target(self, parent_fd: int, name: str) -> None:
        try:
            st = self._adapter._os_stat_nofollow(name, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return
            if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)):
                raise UnsafePathError(
                    f"symlink encountered at {name!r}"
                ) from exc
            raise LocalStorageError(
                f"failed to inspect target {name!r}: {exc}"
            ) from exc
        if stat.S_ISLNK(st.st_mode):
            raise UnsafePathError(f"target {name!r} is a symbolic link")
        if stat.S_ISDIR(st.st_mode):
            raise IsDirectoryError(
                f"target {name!r} is a directory, not a file"
            )
        if not stat.S_ISREG(st.st_mode):
            raise UnsupportedEntryError(
                f"target {name!r} is not a regular file"
            )

    # -- public API ------------------------------------------------------------

    def mkdir(
        self,
        parent: FileRef,
        name: str,
        *,
        exist_ok: bool = False,
    ) -> FileMetadata:
        """Create a single directory component ``name`` under ``parent``.

        When ``exist_ok`` is true and ``name`` already exists as a real
        directory, its metadata is returned without touching the filesystem.
        A symlink occupying ``name`` is never accepted.
        """
        root_fd = self._adapter._check_source_and_state(parent)
        parts = _parse_file_id(parent.file_id)
        self._reject_temp_in_parent(parts)
        self._validate_name(name)
        if not isinstance(exist_ok, bool):
            raise ValueError(
                f"exist_ok must be a bool, got {type(exist_ok).__name__}"
            )

        owned: list[tuple[int, str]] = []
        try:
            parent_fd = self._open_parent_dir(
                root_fd, parts, parent.file_id, owned
            )
            try:
                self._os_mkdir(name, _MKDIR_MODE, dir_fd=parent_fd)
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise LocalStorageError(
                        f"failed to create directory {name!r}: {exc}"
                    ) from exc
                return self._handle_mkdir_eexist(
                    parent_fd, name, parts, exist_ok
                )
            try:
                self._os_fsync(parent_fd)
            except OSError as exc:
                raise LocalStorageError(
                    f"failed to fsync parent after mkdir {name!r}: {exc}"
                ) from exc
            return self._adapter._build_metadata(
                (*parts, name), kind="directory", size_bytes=None
            )
        finally:
            self._adapter._release_owned(owned)

    def _handle_mkdir_eexist(
        self,
        parent_fd: int,
        name: str,
        parts: tuple[str, ...],
        exist_ok: bool,
    ) -> FileMetadata:
        try:
            st = self._adapter._os_stat_nofollow(name, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", -1)):
                raise UnsafePathError(
                    f"symlink encountered at {name!r}"
                ) from exc
            if exc.errno == errno.ENOENT:
                raise AlreadyExistsError(
                    f"entry {name!r} already exists"
                ) from None
            raise LocalStorageError(
                f"failed to inspect existing entry {name!r}: {exc}"
            ) from exc
        if stat.S_ISLNK(st.st_mode):
            raise UnsafePathError(f"entry {name!r} is a symbolic link")
        if stat.S_ISDIR(st.st_mode) and exist_ok:
            return self._adapter._build_metadata(
                (*parts, name), kind="directory", size_bytes=None
            )
        raise AlreadyExistsError(f"entry {name!r} already exists")

    def write_bytes(
        self,
        parent: FileRef,
        name: str,
        data: bytes,
        *,
        overwrite: bool = False,
    ) -> FileMetadata:
        """Write ``data`` to ``name`` under ``parent`` and return metadata.

        With ``overwrite=False`` the publish step is an atomic no-replace
        ``link``; with ``overwrite=True`` the existing target (if any) must be
        a regular file and is replaced by an atomic ``rename``.
        """
        root_fd = self._adapter._check_source_and_state(parent)
        parts = _parse_file_id(parent.file_id)
        self._reject_temp_in_parent(parts)
        self._validate_name(name)
        if not isinstance(overwrite, bool):
            raise ValueError(
                f"overwrite must be a bool, got {type(overwrite).__name__}"
            )
        if not isinstance(data, bytes):
            raise ValueError(
                f"data must be bytes, got {type(data).__name__}"
            )
        if len(data) > self._max_write_bytes:
            raise WriteLimitExceededError(
                f"data size {len(data)} exceeds max_write_bytes "
                f"{self._max_write_bytes}"
            )

        owned: list[tuple[int, str]] = []
        parent_fd: int | None = None
        tmp_fd: int | None = None
        tmp_name: str | None = None
        tmp_on_disk = False
        try:
            parent_fd = self._open_parent_dir(
                root_fd, parts, parent.file_id, owned
            )

            tmp_name, tmp_fd = self._create_temp_file(parent_fd)
            tmp_on_disk = True

            self._write_all(tmp_fd, data)
            try:
                self._os_fsync(tmp_fd)
            except OSError as exc:
                raise LocalStorageError(
                    f"failed to fsync temporary file for {name!r}: {exc}"
                ) from exc

            fd_to_close = tmp_fd
            tmp_fd = None
            try:
                self._os_close(fd_to_close)
            except OSError as exc:
                raise LocalStorageError(
                    f"failed to close temporary file for {name!r}: {exc}"
                ) from exc

            if overwrite:
                self._check_overwrite_target(parent_fd, name)
                try:
                    self._os_rename(
                        tmp_name,
                        name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                    )
                except OSError as exc:
                    if exc.errno in (
                        errno.EISDIR,
                        errno.ENOTEMPTY,
                        errno.EEXIST,
                    ):
                        raise IsDirectoryError(
                            f"cannot replace {name!r} with a file"
                        ) from exc
                    if exc.errno in (
                        errno.ELOOP,
                        getattr(errno, "EMLINK", -1),
                    ):
                        raise UnsafePathError(
                            f"symlink encountered replacing {name!r}"
                        ) from exc
                    raise LocalStorageError(
                        f"failed to replace {name!r}: {exc}"
                    ) from exc
                tmp_on_disk = False
            else:
                try:
                    self._os_link(
                        tmp_name,
                        name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    if exc.errno == errno.EEXIST:
                        raise AlreadyExistsError(
                            f"entry {name!r} already exists"
                        ) from exc
                    if exc.errno in (
                        errno.EPERM,
                        errno.ENOTSUP,
                        errno.EOPNOTSUPP,
                        errno.EXDEV,
                        errno.ENOSYS,
                        errno.EMLINK,
                    ):
                        raise UnsupportedPlatformError(
                            f"atomic link publish not supported for "
                            f"{name!r}: {exc}"
                        ) from exc
                    raise LocalStorageError(
                        f"failed to link temporary file to {name!r}: {exc}"
                    ) from exc
                tmp_on_disk = False
                try:
                    self._os_unlink(tmp_name, dir_fd=parent_fd)
                except OSError as exc:
                    raise LocalStorageError(
                        f"failed to unlink temp name after linking "
                        f"{name!r}: {exc}"
                    ) from exc

            try:
                self._os_fsync(parent_fd)
            except OSError as exc:
                raise LocalStorageError(
                    f"failed to fsync parent after write {name!r}: {exc}"
                ) from exc

            return self._adapter._build_metadata(
                (*parts, name), kind="file", size_bytes=len(data)
            )
        except BaseException:
            if tmp_fd is not None:
                self._close_quietly(tmp_fd)
                tmp_fd = None
            if tmp_on_disk and tmp_name is not None and parent_fd is not None:
                self._unlink_quietly(tmp_name, parent_fd)
            raise
        finally:
            self._adapter._release_owned(owned)
