# MODULE-005A Evidence Report — Partial Bounded Enumeration and Honest Search Accounting (Sarvam)

This report records the implementation, test execution and mutation check of
MODULE-005A (a separate partial-scan capability plus honest raw-entry accounting
in `find`) as performed by Sarvam. It is separate from the Argon design review
(`Dolesh-avkalan/drop`, `drive-tool/module-005a/d032-implementation/`).

## 1. Provenance

| Role | Detail |
| --- | --- |
| Base commit | `356a4d8a71d0e57a57e7c82dbfce50be145a0bdb` — "Resume after Claude handoff, record write merge and partial-scan design task" |
| Base tree | `1d92bfccc8c2b9bb162a6fc0cfac2b0423273952` |
| Base commit parent | `a84f6b3328d2caeb8d71f7c009e370dfea9cf452` (the approved MODULE-005 merge) |
| Branch | `feature/module-005a-partial-scan` (created from the base commit) |
| Assembler | Sarvam |

The base commit was resolved from the short id `356a4d8`; its full SHA is
`356a4d8a71d0e57a57e7c82dbfce50be145a0bdb`. `a84f6b3...356a4d8` compares
`ahead_by: 1`, confirming the base contains the MODULE-005 merge `a84f6b3`.

## 2. Commit on `feature/module-005a-partial-scan`

| # | Commit | Message |
| --- | --- | --- |
| 1 | see §8 | Implement MODULE-005A partial bounded scan and honest search accounting (Sarvam) |
| 2 | see §8 | Add MODULE-005A evidence report (Sarvam) |

Commit 1's parent is the base `356a4d8`. Commit 2 adds this file. The commits
are created through the GitHub Git Data API (the sandbox holds no push
credentials); the API stores the commit message without a trailing newline, so
the pushed SHAs are the ones reported in §8. `main` is not modified, nothing is
merged and nothing is deployed.

## 3. Scope of change

| Path | Change |
| --- | --- |
| `drive_tool/storage.py` | Add frozen `ScanBatch`, `ScanFailure(StorageError)`, `ScanUnsupportedError(StorageError)`, `MAX_SCAN_ENTRIES`, `_validate_scan_max_entries`; add `InMemoryAdapter.scan_children`. `StorageAdapter` protocol unchanged. |
| `drive_tool/local.py` | Add `LocalAdapter.scan_children` (no-lookahead, bounded, single-finally descriptor ownership) and the `_scan_work_error` helper. `stat`, `read_bytes`, `list_children` and the write code are untouched. |
| `drive_tool/search.py` | `find` requires a callable `scan_children` (else `ScanUnsupportedError`), charges each batch's `inspected` before matching, retains partial-folder matches, validates scanner reports loudly, and accounts `ScanFailure`. |
| `tests/test_search.py` | Add `scan_children` to the fake adapter; update the assertions whose semantics changed (see §5). |
| `tests/test_partial_scan.py` | New (37 tests). |
| `tests/test_partial_scan_proofs.py` | New (13 tests). |
| `docs/evidence/MODULE-005A.md` | This report. |

`drive_tool/local_write.py`, `drive_tool/listing.py`, `drive_tool/__init__.py`,
the read/stat containment paths and every `session/` document are **not**
changed. Per the brief's allowed scope, `session/AGENT-INTERFACE.md` is
deliberately left untouched: the design verdict's requested correction there is
a director action, and this task forbids editing session documents. `find` does
report bounded-scan truncation honestly (`reason="max_entries"` / `"max_depth"`
with no offset continuation), which is the behaviour that correction describes.

## 4. Execution environment and results

- Platform: Debian GNU/Linux 12 (bookworm), x86_64, non-root uid 1000 (gVisor sandbox).
- Interpreter A: **Python 3.11.2**
- Interpreter B: **Python 3.12.13**
- No newer interpreter (3.13/3.14) is installed; per the brief, none was installed.

Command (both interpreters, from the repository root):

```
python3.11 -m unittest discover -s tests -v
python3.12 -m unittest discover -s tests -v
```

| Interpreter | Count | Final line | Exit |
| --- | --- | --- | --- |
| Python 3.11.2 | `Ran 256 tests in 2.092s` | `OK` | 0 |
| Python 3.12.13 | `Ran 256 tests in 2.113s` | `OK` | 0 |

256 = the 204 pre-existing tests (of which `test_search.py` grew by 2 from one
split test) plus 50 new tests in the two `test_partial_scan*` modules. No test
is skipped on this platform.

