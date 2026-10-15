Verdict: ACCEPTABLE DESIGN for MODULE-005A (drive_tool/storage.py, drive_tool/local.py, drive_tool/search.py and tests), with CHANGES REQUIRED to session/AGENT-INTERFACE.md to correct the stateless offset-pagination vs. raw-bounded os.scandir contradiction.

## 1. Conflict with session/AGENT-INTERFACE.md & Minimal Service Correction
### The Architectural Conflict

session/AGENT-INTERFACE.md currently makes three simultaneous claims:

1. Token budget: "a single more: <offset> hint when truncated."

2. Runtime shape: "Calls are stateless... continuation is a plain offset."

3. Concurrency and limits: "listing and find have entry budgets enforced inside the adapter while scanning (not after materializing the directory) and report truncation."

On a POSIX filesystem using stdlib os.scandir, these three properties cannot hold simultaneously for directories larger than the raw scan budget:

- Arbitrary physical order: os.scandir (readdir(3) / getdents64) yields entries in filesystem-internal order (e.g., ext4 htree hash order), not lexicographical order.

- No globally sorted prefix without full materialization: Sorting a bounded sample of $B$ raw entries produces an ordered view only of that arbitrary physical sample, not the first $B$ entries of the globally sorted directory.

- Stateless offset cannot advance past the raw scan bound: A stateless retry with offset = 20 re-opens os.scandir from the beginning of the directory stream and hits the exact same raw entry bound $B$. In a 200,000-entry directory with a 10,000-entry scan budget, stateless offset pagination can never inspect entries $10,001 \dots 200,000$, nor can it resume a breadth-first find walk after max_entries is exhausted.

### Minimal Correction to session/AGENT-INTERFACE.md

Do not introduce stateful cursors, telldir/seekdir cookies, or background indexing. Update session/AGENT-INTERFACE.md to distinguish result-window pagination from scan-budget truncation:

1. list remains all-or-nothing on the folder scan budget:

- When a directory fits within max_list_entries (default 10,000 raw entries), ListingService.list_page materializes and sorts the full directory and supports stateless more: <offset> pagination across the sorted entries.

- When a directory exceeds max_list_entries, list_children raises EnumerationLimitError, which the service maps to too_large with a remediation hint (e.g., too_large: p/logs exceeds scan limit (use find with name filter or read a known path)). list never silently turns an incomplete scan into a paginated list.

2. find reports bounded-scan truncation honestly and does not offer more: <offset> on scan exhaustion:

- find returns up to max_results (default 20, cap 100) matches found within max_entries raw directory entries and max_depth.

- Matches within any partially scanned directory are ordered by (name, file_id) within the scanned prefix only, with no claim of global directory sort order.

- When truncated=True:

- reason="max_results": caller may request a larger max_results (up to 100) or narrow name_contains / parent.

- reason="max_entries" or reason="max_depth": no more: <offset> hint is emitted; repeated identical calls scan the same bounded prefix. To inspect deeper or further, the agent must narrow parent (e.g., target a specific subdirectory p/a/b) or increase the bound within allowed caps.

3. Direct-by-reference operations remain $O(\text{depth})$ and independent of folder size:

- stat, read_bytes, and save resolve path components directly via openat(dir_fd, part, O_NOFOLLOW) without calling scandir. A known file inside a 200,000-entry directory is read or written in constant descriptor steps regardless of search truncation.

## 2. Smallest Explicit Adapter Capability: ScanBatch & scan_children
### Why ScanBatch + scan_children over Exception-Attached Partial Data

Attaching partial items to EnumerationLimitError (e.g., exc.partial_items) is rejected for three reasons:

1. Incomplete accounting on non-throwing scans: When a directory has 100 raw entries (50 regular files, 30 symlinks, 20 .drive-tool-tmp-* files) and max_entries=500, list_children succeeds without raising EnumerationLimitError and returns 50 FileMetadata items. An exception attribute never fires here, leaving find unable to charge the 50 filtered/omitted raw entries to its global max_entries budget. ScanBatch.inspected reports the true raw entry count on both complete and partial scans.

2. Separation of normal bounded control flow from failure: Hitting a scan budget during find is expected operational control flow, whereas list_children exceeding max_entries in ListingService is an aborted operation where allocating and sorting partial FileMetadata tuples is wasted work.

