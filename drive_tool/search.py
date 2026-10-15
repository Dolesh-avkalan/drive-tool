"""Bounded breadth-first name search over the storage contract.

MODULE-005A: :func:`find` walks a storage tree breadth-first, level by level,
and returns matching files and directories while enforcing a raw-entry budget, a
result budget, and a depth bound. It never blocks on a large folder: each
directory is scanned through the adapter's ``scan_children`` with only the
remaining entry budget, so a huge directory is stopped early with
``truncated=True`` rather than fully read.

Search now charges *raw scan work*, not returned metadata. A single bounded
directory scan is charged in full the moment it returns, before any name is
matched, so ``FindResult.inspected`` counts the raw directory entries the
adapter actually examined -- including filtered, unsupported, and vanished
entries -- even when ``max_results`` stops matching early. A partially scanned
folder keeps the matches it did find, but its items are ordered only within the
scanned physical prefix; that is not a claim about the global order of the
directory.

Bounded search requires a callable ``scan_children``: supported-item counts
cannot express raw scan work, so an adapter without it raises
:class:`~drive_tool.storage.ScanUnsupportedError` rather than silently falling
back to ``list_children``.

Python 3.11+, standard library only. No threads, no retries, no sleeps.
"""

from __future__ import annotations

from dataclasses import dataclass

from drive_tool.storage import (
    FileMetadata,
    FileRef,
    ScanFailure,
    ScanUnsupportedError,
    StorageAdapter,
    StorageError,
)

__all__ = ["FindResult", "find", "ScanUnsupportedError"]


@dataclass(frozen=True)
class FindResult:
    """The outcome of a bounded :func:`find`.

    ``items`` are the matches in walk order, ``truncated`` is ``True`` when a
    bound stopped the walk (``reason`` names it), ``skipped`` holds the
    ``file_id`` of every directory that could not be scanned, and ``inspected``
    counts the **raw directory entries examined** by the adapter.

    ``inspected`` is the sum of the ``ScanBatch.inspected`` values charged as
    each directory was scanned, so it reflects the raw scan work even when
    ``max_results`` stopped matching early or when filtered/unsupported/vanished
    entries never produced metadata. It can therefore exceed both the number of
    ``items`` returned and the number of entries actually tested for a match.
    """

    items: tuple[FileMetadata, ...]
    truncated: bool
    reason: str | None
    inspected: int
    skipped: tuple[str, ...]


def _validate_int_in_range(
    name: str, value: object, low: int, high: int
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{name} must be an int in {low}..{high}, "
            f"got {type(value).__name__}"
        )
    if value < low or value > high:
        raise ValueError(f"{name} must be in {low}..{high}, got {value}")


def _validate_batch_report(
    batch: object, remaining: int
) -> tuple[tuple[FileMetadata, ...], int, bool]:
    """Validate a scanner's reported batch loudly, without clamping.

    ``items`` must already be a tuple (an arbitrary iterable is never consumed
    to measure it), ``inspected`` a plain ``int`` in ``0..remaining``, and
    ``complete`` a real ``bool``. A batch that claims to be incomplete but did
    not exhaust the remaining budget is rejected rather than silently accepted
    as a no-progress scan.
    """
    items = getattr(batch, "items", None)
    if not isinstance(items, tuple):
        raise StorageError(
            "scan_children must return a ScanBatch whose items is a tuple, "
            f"got {type(items).__name__}"
        )
    inspected = getattr(batch, "inspected", None)
    if isinstance(inspected, bool) or not isinstance(inspected, int):
        raise StorageError(
            f"ScanBatch.inspected must be an int, got {type(inspected).__name__}"
        )
    if inspected < 0 or inspected > remaining:
        raise StorageError(
            f"ScanBatch.inspected {inspected} is outside the range 0..{remaining}"
        )
    complete = getattr(batch, "complete", None)
    if not isinstance(complete, bool):
        raise StorageError(
            f"ScanBatch.complete must be a bool, got {type(complete).__name__}"
        )
    if len(items) > inspected:
        raise StorageError(
            f"ScanBatch reports {len(items)} items but only {inspected} inspected"
        )
    if not complete and inspected != remaining:
        raise StorageError(
            f"an incomplete ScanBatch must exhaust the remaining budget "
            f"{remaining}, but reported inspected={inspected}"
        )
    return items, inspected, complete


def _validate_failure_inspected(failure: ScanFailure, remaining: int) -> int:
    """Validate the ``inspected`` count carried by a :class:`ScanFailure`."""
    inspected = failure.inspected
    if isinstance(inspected, bool) or not isinstance(inspected, int):
        raise StorageError(
            f"ScanFailure.inspected must be an int, got {type(inspected).__name__}"
        )
    if inspected < 0 or inspected > remaining:
        raise StorageError(
            f"ScanFailure.inspected {inspected} is outside the range "
            f"0..{remaining}"
        )
    return inspected