| Test module | Tests |
| --- | --- |
| `test_storage.py` | 36 |
| `test_listing.py` | 35 |
| `test_local.py` | 53 |
| `test_search.py` | 16 |
| `test_enumeration.py` | 15 |
| `test_enumeration_proofs.py` | 6 |
| `test_readonly_core.py` | 16 |
| `test_local_write.py` | 24 |
| `test_local_write_proofs.py` | 5 |
| `test_partial_scan.py` (new) | 37 |
| `test_partial_scan_proofs.py` (new) | 13 |
| **Total** | **256** |

Every pre-existing `list_children` test, the whole MODULE-005 write suite
(`test_local_write.py` 24, `test_local_write_proofs.py` 5) and the containment
suite pass **unchanged**.

## 5. Changed old assertions (and why)

Semantics changed in three places, so three old assertions had to change. No
assertion was deleted, weakened or skipped.

1. **`test_search.py::test_max_results_truncation_vs_exact_last_entry`** —
   `inspected` for the `max_results=2` case changed `2 → 3`. `find` now charges
   the adapter's full bounded scan of the 3-entry root (`batch.inspected == 3`)
   *before* matching, so `inspected` counts raw scan work, not returned matches.
   This is the brief's "result limit still charges full scan" rule.

2. **`test_search.py::test_max_entries_exact_budget_vs_dirs_remaining`** — split
   into three tests because a local folder holding **exactly** the budget is now
   deliberately **incomplete**:
   - `test_max_entries_budget_covering_whole_tree_is_complete`: budget strictly
     greater than the folder size (3 > 2) still observes EOF and is complete
     (`truncated=False`, `inspected=2`).
   - `test_local_exact_budget_is_conservatively_incomplete` (**changed**): a
     2-entry folder with `max_entries=2` now reports `truncated=True,
     reason="max_entries", inspected=2` (was `truncated=False`). Entry `B+1` is
     never probed, so EOF is unknown and "unknown" is not proof of completion.
   - `test_max_entries_exhausted_with_dirs_remaining`: unchanged in substance
     (`truncated=True`, `reason="max_entries"`, `inspected=2`).

3. **`test_search.py::test_large_folder_stops_early_via_adapter_limit`** —
   `inspected` changed `0 → 100`, and the needle changed `"x" → "zzz"`. The old
   `inspected == 0` recorded the MODULE-004 defect (an aborted scan was not
   charged); the bounded scan is now charged in full. The needle change is a
   fixture correction: every `f<i>.txt` contains `"x"` (in `.txt`), so with the
   new "retain partial matches" rule the old needle would stop at
   `max_results`, not at the scan budget; `"zzz"` keeps the test about
   scan-budget truncation, which is what its name asserts.

4. **`test_search.py::test_defensive_guard_against_adapter_ignoring_budget`** —
   renamed `test_over_budget_report_is_rejected_loudly` and now expects a
   `StorageError`. The brief replaces the old silent clamping with loud
   rejection of an over-budget scanner report.

5. **`test_search.py::test_subdirectory_enumeration_limit_stops_not_skipped`** —
   renamed `test_subdirectory_partial_scan_truncates_not_skipped`. The existing
   assertions are preserved; the trigger is now an incomplete batch (partial
   scan) rather than `EnumerationLimitError`, and `inspected == 10` is asserted
   explicitly.

6. **`test_search.py::FakeAdapter`** — gained a `scan_children` method, because
   `find` now hard-requires it. The real operational tests were not weakened:
   the fake returns a bounded prefix and an incomplete flag, and the
   over-budget behaviour is exercised by the rejection test above.

## 6. What the new tests cover

`tests/test_partial_scan.py` (contract, boundary, cleanup): `ScanBatch`
invariants; the in-memory bounded **insertion-order** prefix (not the global
smallest names); exact-budget behaviour (in-memory exact, local conservative);
mandatory/validated budget (`bool`/`None`/range rejected, missing kwarg is
`TypeError`); source/closed/validation precedence; mixed hidden/temp/symlink/
FIFO/ENOENT entries charged; failed-scan accounting (`ScanFailure` with the
right `inspected` and preserved cause for stat, iteration, iterator-close and
descriptor-cleanup failures, and a plain error before any entry is yielded);
`/proc/self/fd` flatness on repeated partial and failing scans; and loud batch
validation (non-tuple items, bool `inspected`, out-of-range `inspected`,
non-bool `complete`, items longer than `inspected`, an incomplete batch that
does not exhaust the budget, out-of-range `ScanFailure.inspected`), plus
capability denial and root-`ScanFailure` re-raise.

