# MODULE-003 — bounded read-only local adapter (Gemini, harder)

Repository: https://github.com/Dolesh-avkalan/drive-tool
Worker: Gemini web chatbot, model Gemini 3.1 Pro selected by messenger.
Base commit: 194d4ad57de6dd895c6eaaf7faf4d08583eab02b (MODULE-001, not main).
Branch for Sarvam's later submission: feature/module-003-local-readonly
Commit message: Implement MODULE-003 bounded local read-only adapter from Gemini

## Role and output
You implement this module and write tests. You cannot push to GitHub in this workflow. API Sarvam captures your complete output and gives it to browser Sarvam, which assembles the files, executes tests in its sandbox, and pushes the separate branch. Return complete files, not sketches.
Read MODULE-001 source at the pinned base. If you cannot fetch it, request it; do not guess imports.
No real VPS or PC filesystem access, service changes, OAuth, writes/deletes, or network API. Tests use temporary synthetic directories only.

## Exact additions
- drive_tool/local.py
- tests/test_local.py
- docs/evidence/MODULE-003-GEMINI.md
Do not change existing MODULE-001 files.
Sarvam later adds docs/evidence/MODULE-003.md with its own real test run and transport provenance.

## Contract
Python 3.11+, standard library only; Linux/POSIX capability-gated initial implementation.
LocalAdapter(source_id: str, root: str | os.PathLike, *, max_read_bytes: int = 1048576) implements stat, list_children, read_bytes from StorageAdapter; support close() and context manager.
source_id nonempty str; max_read_bytes int >0 (bool invalid); invalid config raises ValueError.
Root must be an absolute existing directory, with no symlink in any component. Establish a directory descriptor through no-follow traversal from /; retain it until close. Missing required descriptor/no-follow platform features raise UnsupportedPlatformError clearly; do not emulate weaker checks silently.
FileRef(source_id, "root") is the synthetic root.
All real entry IDs have prefix "p/" followed by canonical relative POSIX path, e.g. "p/notes/report.txt". This avoids collision with a real filename "root".
Reject malformed IDs, absolute paths, empty/dot/dot-dot components, repeated/trailing slashes, NUL and backslash with UnsafePathError. Top-level real filename root has ID "p/root".
Use FileMetadata from MODULE-001. Root: name="", parent_id=None, kind="directory", size_bytes=None.
Entry: basename name; parent_id="root" for top-level, otherwise "p/"+relative parent; kind="file" or "directory"; size_bytes actual file size or None for directory.
Every public storage operation checks source mismatch FIRST (even a malformed/unknown ID). Wrong source raises existing SourceMismatchError.

## Containment and bounded reads — mandatory
A string resolve()/startswith() check is not sufficient. Traverse relative directory components using anchored descriptors and no-follow opens; do not follow symlinks for intermediate or final paths.
Do not let symlink replacement between checks/opens redirect access outside the root.
Root descriptor anchors the initially opened directory even if its pathname is renamed/replaced later.
Use descriptor lifetime management so repeated operations/failed traversals do not leak descriptors. Define operations after close to raise AdapterClosedError (source mismatch still first). Concurrency with close need not be supported; document this.
Only regular files and directories are supported. Reject reading FIFOs/devices/sockets without blocking: opening must not hang before type checking. Use safe nonblocking/type-check strategy, and explain it.
Directory listing returns supported regular files/directories sorted by name; omit symlinks/special entries. An entry disappearing during listing may be skipped. Do not follow it while collecting metadata.
stat/read on symlink raises UnsafePathError; on special entry raises UnsupportedEntryError.
read_bytes on root/directory raises existing IsDirectoryError; list_children on file raises existing NotDirectoryError; nonexistent canonical path raises existing NotFoundError.
Use LocalStorageError(StorageError) for permission/other OS failures with useful context and preserved cause. Specific local errors (UnsafePathError, UnsupportedEntryError, ReadLimitExceededError, AdapterClosedError, UnsupportedPlatformError) extend LocalStorageError.
ReadLimitExceededError when file exceeds max_read_bytes: check fstat and enforce during reading with at most max_read_bytes+1 bytes read, so a file growing after stat cannot bypass the limit. No unbounded read and no allocating proportional to untrusted file size.
Explicitly document that external writers may change file contents during a read; this module does not promise a coherent snapshot/checksum. Hard links, mount policy, and adversarial root provisioning are outside this module's protection; configured roots must be operator-controlled. Do not claim production security completeness.

