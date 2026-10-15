"""Read-only storage contract and in-memory adapter for the drive tool.

MODULE-001: the smallest useful first module. This file defines the storage
contract (frozen value objects, a StorageAdapter protocol, and a small error
hierarchy) plus an InMemoryAdapter backed by synthetic fixtures.

Python 3.11+, standard library only. No network, no filesystem, no writes.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

__all__ = [
    "FileRef",
    "FileMetadata",
    "ScanBatch",
    "StorageAdapter",
    "StorageError",
    "ScanFailure",
    "ScanUnsupportedError",
    "SourceMismatchError",
    "NotFoundError",
    "NotDirectoryError",
    "IsDirectoryError",
    "EnumerationLimitError",
    "AlreadyExistsError",
    "ReadOnlyError",
    "InMemoryAdapter",
    "ROOT_ID",
    "MAX_SCAN_ENTRIES",
]

#: The synthetic root directory id used by the in-memory adapter.
ROOT_ID = "root"

#: Public upper bound for a single :meth:`scan_children` budget. This mirrors
#: the ``max_entries`` ceiling that :func:`drive_tool.search.find` accepts, so a
#: scanner and a caller can never disagree about what "too large" means.
MAX_SCAN_ENTRIES = 100_000

@dataclass(frozen=True)
class FileRef:
    """An opaque reference to a file (or the synthetic root) in a source."""

    source_id: str
    file_id: str

@dataclass(frozen=True)
class FileMetadata:
    """Metadata for a file or directory.

    ``parent_id`` and ``size_bytes`` are ``None`` for the synthetic root.
    """

    ref: FileRef
    name: str
    parent_id: str | None
    kind: str
    size_bytes: int | None

@dataclass(frozen=True)
class ScanBatch:
    """The outcome of a bounded single-directory scan.

    This is the return value of the optional ``scan_children`` adapter method.
    It reports a *bounded physical prefix* of one directory's raw entries, not
    a globally sorted snapshot of the directory.

    Invariants:

    - ``items`` holds only *supported* child entries (regular files and
      directories), sorted by ``(name, file_id)`` **within the scanned physical
      prefix only** -- not the globally sorted first ``inspected`` names.
    - ``0 <= len(items) <= inspected``.
    - ``inspected`` is the number of raw directory entries successfully yielded
      by the scan before filtering, stat, or omission. It never exceeds the
      ``max_entries`` budget passed to the scan.
    - ``complete`` is ``True`` only when end-of-directory was observed within
      the budget; ``False`` means the scan stopped at the budget without
      probing for a further entry. ``complete=False`` is therefore a
      conservative "unknown / possibly more" signal: an unknown end-of-directory
      is not proof that another entry exists.
    """

    items: tuple[FileMetadata, ...]
    inspected: int
    complete: bool

class StorageError(Exception):
    """Base class for all storage contract errors."""

class ScanFailure(StorageError):
    """A bounded directory scan failed after raw work had already been done.

    ``inspected`` is the number of raw directory entries successfully yielded by
    the scan before the failure (so partial scan work is never silently lost),
    and ``error`` is the underlying :class:`StorageError` that caused it. The
    original error is preserved as ``error`` and as the ``__cause__`` chain.

    Failures that occur *before* any entry is yielded (for example opening the
    directory) are ordinary storage errors and are not wrapped.
    """

    def __init__(self, inspected: int, error: StorageError) -> None:
        self.inspected = inspected
        self.error = error
        super().__init__(
            f"directory scan failed after inspecting {inspected} "
            f"entr{'y' if inspected == 1 else 'ies'}: {error}"
        )

class ScanUnsupportedError(StorageError):
    """Raised when a bounded directory scan is required but not supported.

    ``find`` needs to bound *raw* scan work, which supported-item counts cannot
    express, so an adapter without a callable ``scan_children`` cannot serve a
    search. This is a hard capability requirement, not a fallback trigger.
    """

class SourceMismatchError(StorageError):
    """The reference's source_id does not match the adapter's source."""

class NotFoundError(StorageError):
    """The referenced file_id is unknown to this adapter."""

class NotDirectoryError(StorageError):
    """A directory-only operation was requested on a non-directory."""

class IsDirectoryError(StorageError):
    """A file-only operation was requested on a directory."""

class EnumerationLimitError(StorageError):
    """A directory enumeration exceeded its configured entry limit.

    ``limit`` is the configured maximum that was exceeded.
    """

    def __init__(self, limit: int, message: str | None = None) -> None:
        self.limit = limit
        super().__init__(
            message or f"directory entry count exceeded limit of {limit}"
        )

class AlreadyExistsError(StorageError):
    """A create-only operation found the target name already present."""

class ReadOnlyError(StorageError):
    """A mutating operation was attempted on a read-only storage source."""

@runtime_checkable
class StorageAdapter(Protocol):
    """The read-only storage contract.

    Implementations expose a single ``source_id`` and resolve opaque
    ``file_id`` values within it. Every method rejects a mismatched
    ``source_id`` before it inspects the ``file_id``.
    """

    source_id: str

    def stat(self, ref: FileRef) -> FileMetadata:
        """Return metadata for ``ref``."""
        ...

    def list_children(
        self, parent: FileRef, *, max_entries: int | None = None
    ) -> list[FileMetadata]:
        """Return metadata for the direct children of directory ``parent``.

        When ``max_entries`` is not ``None`` it is an upper bound on the
        number of directory entries examined; adapters may raise
        :class:`EnumerationLimitError` when the directory holds more.
        """
        ...

    def read_bytes(self, ref: FileRef) -> bytes:
        """Return the exact bytes stored for file ``ref``."""
        ...

_INVALID_FILENAME_TOKENS = (".", "..", ROOT_ID)
_INVALID_FILENAME_CHARS = ("/", "\\", "\x00")

def _validate_filename(name: object) -> str:
    """Validate a fixture filename, returning it if valid.

    A valid filename is a non-empty string that is not ``"."``, ``".."``,
    or ``"root"`` and that contains no slash, backslash, or NUL.
    """
    if not isinstance(name, str):
        raise ValueError(f"filename must be a string, got {type(name).__name__}")
    if name == "":
        raise ValueError("filename must be non-empty")
    if name in _INVALID_FILENAME_TOKENS:
        raise ValueError(f"filename {name!r} is reserved")
    for char in _INVALID_FILENAME_CHARS:
        if char in name:
            raise ValueError(f"filename {name!r} must not contain {char!r}")
    return name

def _validate_max_entries(max_entries: object) -> None:
    """Validate an optional ``max_entries``: ``None`` or ``int >= 1``."""
    if max_entries is None:
        return
    if isinstance(max_entries, bool) or not isinstance(max_entries, int):
        raise ValueError(
            f"max_entries must be a positive int or None, "
            f"got {type(max_entries).__name__}"
        )
    if max_entries < 1:
        raise ValueError(f"max_entries must be >= 1, got {max_entries}")


def _validate_scan_max_entries(max_entries: object) -> None:
    """Validate a mandatory ``scan_children`` budget.

    ``max_entries`` is a required positive ``int`` in ``1..MAX_SCAN_ENTRIES``;
    ``bool`` (including ``True``/``False``) and ``None`` are rejected.
    """
    if isinstance(max_entries, bool) or not isinstance(max_entries, int):
        raise ValueError(
            f"max_entries must be a positive int, "
            f"got {type(max_entries).__name__}"
        )
    if max_entries < 1:
        raise ValueError(f"max_entries must be >= 1, got {max_entries}")
    if max_entries > MAX_SCAN_ENTRIES:
        raise ValueError(
            f"max_entries must be <= {MAX_SCAN_ENTRIES}, got {max_entries}"
        )

class InMemoryAdapter:
    """A read-only adapter over flat synthetic fixtures held in memory.

    ``files`` maps a filename to its exact bytes. The filename is also the
    file's ``file_id``. A synthetic root directory (id ``"root"``) is the
    single parent of every fixture file.
    """

    def __init__(self, source_id: str, files: Mapping[str, bytes]) -> None:
        if not isinstance(source_id, str) or source_id == "":
            raise ValueError("source_id must be a non-empty string")
        if not isinstance(files, Mapping):
            raise ValueError(
                f"files must be a mapping, got {type(files).__name__}"
            )

        # Validate first, then copy, so a bad mapping never half-populates us.
        validated: dict[str, bytes] = {}
        for name, payload in files.items():
            _validate_filename(name)
            if not isinstance(payload, bytes):
                raise ValueError(
                    f"payload for {name!r} must be bytes, "
                    f"got {type(payload).__name__}"
                )
            validated[name] = payload

        self._source_id = source_id
        # Copy the mapping so later external mutation cannot affect us.
        self._files: dict[str, bytes] = dict(validated)

    @property
    def source_id(self) -> str:
        return self._source_id

    # -- internals ---------------------------------------------------------

    def _root_ref(self) -> FileRef:
        return FileRef(self._source_id, ROOT_ID)

    def _root_metadata(self) -> FileMetadata:
        return FileMetadata(
            ref=self._root_ref(),
            name="",
            parent_id=None,
            kind="directory",
            size_bytes=None,
        )

    def _file_metadata(self, name: str) -> FileMetadata:
        return FileMetadata(
            ref=FileRef(self._source_id, name),
            name=name,
            parent_id=ROOT_ID,
            kind="file",
            size_bytes=len(self._files[name]),
        )

    def _check_source(self, ref: FileRef) -> None:
        """Reject a mismatched source_id. Always runs before file_id checks."""
        if not isinstance(ref, FileRef):
            raise TypeError(f"expected FileRef, got {type(ref).__name__}")
        if ref.source_id != self._source_id:
            raise SourceMismatchError(
                f"ref source_id {ref.source_id!r} does not match "
                f"adapter source_id {self._source_id!r}"
            )

    # -- contract ----------------------------------------------------------

    def stat(self, ref: FileRef) -> FileMetadata:
        self._check_source(ref)
        if ref.file_id == ROOT_ID:
            return self._root_metadata()
        if ref.file_id not in self._files:
            raise NotFoundError(f"unknown file_id {ref.file_id!r}")
        return self._file_metadata(ref.file_id)

    def list_children(
        self, parent: FileRef, *, max_entries: int | None = None
    ) -> list[FileMetadata]:
        self._check_source(parent)
        _validate_max_entries(max_entries)
        if parent.file_id == ROOT_ID:
            if max_entries is not None and len(self._files) > max_entries:
                raise EnumerationLimitError(
                    max_entries,
                    f"directory {parent.file_id!r} exceeds limit of "
                    f"{max_entries} entries",
                )
            return [self._file_metadata(name) for name in sorted(self._files)]
        if parent.file_id not in self._files:
            raise NotFoundError(f"unknown file_id {parent.file_id!r}")
        raise NotDirectoryError(f"{parent.file_id!r} is not a directory")

    def scan_children(self, parent: FileRef, *, max_entries: int) -> ScanBatch:
        """Return a bounded, insertion-order prefix of the fixture children.

        ``max_entries`` is a mandatory positive ``int``. The fixture mapping is
        never sorted or fully materialized: at most ``max_entries`` names are
        taken in insertion order (a bounded prefix), and only that prefix is
        sorted by ``(name, file_id)``. Because the total length is known, an
        in-memory scan reports exact completeness: ``complete=True`` when every
        fixture entry fit in the prefix, ``complete=False`` otherwise.
        """
        self._check_source(parent)
        _validate_scan_max_entries(max_entries)
        if parent.file_id == ROOT_ID:
            total = len(self._files)
            prefix_names = list(itertools.islice(self._files, max_entries))
            items = tuple(
                sorted(
                    (self._file_metadata(name) for name in prefix_names),
                    key=lambda meta: (meta.name, meta.ref.file_id),
                )
            )
            return ScanBatch(
                items=items,
                inspected=len(prefix_names),
                complete=total <= max_entries,
            )
        if parent.file_id not in self._files:
            raise NotFoundError(f"unknown file_id {parent.file_id!r}")
        raise NotDirectoryError(f"{parent.file_id!r} is not a directory")

    def read_bytes(self, ref: FileRef) -> bytes:
        self._check_source(ref)
        if ref.file_id == ROOT_ID:
            raise IsDirectoryError(f"{ROOT_ID!r} is a directory, not a file")
        if ref.file_id not in self._files:
            raise NotFoundError(f"unknown file_id {ref.file_id!r}")
        return self._files[ref.file_id]