3. Clear error semantics: If os.scandir or entry.stat fails mid-directory with EIO or EACCES, that is a true LocalStorageError (the directory is added to find.skipped at depth > 0 or raised at depth == 0), not a clean bounded prefix. ScanBatch represents a clean scan; exceptions represent actual storage failures.

### Exact Signatures & Invariants (drive_tool/storage.py)

```python
@dataclass(frozen=True)
class ScanBatch:
    """The outcome of a bounded single-directory scan.

    Invariants:
    - ``items`` contains only supported child ``FileMetadata`` entries,
      sorted by ``(name, file_id)``.
    - ``0 <= len(items) <= inspected``
    - When ``max_entries`` is not ``None``, ``inspected <= max_entries``.
    - ``complete`` is ``True`` iff the directory stream reached EOF within
      ``max_entries`` raw entries; ``False`` iff at least one additional
      raw entry existed beyond the ``max_entries`` bound.
    """

    items: tuple[FileMetadata, ...]
    inspected: int
    complete: bool
```

### Capability Detection & Third-Party Adapter Compatibility

- StorageAdapter protocol remains unchanged: Keep stat, list_children, and read_bytes as the sole required methods on @runtime_checkable class StorageAdapter(Protocol). Existing third-party or test implementations of StorageAdapter continue to satisfy isinstance(adapter, StorageAdapter) without modification.

- Optional scan_children method:

```python
def scan_children(
    self, parent: FileRef, *, max_entries: int | None = None
) -> ScanBatch: ...
```

Implemented on both InMemoryAdapter and LocalAdapter.

- Detection in find: find looks up scan_fn = getattr(adapter, "scan_children", None).

- If callable(scan_fn), find calls scan_fn(dir_ref, max_entries=remaining_budget).

- Otherwise, find falls back to adapter.list_children(dir_ref, max_entries=remaining_budget):

- On success returning raw_children: charges min(len(raw_children), remaining_budget) and processes up to remaining_budget sorted entries (complete = len(raw_children) <= remaining_budget).

- On EnumerationLimitError: charges the full remaining_budget (inspected += remaining_budget) and terminates with truncated=True, reason="max_entries".

## 3. Exact Adapter Semantics & Boundary Rules
### InMemoryAdapter (drive_tool/storage.py)

1. scan_children(self, parent: FileRef, *, max_entries: int | None = None) -> ScanBatch:

- Validates self._check_source(parent) first, then _validate_max_entries(max_entries).

- If parent.file_id != ROOT_ID: raises NotFoundError if unknown, else NotDirectoryError.

- Let names = sorted(self._files) and total = len(names).

- Zero lookahead needed: total is known in $O(1)$.

- If max_entries is None or total <= max_entries:
returns ScanBatch(items=tuple(self._file_metadata(n) for n in names), inspected=total, complete=True).

- If total > max_entries:
returns ScanBatch(items=tuple(self._file_metadata(n) for n in names[:max_entries]), inspected=max_entries, complete=False).

2. list_children(self, parent: FileRef, *, max_entries: int | None = None) -> list[FileMetadata]:

- Calls batch = self.scan_children(parent, max_entries=max_entries).

- If not batch.complete: raises EnumerationLimitError(max_entries, ...).

- Returns list(batch.items).

### LocalAdapter (drive_tool/local.py)

1. Validation & Setup:

- Enforces _check_source_and_state(parent) (TypeError $\to$ SourceMismatchError $\to$ AdapterClosedError), then validates max_entries (None uses self._max_list_entries; bool, non-int, or < 1 raises ValueError), then _parse_file_id(parent.file_id).

2. Descriptor Ownership & Cleanup:

- Identical single-finally ownership model via owned: list[tuple[int, str]] = [] and self._release_owned(owned).

- parent_fd (when != root_fd) and scan_fd are appended to owned the instant they are opened.

- os.scandir(scan_fd) duplicates scan_fd internally via _Py_dup / fdopendir. Entering with scandir_it as entries: guarantees that exiting the with block—including via break on budget exhaustion or via exception—closes the duplicated DIR* descriptor immediately, while finally: self._release_owned(owned) closes scan_fd and parent_fd exactly once.

