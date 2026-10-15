# MODULE-005B Evidence Report — Bounded Local Range Reads (Sarvam)

This report records the implementation, test execution and mutation check of
MODULE-005B (an optional `LocalAdapter.read_range` capability plus the frozen
`RangeReadResult` value object) as performed by Sarvam. It is the assembler's
own builder run; it makes **no independent-acceptance claim**.

## 1. Provenance

| Role | Detail |
| --- | --- |
| Base commit | `03a8250a5d02823812533290356cf375b7d2c33e` — "Record approved partial-scan integration" |
| Base tree | `3332be94c547e606a31456bb0deb5c6a5c8fbbc2` |
| Base commit parent | `0d9aa8f593e460e3e016114fcde0dc8fc7160951` (the approved MODULE-005A PR #4 merge, per `session/DECISIONS.md` D023) |
| Branch | `feature/module-005b-range-read` (created from the base commit) |
| Assembler | Sarvam |

The base commit was retrieved through the GitHub API; its tree and parent were
confirmed against the live repository. The parent chain confirms the base
contains the approved PR #4 merge (`0d9aa8f`), as the brief states.

## 2. Commits on `feature/module-005b-range-read`

| # | Commit | Message |
| --- | --- | --- |
| 1 | `2fe70a904e39d6f39b5648ffedbf10d6d9d35873` | Add LocalAdapter.read_range and RangeReadResult (MODULE-005B) |
| 2 | `433765cd5a6306a1a4612bec827397309714ca8a` | Add MODULE-005B read_range contract/boundary/cleanup tests |
| 3 | `ecda94e22f73f5082f06ed1200383181a2f0fee9` | Add MODULE-005B read_range adversarial proofs |
| 4 | see §8 | Add MODULE-005B evidence report |

Commit 1's parent is the base `03a8250`. Commits are created through the GitHub
content API (the sandbox holds no push credentials). `main` is not modified,
nothing is merged and nothing is deployed. The three source/test files were
re-fetched from the branch head `ecda94e` and their SHA-256 matched the local
candidate byte-for-byte (§8).

## 3. Scope of change

| Path | Change |
| --- | --- |
| `drive_tool/local.py` | Add `RangeReadResult` (frozen dataclass, exported in `__all__`); `LocalAdapter.read_range`; private `_read_open_file_range`, `_require_pread_capability`, `_os_pread` seam and module helper `_validate_range_options`; constants `_DEFAULT_RANGE_LENGTH` (262144) and `_RANGE_MAX_BOUND` (2**63-1); one `dataclasses` import. `read_bytes`, `stat`, `list_children`, `scan_children`, the write/listing/search modules, `StorageAdapter` and `__init__.py` are **unchanged**. |
| `tests/test_local_range.py` | New (62 tests). |
| `tests/test_local_range_proofs.py` | New (15 tests). |
| `docs/evidence/MODULE-005B.md` | This report. |

No `StorageAdapter` protocol change, in-memory implementation, service, OAuth,
streaming session, filesystem index, new dependency, deployment or
implementation merge. Python 3.11+ standard library only. The base-revision
`read_bytes` behaviour is byte-for-byte unchanged (the same resolver,
descriptor-ownership helpers and OS seams are reused; no second path resolver).

## 4. Public surface and validation order

`read_range(self, ref, *, offset=0, length=None) -> RangeReadResult`. The frozen
`RangeReadResult` fields are `data: bytes`, `offset: int`, `observed_size: int`,
`observed_mtime_ns: int`, `eof_observed: bool`; `len(data)` is the returned byte
count; no public filesystem path or descriptor is exposed.

Validation and I/O order inside `read_range` (documented because it fixes which
exception wins):

1. `_check_source_and_state(ref)` — `TypeError` / `SourceMismatchError` /
   `AdapterClosedError` (existing precedence, matching `read_bytes`).
2. `_parse_file_id(ref.file_id)` — `UnsafePathError` (ref checking, matching
   `read_bytes`).
3. synthetic-root rejection — `IsDirectoryError` (matching `read_bytes`).
4. `_validate_range_options(offset, length, self._max_read_bytes)` —
   `TypeError`/`ValueError`.
5. `_require_pread_capability()` — `UnsupportedPlatformError`.
6. `_traverse_parent_dir` → anchored no-follow open → single `fstat` →
   validate regular file → `pread` loop.

Steps 4–5 perform **no** filesystem syscall, so invalid options and a missing
`os.pread` fail before any open/traverse/stat/read for the operation. Ref
checking is kept in the same relative position as `read_bytes` (the brief
requires "same source/state/ref checking ... as `read_bytes`"), so a malformed
`file_id` is rejected before options are examined — this is the one ordering
judgement the brief leaves to the implementer, and it is stated here for review.
The constructor's positive byte cap is used through its actual internal name
`self._max_read_bytes`.

## 5. Execution environment and results

- Platform: Debian GNU/Linux 12 (bookworm), x86_64, non-root uid 1000.
- Interpreter A: **Python 3.11.2**
- Interpreter B: **Python 3.12.13**
- No 3.13/3.14 interpreter is installed; per the brief, none was installed and
  none is claimed.

Command (both interpreters, from the repository root):

```
python3.11 -m unittest discover -s tests -v
python3.12 -m unittest discover -s tests -v
```

| Interpreter | Count | Final line | Exit |
| --- | --- | --- | --- |
| Python 3.11.2 | `Ran 351 tests in 2.504s` | `OK` | 0 |
| Python 3.12.13 | `Ran 351 tests in 2.774s` | `OK` | 0 |

351 = the 274 pre-existing tests plus 77 new tests in the two `test_local_range*`
modules. **Zero tests are skipped** on this platform (the final line is plain
`OK`, not `OK (skipped=...)`).

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
| `test_partial_scan.py` | 37 |
| `test_partial_scan_proofs.py` | 13 |
| `test_partial_scan_review_fixes.py` | 18 |
| `test_local_range.py` (new) | 62 |
| `test_local_range_proofs.py` (new) | 15 |
| **Total** | **351** |

Every pre-existing test — including the full `read_bytes` containment/limit
suite — passes **unchanged**. No old assertion was deleted, weakened or skipped.

## 6. What the new tests cover

`tests/test_local_range.py` (contract, boundary, cleanup): `RangeReadResult`
export/frozen/fields; option type/bounds before any syscall (a `_SeamSpy`
asserts zero filesystem calls on invalid options), including bool/float/str
`TypeError`, negative/over-`2**63-1` `ValueError`, `length` range, and the
`offset+effective_length > 2**63-1` rule; source-mismatch/closed/malformed-id
precedence; default window (`min(262144, cap)`) and explicit windows; chunking
at ≤65536 with advancing offsets; empty/small/exact/over-window/beyond-EOF and
offset==size windows; a sparse 1 MiB file with a tiny high-offset read while
`read_bytes` still denies the whole read; short nonempty `pread`s and `EINTR`
retries; growth/truncation after the initial `fstat` with honest `eof_observed`;
source/state/ref and entry-class rejection; final/intermediate symlinks, a
renamed root replaced by an outside symlink, and an adversarial target swap;
FIFO rejection without blocking (bounded by a timeout thread); the
missing-`os.pread` gate with construction/`read_bytes` still working;
concurrent independent offsets; and open/fstat/read/close fault injection with
preserved `OSError` cause, primary-error priority over a simultaneous close
failure, parent-close handoff (child released exactly once) and
`/proc/self/fd` stability.

`tests/test_local_range_proofs.py` (adversarial proofs): total returned syscall
bytes never exceed the window and requested offsets never pass the window end;
no `pread` is issued after EOF is observed; the prefix is never read (first
`pread` starts at the requested offset); requests are never unbounded
(≤65536); the `read_bytes` seam (`_os_read`) is never invoked; one-byte chunks
still fill the whole window; an exactly-filled window is not EOF and an
offset beyond EOF is not rejected; the target open carries `O_NOFOLLOW` and not
`O_DIRECTORY`; a swapped symlink cannot escape; every acquired descriptor is
released exactly once across success and failure ops and `/proc/self/fd` is
stable over 50 iterations; each concurrent call opens its own descriptor;
growth after `fstat` is visible and EOF comes from `pread`, not metadata.

Observational syscall spies and controlled fault injection are used only to
*observe* real-kernel calls (they delegate to the real seams); the containment
tests exercise the real kernel and reuse the `tests/test_local.py` fixtures. No
real roots or credentials are touched; fixtures are tiny synthetic data (a
sparse 1 MiB logical file is used for the larger-than-cap case).

## 7. Mutation check

Each mutation was applied to an **isolated copy** of the candidate tree (never
the committed candidate), the full 351-test suite was run, and the copy was
discarded. Results are identical on Python 3.11.2 and 3.12.13. **7 mutations,
7 caught, 0 survivors.** Raw per-mutant logs are published with this report.

| # | Mutation (in `drive_tool/local.py`) | Caught by (count) |
| --- | --- | --- |
| M1 | read_range implemented as `read_bytes`-and-slice (whole read, then slice; also inherits whole-size rejection) | 25 tests incl. `test_read_range_never_uses_the_read_seam`, `test_tiny_high_offset_read_on_larger_than_cap_file`, `test_window_chunked_at_most_65536` |
| M2 | unbounded `pread` request (`request = 1 << 30`) | 18 tests incl. `test_request_never_unbounded`, `test_window_chunked_at_most_65536`, `test_total_returned_bytes_never_exceed_window` |
| M3 | early stop on a short nonempty read (`if len(chunk) < request: break`) | 9 tests incl. `test_one_byte_chunks_fill_the_whole_window`, `test_short_nonempty_reads_continue_to_fill_window` |
| M4 | EOF derived from `observed_size` (`eof_observed or offset+length >= observed_size`) | 3 tests: `test_exact_window_that_fills_request_is_not_eof`, `test_exact_fill_is_not_eof_even_though_size_matches`, `test_eof_not_derived_from_observed_size` |
| M5 | file open flags drop `O_NOFOLLOW` | 7 tests incl. `test_final_symlink_rejected`, `test_swapped_symlink_cannot_escape`, `test_target_open_uses_no_follow_flags` |
| M6a | `read_range` no longer releases its descriptors (`finally: pass`) | 5 tests incl. `test_success_and_failure_release_every_fd_once`, `test_linux_proc_self_fd_stable`, `test_no_fd_growth_across_success_and_failures` |
| M6b | cleanup close failure masks the primary read error | 2 tests: `test_primary_pread_error_wins_over_close_failure`, `test_primary_and_cleanup_errors_no_leak_no_retry` |

No mutation survives, so there is no mutation gap to report. The mutation runner
(`mutation_runner.py`, SHA-256
`e9e4ed5db6a671093a4fe0f21dca671621bf8afc6bfa97861040e4df5118e14b`) is published
with this report; mutants are never committed to the implementation.

## 8. Pushed identities and blob hashes

Branch head after the source/test commits: `ecda94e22f73f5082f06ed1200383181a2f0fee9`.

| Path | Blob SHA (GitHub) | SHA-256 (bytes) |
| --- | --- | --- |
| `drive_tool/local.py` | `74a45c61d3ea9c6e64005b1d92834fed17323d62` | `8d382347a4d1118440d364c693624de54883d517376779076c753672818ee2b7` |
| `tests/test_local_range.py` | `7fa3f80e97e84d8c8e7ff57c9cf2a950d5534fa9` | `59651dfac93992563d1e5205a6762f0c435adf3d0d176ec92209ab7ab38b4555` |
| `tests/test_local_range_proofs.py` | `d217e66b72a40de3bbc042f199db1ab25da341ba` | `63a7deb685f2761ec510ed895018e90f82166d6e44f0f55696bc2eca85ca81c7` |
| `docs/evidence/MODULE-005B.md` | this commit | see the drop manifest |

The three source/test blobs were re-fetched from the branch and their SHA-256
matched the local candidate. No `__pycache__`/`.pyc` file is tracked on the
branch.

## 9. Notes and limits

- This report claims only the builder execution performed here. There is no
  independent reviewer result and no target-cloud result in it; the branch is
  not merged and nothing is deployed.
- `eof_observed` is a per-call observation, never a snapshot/version/content-hash
  promise; repeated calls may see different file versions. `observed_size` and
  `observed_mtime_ns` are single `fstat` observations.
- `max_read_bytes` bounds the requested/returned bytes **per range call**; a
  larger total file is permitted. `read_bytes` still rejects whole files larger
  than its cap.
- Only Python 3.11.2 and 3.12.13 are present; newer interpreters are unsupported
  in this environment and were not installed or claimed.
- The capability requires a callable `os.pread`; on a platform without it,
  `read_range` raises `UnsupportedPlatformError` while construction and
  `read_bytes` continue to work.
- Infinite repeated `EINTR`/blocked kernel I/O is not hard-cancelled by this
  adapter; no global lock or hard syscall-deadline is claimed.
