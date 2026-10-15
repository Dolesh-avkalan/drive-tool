# MODULE-005A — partial bounded enumeration and honest search accounting

Sarvam, I need you to implement and test this focused module. Base your new branch `feature/module-005a-partial-scan` on exact main commit `356a4d8` (resolve the full SHA and report it). This includes the approved MODULE-005 merge a84f6b3. Preserve the existing write module and tests. Push the candidate and raw synthetic evidence through your connected GitHub tools; do not merge or deploy.

## Design authority and scope

I reviewed Argon d031 and adopt a separate partial-scan capability, not partial data attached to EnumerationLimitError. The precise rules below override d031's uncounted lookahead, metadata-only legacy fallback, and uncharged failed scans. Do not implement those three suggestions. D031's mixed-entry fixture totals eight entries, not seven; use correct deterministic fixtures.

Allowed source: drive_tool/storage.py, drive_tool/local.py, drive_tool/search.py. Allowed tests: existing enumeration/search/storage tests where semantics explicitly change, plus tests/test_partial_scan.py and tests/test_partial_scan_proofs.py. Evidence: docs/evidence/MODULE-005A.md and drop/drive-tool/module-005a/d032-implementation/. Do not edit local_write.py, read/stat containment, ListingService behavior, service implementation or session documents. Share enumeration helpers only if old list_children behavior and cleanup remain unchanged; avoid gratuitous refactors.

## Required contract

Add frozen `ScanBatch(items: tuple[FileMetadata, ...], inspected: int, complete: bool)` in storage.py, exported. Items are supported entries sorted by (name, file_id) within the scanned physical prefix only. Not a globally sorted directory prefix or snapshot.

Add optional `scan_children(parent: FileRef, *, max_entries: int) -> ScanBatch` to LocalAdapter and InMemoryAdapter. max_entries is mandatory, positive integer, bool rejected; keep it <=100000 if choosing a public maximum and document consistently. Do not add it as a required method to StorageAdapter: legacy adapters remain valid for existing stat/read/list. An optional protocol is acceptable, no framework.

Scan at most B successfully yielded raw entries and never ask the iterator for entry B+1. Increment inspected on every yielded entry before filtering or stat: invalid/reserved names, symlinks, writer temp names, FIFOs/special entries, ENOENT-disappearing entries all count. At most B metadata/stat attempts. Stop before next() once inspected == B. complete=True only when EOF was observed before exhaustion. For a local folder with exactly B entries, complete=False is deliberately conservative: EOF was not probed. Document that unknown EOF is not proof another entry exists. In-memory fixtures may report exact completeness from known length, but must not sort/materialize the entire fixture mapping just to take a bounded prefix; use a bounded insertion-order prefix, then sort that prefix. None/unbounded scans are not part of this new method.

Keep old list_children and ListingService unchanged: still full sorted children or EnumerationLimitError, including the existing exact-limit success behavior and legacy limit+1 witness. Its existing tests must continue to pass. Do not implement list_children by naively delegating to the strict partial scan and declaring exact-budget folders too large.

Preserve source mismatch/closed/argument precedence; no-follow anchored directory opens and root protection; immediate ownership tracking; one cleanup attempt per owned descriptor, no retry after ambiguous close; scandir iterator cleanup on partial return and errors; original error priority. No returns before cleanup succeeds.

### Failed-scan accounting

Add `ScanFailure(StorageError)` with `inspected: int` and `error: StorageError`, preserving the underlying error/cause. Once scan iteration has begun, storage errors from iteration, non-ENOENT child stat, iterator close, or descriptor cleanup must carry the number of successfully yielded raw entries in this exception. Discard the failed folder's partial items. Before any entry is yielded, ordinary storage errors may propagate unchanged (zero raw work). Never convert KeyboardInterrupt/SystemExit or arbitrary programming errors into a successful partial result; always clean up. Tests must exercise failures after a nonzero prefix and cleanup failures, not just errors before scan start.

### Search behavior

find requires callable scan_children. If absent, raise a distinct exported `ScanUnsupportedError(StorageError)` before attempting enumeration; do not silently fall back to list_children because supported-item counts do not bound raw scan work. All old public argument validation still occurs first. Document this explicit capability requirement; update fake tree adapters in tests to implement it. Do not weaken real operational tests to make them pass.

For each folder, pass remaining=max_entries-inspected; never call a scanner when remaining <=0. Charge the returned batch.inspected immediately, before matching. Therefore inspected measures raw scan work even when max_results stops processing early. Validate scanner reports: non-bool int 0..remaining, bool complete, len(items)<=inspected, and incomplete batch must exhaust remaining (unless a clear new reason is explicitly specified; do not silently accept no-progress batches). Reject invalid reports loudly with StorageError; no clamping that hides excess work or converts strings/bools. Do not consume an arbitrary unbounded iterable to validate it: ScanBatch.items is a tuple.

Retain matching entries from partial batches. Preserve BFS, depth bound, source behavior, case handling, fixed max_results/max_entries bounds, and skipped-subdirectory semantics. Within a partial directory, sorting is only within that bounded physical subset. After processing a partial batch return truncated=True, reason=max_entries unless max_results already stopped earlier, which takes precedence. Exact result count on the last item may only be complete when batch.complete and no queued/depth-limited work exists. Depth truncation remains explicit.

On ScanFailure, charge its inspected (validate bounds); starting-root failure re-raises the original error object/cause, subdirectory failure appends skipped and continues only if budget remains. Partial items from the failed folder are not returned. Ordinary pre-scan StorageError propagates at root or is skipped at depth>0 with zero inspected. No more scans after failed work consumes the remaining budget. Do not report complete search coverage when truncation or skipped directories indicate otherwise.

No cursor/state/index, API, background tasks, writes, or arbitrary filesystem access. Repeated identical bounded searches do not promise eventual complete coverage. Direct known-file reads remain independent.

## Acceptance evidence

Run the full combined suite on available Python 3.11 and 3.12 (plus any already-installed newer version); record exact environment, command, output, counts, skips, exit and commit/tree identities. Do not install interpreters. Explain each changed old assertion, especially inspected now charging full raw batches and conservative local exact-budget completion. Existing list_children and all write tests remain unchanged and passing.

Deterministic regressions must show early matching entries retained from an over-budget real/synthetic directory; zero-item partial scan stays truncated; mixed hidden/temp/symlink/FIFO/ENOENT entries charged; huge directories stop after exactly B yielded entries with NO B+1 next call; result limit still charges full scan; raw counts across multiple BFS folders; iteration/stat/iterator-close/fd-close failures after partial work charge correctly and preserve errors; no scans after exhausted failed-scan budget; malformed batch/capability denial; original list exact-limit and source/closed/validation precedence; repeated partial scans and failure paths leave no fd growth. Real fixture order should be controlled by a test seam where a particular prefix matters; do not assume filesystem alphabetical order.

Mutation checks in scratch copies: discard partial matches; count only metadata; read B+1 entries; set complete=True at a hard cap without EOF; ignore ScanFailure.inspected; scan a sibling after failed work exhausted the budget; leak iterator/fd on partial return. Each must be caught by a meaningful test. Record honest survivors, unsupported environments, corrections and PUSH_BLOCKED outcomes. Return a small summary and exact GitHub SHAs; no fabricated independent or target-cloud results.