3. Boundary Semantics & Un-statted EOF Probe:

- POSIX readdir streams do not expose entry count up front; EOF is only observable when the iterator yields StopIteration.

- Inside with scandir_it as entries::

```python
children: list[FileMetadata] = []
entry_count = 0
complete = True
with scandir_it as entries:
    for entry in entries:
        if entry_count == effective_limit:
            # A (effective_limit + 1)-th dirent exists in the stream.
            # Do not inspect its name, do not call entry.stat(), and do
            # not include it in children.
            complete = False
            break
        entry_count += 1
        ...
```

- Why this exact boundary rule is required:

- Below limit ($N < B$): Loop processes $N$ entries, scandir hits StopIteration, returns ScanBatch(items=..., inspected=N, complete=True).

- Exact limit ($N = B$): Loop processes $B$ entries (entry_count == B), next step on entries hits StopIteration (EOF), returns ScanBatch(items=..., inspected=B, complete=True). A directory of size $B$ scanned with budget $B$ is not falsely reported as truncated.

- Over limit ($N > B$): After $B$ entries are counted and processed, the iterator yields the $(B+1)$-th DirEntry from the scandir buffer as a non-EOF witness. The guard if entry_count == effective_limit: immediately sets complete = False and breaks before incrementing entry_count, inspecting entry.name, or issuing an fstatat syscall (entry.stat(follow_symlinks=False)).

- Accounting: batch.inspected is B (effective_limit), never B + 1. At most $B$ entries ever have stat() called or FileMetadata built (0 <= len(batch.items) <= batch.inspected <= effective_limit).

4. Raw Entry Charging Inside the Budget ($1 \dots B$):
Every raw entry up to effective_limit increments entry_count (inspected) before filtering:

- Reserved/invalid names ("", ".", "..", containing "/", "\\", "\x00"): counted in inspected, omitted from items.

- Writer temp files (name.startswith(TEMP_PREFIX)): counted in inspected, omitted from items without calling entry.stat().

- Concurrent disappearance (entry.stat(follow_symlinks=False) raising FileNotFoundError or OSError with errno == errno.ENOENT): counted in inspected, omitted from items.

- Symlinks (stat.S_ISLNK): counted in inspected, omitted from items.

- Unsupported special files (FIFO, socket, device node): counted in inspected, omitted from items.

- Supported regular files and directories (S_ISREG, S_ISDIR): counted in inspected and appended to children.

5. Iteration / stat Failures:

- Any non-ENOENT OSError during entry.stat(follow_symlinks=False) or scandir iteration/close raises LocalStorageError (discarding partial items for that folder).

6. Sorting & Return:

- children.sort(key=lambda meta: meta.name)

- Returns ScanBatch(items=tuple(children), inspected=entry_count, complete=complete).

7. LocalAdapter.list_children Delegation:

- Calls batch = self.scan_children(parent, max_entries=max_entries).

- If not batch.complete: raises EnumerationLimitError(effective_limit, f"directory {parent.file_id!r} exceeds limit of {effective_limit} entries").

- Returns list(batch.items).

8. What Is Not a Snapshot:

- scan_children is a single forward pass over a live POSIX DIR* stream. Concurrent file creations, deletions, or renames during iteration may or may not be observed, and batch.items on a partial scan (complete=False) is sorted by name only within the arbitrary physical subset of inspected raw entries read before the bound was reached.

## 4. find Specification (drive_tool/search.py)
### Budget Charging, Traversal, and Precedence Rules

1. No scans after an exhausted global budget:
Before scanning each dir_ref in current_level, compute remaining_budget = max_entries - inspected. If remaining_budget <= 0, return immediately with truncated=True, reason="max_entries".

2. Charge scan work (batch_inspected) when the scan completes:

- Once scan_children(dir_ref, max_entries=remaining_budget) returns batch, clamp defensively against buggy fakes:

- raw_children = list(batch.items)

- If len(raw_children) > remaining_budget: slice raw_children = sorted(raw_children, key=lambda m: (m.name, m.ref.file_id))[:remaining_budget] and force batch_complete = False.

- batch_inspected = min(remaining_budget, max(int(batch.inspected), len(raw_children)))