def find(
    adapter: StorageAdapter,
    parent: FileRef,
    name_contains: str,
    *,
    max_results: int = 20,
    max_entries: int = 5000,
    max_depth: int = 4,
    case_sensitive: bool = False,
) -> FindResult:
    """Return matches for ``name_contains`` in the subtree rooted at ``parent``.

    The walk is breadth-first; directories within a level are visited in name
    order and matches are returned in walk order (shallower first, then by
    name). A match is a substring test on the entry name (casefolded unless
    ``case_sensitive``), and both files and directories can match.

    All arguments are validated before the adapter is touched. The adapter must
    expose a callable ``scan_children``; otherwise
    :class:`~drive_tool.storage.ScanUnsupportedError` is raised before any
    enumeration is attempted. Each directory is scanned with only the remaining
    entry budget (``max_entries - inspected``), and the batch's ``inspected`` is
    charged immediately, so ``inspected`` measures raw scan work even when
    ``max_results`` stops matching early.

    A ``ScanFailure`` re-raises the original error at the starting root, or, at
    depth ``> 0``, is recorded in ``skipped`` after charging its ``inspected``,
    and the walk continues only while budget remains. Any other ``StorageError``
    listing a subdirectory is recorded in ``skipped`` (charging nothing, since
    it failed before scanning); the same error listing ``parent`` propagates.
    Reaching an entry budget with work left, or scanning a folder that was only
    partially covered, ends the walk with ``truncated=True, reason="max_entries"``
    unless ``max_results`` already stopped it first.
    """
    # -- validate before touching the adapter ------------------------------
    if not isinstance(name_contains, str) or name_contains == "":
        raise ValueError("name_contains must be a non-empty str")
    _validate_int_in_range("max_results", max_results, 1, 100)
    _validate_int_in_range("max_entries", max_entries, 1, 100_000)
    _validate_int_in_range("max_depth", max_depth, 0, 16)
    if not isinstance(case_sensitive, bool):
        raise ValueError(
            f"case_sensitive must be a bool, got {type(case_sensitive).__name__}"
        )

    # -- hard capability requirement ---------------------------------------
    scan_fn = getattr(adapter, "scan_children", None)
    if not callable(scan_fn):
        raise ScanUnsupportedError(
            f"adapter {type(adapter).__name__!r} has no callable scan_children; "
            f"bounded search requires bounded raw scanning"
        )

    needle = name_contains if case_sensitive else name_contains.casefold()
    matches: list[FileMetadata] = []
    skipped: list[str] = []
    inspected = 0
    hit_max_depth = False

    # Queue holds (dir_ref, depth) for the current BFS level. Directories for
    # the next depth accumulate in next_level_dirs and are sorted by
    # (name, file_id) before the next level begins.
    current_level: list[tuple[FileRef, int]] = [(parent, 0)]

    while current_level:
        next_level_dirs: list[tuple[FileMetadata, int]] = []

        for idx, (dir_ref, depth) in enumerate(current_level):
            remaining_budget = max_entries - inspected
            if remaining_budget <= 0:
                # Budget exhausted with directories still to open.
                return FindResult(
                    items=tuple(matches),
                    truncated=True,
                    reason="max_entries",
                    inspected=inspected,
                    skipped=tuple(skipped),
                )

            try:
                batch = scan_fn(dir_ref, max_entries=remaining_budget)
            except ScanFailure as failure:
                # A scan that failed after doing raw work still charges it.
                inspected += _validate_failure_inspected(failure, remaining_budget)
                if depth == 0:
                    # The starting parent's own failure re-raises the original
                    # error object (and its preserved cause).
                    if not isinstance(failure.error, StorageError):
                        raise StorageError(
                            "ScanFailure.error must be a StorageError"
                        )
                    raise failure.error
                # A subdirectory that failed mid-scan is skipped; the walk only
                # continues while budget remains.
                skipped.append(dir_ref.file_id)
                continue
            except StorageError:
                if depth == 0:
                    # The starting parent's own errors propagate unchanged.
                    raise
                # A subdirectory that failed before scanning (or that vanished)
                # is skipped and charges no raw entries.
                skipped.append(dir_ref.file_id)
                continue

            items, batch_inspected, batch_complete = _validate_batch_report(
                batch, remaining_budget
            )
            # Charge the raw scan work before matching anything.
            inspected += batch_inspected
            children = sorted(items, key=lambda m: (m.name, m.ref.file_id))
            more_dirs_at_this_level = (idx + 1) < len(current_level)
            exact_last_entry = False

            for child_idx, entry in enumerate(children):
                if entry.kind == "directory":
                    if depth < max_depth:
                        next_level_dirs.append((entry, depth + 1))
                    else:
                        hit_max_depth = True

                haystack = (
                    entry.name if case_sensitive else entry.name.casefold()
                )
                if needle in haystack:
                    matches.append(entry)
                    if len(matches) == max_results:
                        more_entries_in_dir = (child_idx + 1) < len(children)
                        more_dirs_queued = (
                            more_dirs_at_this_level or bool(next_level_dirs)
                        )
                        if (
                            more_entries_in_dir
                            or not batch_complete
                            or more_dirs_queued
                        ):
                            return FindResult(
                                items=tuple(matches),
                                truncated=True,
                                reason="max_results",
                                inspected=inspected,
                                skipped=tuple(skipped),
                            )
                        # Exact match count on the last entry of a complete scan
                        # with no queued work: the walk is finished.
                        exact_last_entry = True
                        break

            if exact_last_entry:
                break

            if not batch_complete:
                # A partially scanned folder is truncation, even if it held no
                # matches: incomplete coverage is not "not found".
                return FindResult(
                    items=tuple(matches),
                    truncated=True,
                    reason="max_entries",
                    inspected=inspected,
                    skipped=tuple(skipped),
                )

        if len(matches) == max_results:
            break

        next_level_dirs.sort(key=lambda pair: (pair[0].name, pair[0].ref.file_id))
        current_level = [(meta.ref, d) for meta, d in next_level_dirs]

    if hit_max_depth:
        return FindResult(
            items=tuple(matches),
            truncated=True,
            reason="max_depth",
            inspected=inspected,
            skipped=tuple(skipped),
        )

    return FindResult(
        items=tuple(matches),
        truncated=False,
        reason=None,
        inspected=inspected,
        skipped=tuple(skipped),
    )