## Acceptance/tests
Use unittest, tempfile, tiny fixtures and deterministic tests:
- Nested stat/list/read; exact binary/empty files; metadata/parent IDs; real filename root.
- Sorted listing, empty directory.
- Source mismatch precedence across all three methods.
- Missing entries; directory/file operation errors; all malformed ID classes.
- Symlink to file and to directory outside the configured root cannot be read/traversed; listing omits them. Final and intermediate symlinks tested.
- Symlink root/ancestor rejected at configuration.
- Root renamed and its old pathname replaced by outside symlink: old adapter stays anchored to original directory.
- Read limit below/exact/above threshold and enforcement against simulated growth (deterministic bounded-read unit seam if needed; explain what it proves).
- FIFO/special entry test cannot hang; use timeout-bounded child process if needed.
- Close/context manager cleanup and failure-path descriptor cleanup with a bounded Linux fd-count test or deterministic mock.
- Explicit unsupported-platform gate; existing MODULE-001 tests unaffected.
- Include a deterministic check/symlink-swap adversarial test where feasible; distinguish mechanism review from stress-test proof.
Tests must be skippable with explicit reason where a required platform facility is absent; never silently claim those skipped tests prove containment.

## Deliver and evidence
Return each exact path and full file contents in fenced blocks, or downloadable individual files PLUS accessible full source if download cannot be captured. No ellipses, truncated files, or executable payload hidden in links.
MODULE-003-GEMINI.md: design rationale, threat boundaries, tests written, any actual execution command/log/runtime, and unresolved risks. If you cannot execute code say TESTS_NOT_RUN; Sarvam will execute it separately.
Final status: GENERATED or BLOCKED; include source filenames, exact base, tests-run/not-run, limitations. Do not claim a push.
Sarvam's later testing report must identify your source and any fixes separately. Security review and VPS integration remain director gates after submission.


# Pinned base source included offline
Base commit: 194d4ad57de6dd895c6eaaf7faf4d08583eab02b. These are existing source files for imports/context. Do not modify them. No web retrieval is needed.

## drive_tool/storage.py
```python
"""Read-only storage contract and in-memory adapter for the drive tool.

MODULE-001: the smallest useful first module. This file defines the storage
contract (frozen value objects, a StorageAdapter protocol, and a small error
hierarchy) plus an InMemoryAdapter backed by synthetic fixtures.

Python 3.11+, standard library only. No network, no filesystem, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

__all__ = [
    "FileRef",
    "FileMetadata",
    "StorageAdapter",
    "StorageError",
    "SourceMismatchError",
    "NotFoundError",
    "NotDirectoryError",
    "IsDirectoryError",
    "InMemoryAdapter",
    "ROOT_ID",
]

#: The synthetic root directory id used by the in-memory adapter.
ROOT_ID = "root"


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


class StorageError(Exception):
    """Base class for all storage contract errors."""


class SourceMismatchError(StorageError):
    """The reference's source_id does not match the adapter's source."""


class NotFoundError(StorageError):
    """The referenced file_id is unknown to this adapter."""


class NotDirectoryError(StorageError):
    """A directory-only operation was requested on a non-directory."""


class IsDirectoryError(StorageError):
    """A file-only operation was requested on a directory."""


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

    def list_children(self, parent: FileRef) -> list[FileMetadata]:
        """Return metadata for the direct children of directory ``parent``."""
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

    def list_children(self, parent: FileRef) -> list[FileMetadata]:
        self._check_source(parent)
        if parent.file_id == ROOT_ID:
            return [self._file_metadata(name) for name in sorted(self._files)]
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

```

## drive_tool/__init__.py
```python
"""drive_tool — read-only storage contract and in-memory adapter (MODULE-001)."""

from drive_tool.storage import (
    ROOT_ID,
    FileMetadata,
    FileRef,
    InMemoryAdapter,
    IsDirectoryError,
    NotDirectoryError,
    NotFoundError,
    SourceMismatchError,
    StorageAdapter,
    StorageError,
)

__all__ = [
    "ROOT_ID",
    "FileRef",
    "FileMetadata",
    "StorageAdapter",
    "StorageError",
    "SourceMismatchError",
    "NotFoundError",
    "NotDirectoryError",
    "IsDirectoryError",
    "InMemoryAdapter",
]

```