- batch_complete = bool(batch.complete) and (len(batch.items) <= remaining_budget) and (batch.inspected <= remaining_budget)

- Immediately add inspected += batch_inspected.

- Scan work vs. processed matches: Because the adapter must finish its bounded scan of dir_ref and sort batch.items by name before find can evaluate matches in walk order, inspected reflects the total raw directory entries scanned by the adapter, even if find stops early mid-folder due to max_results. Thus inspected can be larger than len(items) or the number of FileMetadata items checked for substring matches. Update FindResult and find docstrings to state this explicitly.

3. Subdirectory queueing & max_depth:

- Sort children = sorted(raw_children, key=lambda m: (m.name, m.ref.file_id)).

- For each entry in children:

- If entry.kind == "directory":

- If depth < max_depth: append (entry, depth + 1) to next_level_dirs.

- Else: set hit_max_depth = True.

4. Match evaluation & max_results stopping check:

- If needle in haystack: append entry to matches.

- When len(matches) == max_results:

- more_entries_in_dir = (child_idx + 1) < len(children)

- more_dirs_queued = more_dirs_at_this_level or bool(next_level_dirs)

- If more_entries_in_dir or not batch_complete or more_dirs_queued:
return FindResult(items=tuple(matches), truncated=True, reason="max_results", inspected=inspected, skipped=tuple(skipped))

- Otherwise (exact match count on the final entry of a complete scan with no remaining directories at this or the next level), break out of the loop (allowing if hit_max_depth: to report reason="max_depth" if an earlier directory hit max_depth, or truncated=False, reason=None if the entire tree was exhausted).

5. Partial folder completion after scanning children (len(matches) < max_results):

- After iterating children of dir_ref, if not batch_complete:
return FindResult(items=tuple(matches), truncated=True, reason="max_entries", inspected=inspected, skipped=tuple(skipped))

- Zero-result partial scan: If matches is empty and not batch_complete, this returns items=(), truncated=True, reason="max_entries", inspected=inspected—never truncated=False.

6. Error precedence & skipped:

- Argument validation errors (ValueError) raise before any adapter call.

- At depth == 0, any StorageError (other than EnumerationLimitError on the legacy fallback path) propagates unchanged.

- At depth >= 1, any StorageError (other than EnumerationLimitError on the legacy fallback path) appends dir_ref.file_id to skipped without charging child entries beyond the 1 entry already charged when the subdirectory itself was discovered in its parent.

## 5. Allowed File Scope for MODULE-005A

Keep implementation narrow and stdlib-only. Do not modify write paths (LocalWriter or write tests from MODULE-005).

| File | Allowed Changes |
| --- | --- |
| drive_tool/storage.py | Add ScanBatch to __all__ and module; add InMemoryAdapter.scan_children; refactor InMemoryAdapter.list_children to delegate to scan_children. Keep StorageAdapter protocol methods unchanged. |
| drive_tool/local.py | Add LocalAdapter.scan_children; refactor LocalAdapter.list_children to delegate to scan_children. Keep all stat, read_bytes, and MODULE-005 write code untouched. |
| drive_tool/search.py | Update find to use scan_children (with list_children fallback for third-party StorageAdapters), charge batch.inspected, retain matches from partial batches, and update docstrings on FindResult and find. |
| session/AGENT-INTERFACE.md | Clarify list (too_large on scan limit; more: <offset> only for complete sorted listings) vs. find (bounded prefix search; no stateless offset continuation on max_entries/max_depth). |
| tests/test_storage.py, tests/test_local.py, tests/test_search.py | Add unit, boundary, descriptor-leak, and mutation/adversarial tests for ScanBatch, scan_children, and bounded find. |

## 6. Compatibility Changes & Unresolved Decisions
### Compatibility Changes from MODULE-004

1. find on over-budget directories now returns partial matches and charges the scan:

- Before (MODULE-004): When a folder exceeded remaining_budget, list_children raised EnumerationLimitError, find discarded all matches from that folder, and inspected did not include the aborted scan.

- After (MODULE-005A): find retains matches found within the remaining_budget raw entries of that folder and adds batch.inspected (== remaining_budget) to FindResult.inspected. Any existing test in tests/test_search.py that asserted items == () or inspected == 0 when a directory with matching entries hit max_entries must be updated to assert the retained matches and inspected == max_entries.

