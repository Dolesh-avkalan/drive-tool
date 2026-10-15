# MODULE-004 Evidence Report — Assembly, Execution and Provenance (Sarvam)

This report records the assembly, test execution and provenance of MODULE-004
(bounded enumeration and bounded find) as performed by the assembler (Sarvam).
It is separate from the implementer's own output
(`Dolesh-avkalan/drop`, `drive-tool/module-004/d020-deepseek-implementation.md`).

## 1. Provenance

| Role | Detail |
| --- | --- |
| Implementer | DeepSeek (deepthink on, search off) |
| Implementer output | `Dolesh-avkalan/drop`, branch `main`, `drive-tool/module-004/d020-deepseek-implementation.md` (status: `GENERATED, TESTS_NOT_RUN`) |
| Brief | `Dolesh-avkalan/drive-tool`, `tasks/MODULE-004.md` @ `main` |
| Base commit | `ca78950893658ed0d75965f4900e45313306d4fa` ("MODULE-004 brief: bounded enumeration and bounded find; dispatch recorded") |
| Branch | `feature/module-004-bounded-enumeration` (created from `main`) |
| Assembler | Sarvam |

The brief text names the base as "main at `09a5928`"; `main` had advanced by one
documentation commit (the brief's own dispatch, `ca78950`) before this assembly,
so the branch was created from the actual `main` HEAD `ca78950`, which contains
`09a5928`.

The implementer delivered every required block in the brief's format — five
`FILE:` blocks and three `REPLACE IN drive_tool/local.py:` blocks — with no
ellipses or placeholders, so assembly proceeded.

## 2. Commits on `feature/module-004-bounded-enumeration`

1. **Implement MODULE-004 bounded enumeration and find (DeepSeek)** —
   `790ca3fd637e08149112cecd8c5fac117bac4b8d`.
   Applies DeepSeek's output exactly:
   - full files written: `drive_tool/storage.py`, `drive_tool/listing.py`,
     `drive_tool/search.py`, `tests/test_enumeration.py`,
     `tests/test_search.py`;
   - three whole-definition replacements in `drive_tool/local.py`: the
     `from drive_tool.storage import (...)` import block, `LocalAdapter.__init__`,
     and `LocalAdapter.list_children`.

   No other file was changed. `drive_tool/__init__.py` was not touched (as the
   brief requires).

2. **Add MODULE-004 evidence report** — this file.

No fix commit was needed; the suite passed as delivered on every available
Python 3.11+ interpreter (§4). No test was modified, skipped, or deleted.

## 3. Execution environment

- Platform: Debian GNU/Linux 12 (bookworm), x86_64, gVisor sandbox, non-root
  uid 1000.
- Interpreter A: **Python 3.11.2**
- Interpreter B: **Python 3.12.13**
- Command (both interpreters, run from the repository root):

  ```
  python3.11 -m unittest discover -s tests -v
  python3.12 -m unittest discover -s tests -v
  ```

These are the two Python 3.11+ interpreters present in the environment.

## 4. Test results

| Interpreter | Command | Count | Final line |
| --- | --- | --- | --- |
| Python 3.11.2 | `python3.11 -m unittest discover -s tests -v` | `Ran 169 tests in 1.054s` | `OK` |
| Python 3.12.13 | `python3.12 -m unittest discover -s tests -v` | `Ran 169 tests in 1.051s` | `OK` |

All 169 tests pass on both interpreters. Exit code `0` in both runs. No
failures, no errors, no skips.

The 169 tests comprise 29 new tests for this module
(`tests/test_enumeration.py`: 15; `tests/test_search.py`: 14) plus the 140
pre-existing tests, which pass unchanged:

| Test module | Tests |
| --- | --- |
| `test_enumeration.py` (new) | 15 |
| `test_search.py` (new) | 14 |
| `test_listing.py` | 35 |
| `test_local.py` | 53 |
| `test_readonly_core.py` | 16 |
| `test_storage.py` | 36 |
| **Total** | **169** |

### Raw output tail (Python 3.11.2)

```
test_binary_file_size (test_storage.TestStat.test_binary_file_size) ... ok
test_empty_file_size (test_storage.TestStat.test_empty_file_size) ... ok
test_file_stat_values (test_storage.TestStat.test_file_stat_values) ... ok
test_root_stat_values (test_storage.TestStat.test_root_stat_values) ... ok

----------------------------------------------------------------------
Ran 169 tests in 1.054s

OK
```

### Raw output tail (Python 3.12.13)

```
test_binary_file_size (test_storage.TestStat.test_binary_file_size) ... ok
test_empty_file_size (test_storage.TestStat.test_empty_file_size) ... ok
test_file_stat_values (test_storage.TestStat.test_file_stat_values) ... ok
test_root_stat_values (test_storage.TestStat.test_root_stat_values) ... ok

----------------------------------------------------------------------
Ran 169 tests in 1.051s

OK
```

## 5. Fixes

**None.** The suite passed on the first run on both interpreters, so no
"Sarvam fixes" commit exists and no source or test file was altered after
applying DeepSeek's output.

## 6. Verification of what was pushed

The six files committed in commit 1 were read from the same working tree the
suite ran against, and their git blob hashes match the blobs recorded by the
GitHub commit API exactly:

| Path | Blob SHA |
| --- | --- |
| `drive_tool/storage.py` | `7d53356e510a0e53e10a22e4498aff5699f5bc96` |
| `drive_tool/listing.py` | `28dddd549c068b00b149a16ad9820445b8af7946` |
| `drive_tool/local.py` | `ee213b135cd22aa167ec1de18700ac7a588e8cf4` |
| `drive_tool/search.py` | `3833397d895e8116b3363e07214ebc2f655638e2` |
| `tests/test_enumeration.py` | `f7e1eef00de1d82fca7001b64a6af9cfc6d1e667` |
| `tests/test_search.py` | `18c95cae98e80ffe1ed6bf8426823a3d2d833aa8` |

## 7. Notes and limits

- DeepSeek's delivered files use single blank lines between top-level
  definitions (the repository's existing files use two). This is cosmetic, part
  of the implementer's output, and was applied exactly as delivered rather than
  reformatted.
- Independent code review (Gemini, d019) and independent director execution at
  the pushed commit remain the acceptance gates, per the brief. This report
  claims only what was actually executed here.

## 8. Proof tests (DeepSeek d023; assembled and verified by Sarvam, d024)

Gemini's review (`d022`) accepted MODULE-004 with five test nits: the existing
suites exercised the enumeration and find rules but did not prove that the
implementation avoided the forbidden shortcut each rule bans. DeepSeek (`d023`)
wrote `tests/test_enumeration_proofs.py` to close those five gaps. Sarvam added
the file exactly as delivered, ran it on Python 3.11 and 3.12, verified that
each proof test fails under the mutation it targets, and fixed one fixture
defect in the test (below). No implementation file was changed.

### Commits

| # | Commit | Message |
| --- | --- | --- |
| 1 | `d6f8afb76df6369258f3adbb85261403a6f5c974` | Add MODULE-004 proof tests (DeepSeek) — the d023 file exactly as delivered |
| 2 | `2bb2c3a7626f6134381776beb68645399111b614` | Sarvam fixes: correct nested-directory path in the fd-leak proof test |
| 3 | this commit | Document MODULE-004 proof tests and mutation results (this section) |

Parent of commit 1 is `380613264657e2b035d8841baa1a3ed5b801580b`, the branch tip
before this work.

### Test fix (commit 2)

Commit 1 added the d023 file exactly as delivered. On the real code one test
errored on both interpreters: `test_no_fd_leak_on_repeated_limit_errors_in_nested_directory`
raised `NotFoundError: entry 'p/sub/leaf' not found` instead of the expected
`EnumerationLimitError`.

Cause: the test built the fixture at `root/p/sub/leaf` but referenced
`FileRef("src", "p/sub/leaf")`. In `LocalAdapter` the mandatory ID prefix is
`p/`, so the file_id `p/sub/leaf` denotes the on-disk path `root/sub/leaf`, not
`root/p/sub/leaf`. Gemini's nit-3 fix explicitly specifies the reference
`FileRef("src", "p/sub/leaf")`, so the reference is correct and the *fixture*
was wrong. Commit 2 changes only the fixture path to `root/sub/leaf` (two lines)
so the reference resolves and the test lists a genuinely nested directory
(both a `parent_fd` and a `scan_fd` are acquired). No assertion was weakened and
no production file was touched.

### Interpreters and result

| Interpreter | Command | Result |
| --- | --- | --- |
| Python 3.11.2 | `python3.11 -m unittest discover -s tests -v` | 175 tests, OK |
| Python 3.12.13 | `python3.12 -m unittest discover -s tests -v` | 175 tests, OK |

169 tests pre-existed; the new file adds 6, for 175 total. All pass on both
interpreters.

### Mutation table

Each mutation was applied to a scratch copy of the branch (never committed), the
proof file was run alone, and the mutation was then reverted. "Failing" means
the named proof test failed or errored under that mutation.

| Mutation (the forbidden shortcut) | Proof test(s) that fail |
| --- | --- |
| M1 move the limit check to after the scan loop (`local.py` `list_children`) | `test_scan_stops_early_on_limit` |
| M2 move entry counting below the odd-name filter (`local.py` `list_children`) | `test_backslash_name_counts_toward_limit_and_is_omitted` |
| M3 stop adding the traversed `parent_fd` to `owned` (`local.py` `list_children`) | `test_no_fd_leak_on_repeated_limit_errors_in_nested_directory` |
| M4a drop `or more_dirs_queued` (`search.py` `find`) | `test_max_results_on_last_entry_with_child_directory_queued`, `test_max_results_on_last_entry_with_sibling_directory_remaining` |
| M4b drop `more_dirs_at_this_level` (`search.py` `find`) | `test_max_results_on_last_entry_with_sibling_directory_remaining` |
| M5 `list_page` always passes `max_entries` (`listing.py`) | `test_list_page_passes_max_entries_only_when_given` |

Every mutation is caught by at least one proof test; no proof test passed under
its own mutation, so there is no proof gap. M4a removes the entire
`more_dirs_queued` term, so it necessarily also trips the sibling-directory
test (M4b isolates the `more_dirs_at_this_level` half). Results are identical on
Python 3.11.2 and 3.12.13.