`tests/test_partial_scan_proofs.py` (adversarial proofs): a 200-entry directory
stops after exactly `B` yielded entries with **no `B+1` next call** and at most
`B` stat calls; an early match is retained from an over-budget directory; a
zero-item partial scan is truncation, not "not found"; the result limit still
charges the full bounded scan (whole folder and partial folder); raw counts are
summed across BFS folders; matches are ordered within the scanned prefix only;
the iterator-close failure charges the prefix; the old `list_children`
exact-limit success and limit+1 witness are unchanged; and repeated partial and
failing scans leave no fd growth.

Physical entry order is controlled by a deterministic test seam
(`OrderedScanAdapter`, which scripts scandir order, counts `next()`/`stat`
calls and injects failures); no test assumes filesystem alphabetical order.

## 7. Mutation check

Each mutation was applied to a scratch copy of the candidate (never committed),
the full suite was run, and the copy was discarded. Results are identical on
Python 3.11.2 and 3.12.13. **7 mutations, 7 caught, 0 survivors.**

| # | Mutation | Caught by |
| --- | --- | --- |
| M1 | discard partial matches (`search.py`, incomplete-batch return `items=()`) | `test_early_match_retained_from_over_budget_directory`, `test_matches_ordered_within_scanned_prefix_only`, `test_local_exact_budget_is_conservatively_incomplete` |
| M2 | count only metadata (`local.py`, `inspected=len(children)`) | `test_counts_all_raw_and_returns_only_supported`, `test_mixed_hidden_temp_symlink_fifo_all_charged`, `test_vanishing_entries_are_charged_but_omitted` |
| M3 | read `B+1` entries (`local.py`, `while entry_count <= max_entries`) | 11 tests incl. `test_exact_budget_is_incomplete_and_never_probes_b_plus_1`, `test_huge_directory_stops_after_exactly_b_no_b_plus_1` |
| M4 | `complete=True` at a hard cap without EOF (`local.py`, `complete = True`) | 8 tests incl. `test_exact_budget_is_incomplete_and_never_probes_b_plus_1`, `test_over_budget_stops_at_b_without_b_plus_1` |
| M5 | ignore `ScanFailure.inspected` (`search.py`, drop the `inspected +=`) | `test_subdir_scanfailure_is_skipped_and_charged`, `test_no_scans_after_failed_work_exhausts_budget` |
| M6 | scan a sibling after failed work exhausted the budget (`search.py`, `remaining_budget < 0`) | `test_no_scans_after_failed_work_exhausts_budget` |
| M7 | leak iterator/fd on partial return (`local.py`, skip `_release_owned` in the `finally`) | `test_descriptor_cleanup_failure_is_scanfailure`, `test_no_fd_growth_on_partial_and_failing_scans`, `test_repeated_partial_and_failing_scans_no_fd_growth` |

No mutation survives, so there is no mutation gap to report.

## 8. Pushed identities and blob hashes

| Path | Blob SHA |
| --- | --- |
| `drive_tool/storage.py` | `3f4f8dcf50bbf68e97cd807fe40db1ee1bec4576` |
| `drive_tool/local.py` | `d2d03ac02ede36a1bcbafab687a139d446491fcf` |
| `drive_tool/search.py` | `5a19344114f08dbee2adbfda2dd64d312fc8df5e` |
| `tests/test_search.py` | `6c31aed4ae8e84157efb3054131c4040a1fa6008` |
| `tests/test_partial_scan.py` | `0939ee77d379c994b9966d478cf94b136dc398e8` |
| `tests/test_partial_scan_proofs.py` | `51c990bc26661d98498086371be47cd08764be56` |

The branch commit SHAs are reported in the assembler's reply (the Git Data API
records the message without a trailing newline). No `__pycache__`/`.pyc` file
is tracked on the branch.

## 9. Notes and limits

- This report claims only execution performed here. There is no independent
  reviewer result and no target-cloud result in it.
- The base commit `356a4d8` is used exactly as instructed; the branch is not
  merged and nothing is deployed.
- Only Python 3.11.2 and 3.12.13 are present; newer interpreters are
  unsupported in this environment and were not installed.
- `scan_children` is optional on an adapter but its `max_entries` argument is
  mandatory; the public ceiling is `MAX_SCAN_ENTRIES = 100000`, matching
  `find`'s `max_entries` cap. Repeated identical bounded searches make no
  promise of eventual complete coverage.