2. find.inspected charges the full bounded folder scan before max_results early exit:

- Before (MODULE-004): inspected incremented per FileMetadata inside the Python loop in find, so stopping at max_results=1 in a 10-file directory reported inspected=1, and symlinks/temp files were never counted.

- After (MODULE-005A): inspected charges batch.inspected from scan_children (so scanning a 10-entry directory with max_entries=5000 and stopping at max_results=1 reports inspected=10). Any existing tests/test_search.py assertion that expected inspected == max_results inside a larger directory must be updated to expect the directory's raw batch.inspected.

3. list_children and ListingService.list_page behavior is 100% unchanged.

### Unresolved Decisions

- None blocking MODULE-005A. If a future milestone requires paginating directories with $> 10,000$ entries via list, that must be an explicit configuration raise of max_list_entries rather than a silent partial list.

## 7. Focused Adversarial & Mutation Acceptance Tests

1. Exact-budget vs. Over-budget Boundary (Kills < vs <= and complete flag mutants):

- Directory with exactly $N = 5$ regular files, scan_children(root, max_entries=5) $\to$ complete=True, inspected=5, len(items)=5. list_children(root, max_entries=5) succeeds; find(..., max_entries=5) returns truncated=False, reason=None, inspected=5.

- Directory with $N = 6$ regular files, scan_children(root, max_entries=5) $\to$ complete=False, inspected=5, len(items)=5. Verify via spy on entry.stat / _os_scandir that the 6th DirEntry is never .stat()ed. list_children(root, max_entries=5) raises EnumerationLimitError(5).

2. Early Match Retention in Truncated Folder (Core Defect Regression):

- Directory with 50 files where matching file needle.txt is yielded within the first 5 raw scandir entries and max_entries=10: find returns needle.txt in items, truncated=True, reason="max_entries", inspected=10.

3. Zero-Result Partial Scan Is Truncation, Not Not-Found:

- Directory with 20 non-matching files (or 20 .drive-tool-tmp-* / symlink entries), max_entries=5: scan_children returns ScanBatch(items=(), inspected=5, complete=False), and find returns FindResult(items=(), truncated=True, reason="max_entries", inspected=5, skipped=()).

4. Raw Budget Charging for Filtered/Unsupported/Disappeared Entries:

- Directory containing 2 symlinks, 2 .drive-tool-tmp-xyz files, 1 FIFO, 1 entry that vanishes with ENOENT on entry.stat(), and 1 matching regular file (7 raw entries total), plus a non-empty subdirectory sub:

- With max_entries=7: scan_children(root, max_entries=7) returns len(items)=2 (the regular file + sub), inspected=7, complete=True. find then sees remaining_budget == 0 before opening sub and stops with truncated=True, reason="max_entries", inspected=7 without calling scan_children(sub).

5. No Lookahead / No Scan After Budget Exhaustion:

- Spy on adapter.scan_children: when root consumes all max_entries (inspected == max_entries) and queues sub1, scan_children is called once (for root) and never called for sub1.

6. Precedence When max_results and not batch.complete Coincide:

- Directory of 10 raw entries scanned with max_entries=5, max_results=2:

- When 2 matches occur at indices 0 and 4 of the 5-item partial batch (more_entries_in_dir == False, batch.complete == False), find still returns truncated=True, reason="max_results", inspected=5 (never truncated=False).

7. Legacy / Third-Party StorageAdapter Fallback (No scan_children Method):

- Pass a minimal custom adapter implementing only stat, list_children, and read_bytes to find:

- When list_children raises EnumerationLimitError, find charges remaining_budget to inspected and returns truncated=True, reason="max_entries".

- When a buggy fake returns len(items) > remaining_budget, find caps processed items and inspected at max_entries.

8. Descriptor & scandir Iterator Cleanup Under Partial Scans and Errors:

- Run repeated partial scan_children(root, max_entries=3) calls on a 100-entry directory and verify open file descriptor count (/proc/self/fd or _os_open/_os_close + scandir_it.__exit__ spy) is strictly flat, confirming break inside with scandir_it as entries: closes both the duplicated DIR* fd and scan_fd.