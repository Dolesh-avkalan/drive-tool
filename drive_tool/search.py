"""Bounded breadth-first name search over the storage contract.

MODULE-004: :func:`find` walks a storage tree breadth-first, level by level,
and returns matching files and directories while enforcing an entry budget, a
result budget, and a depth bound. It never blocks on a large folder: each
``list_children`` call receives only the remaining entry budget, so a huge
directory is stopped early with ``truncated=True`` rather than fully read.

Python 3.11+, standard library only. No threads, no retries, no sleeps.
"""

from __future__ import annotations

from dataclasses import dataclass

from drive_tool.storage import (
    EnumerationLimitError,
    FileMetadata,
    FileRef,
    StorageAdapter,
    StorageError,
)

__all__ = ["FindResult", "find"]

@dataclass(frozen=True)
class FindResult:
    """The outcome of a bounded :func:`find`.

    ``items`` are the matches in walk order, ``truncated`` is ``True`` when a
    bound stopped the walk (``reason`` names it), ``inspected`` counts the
    entries the adapter returned and the walk examined, and ``skipped`` holds
    the ``file_id`` of every directory that could not be listed.
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
    ``case_sensitive``), and both files and directories can match. All
    arguments are validated before the adapter is called. A non-limit
    ``StorageError`` listing a subdirectory adds its ``file_id`` to
    ``skipped`` and the walk continues; the same error listing ``parent``
    propagates. An :class:`EnumerationLimitError` at any depth, or a reached
    entry budget with directories left, ends the walk with
    ``truncated=True, reason="max_entries"``.
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
                raw_children = adapter.list_children(
                    dir_ref, max_entries=remaining_budget
                )
            except EnumerationLimitError:
                # Explicit catch before StorageError (it is a subclass):
                # an over-limit directory stops the walk at any depth.
                return FindResult(
                    items=tuple(matches),
                    truncated=True,
                    reason="max_entries",
                    inspected=inspected,
                    skipped=tuple(skipped),
                )
            except StorageError:
                if depth == 0:
                    # The starting parent's own errors propagate.
                    raise
                # A subdirectory that vanished or became unreadable is skipped.
                skipped.append(dir_ref.file_id)
                continue

            children = sorted(
                raw_children, key=lambda m: (m.name, m.ref.file_id)
            )
            more_dirs_at_this_level = (idx + 1) < len(current_level)

            for child_idx, entry in enumerate(children):
                # Defensive guard: a fake adapter that ignores its budget must
                # not let us overshoot the entry limit.
                if inspected >= max_entries:
                    return FindResult(
                        items=tuple(matches),
                        truncated=True,
                        reason="max_entries",
                        inspected=inspected,
                        skipped=tuple(skipped),
                    )

                inspected += 1
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
                        if more_entries_in_dir or more_dirs_queued:
                            return FindResult(
                                items=tuple(matches),
                                truncated=True,
                                reason="max_results",
                                inspected=inspected,
                                skipped=tuple(skipped),
                            )
                        # No more work anywhere: fall through and finish.
                        break

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
