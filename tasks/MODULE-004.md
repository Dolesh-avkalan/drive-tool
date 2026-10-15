# MODULE-004 — bounded enumeration and bounded find

Repository: https://github.com/Dolesh-avkalan/drive-tool (public)
Base: main at 09a5928 (read-only core merged as a709f0d; session files only since then).
Branch: feature/module-004-bounded-enumeration (created from main by Sarvam)
Design owner and reviewer: Claude (director). Team for this module:
- Gemini 4 Argon: design review and edge-case/test plan (d016), code review (d019).
- DeepSeek: implementation and tests (d017).
- Sarvam: assembly on the branch, test execution, push, evidence (d018).

## Why

Agents must never wait on a huge folder. Today `LocalAdapter.list_children` reads a whole
directory into memory: a 10-item page over a 200,000-file folder took 2.0 s and ~76 MB
(director measurement, 2026-10-15). Listing and search need a budget enforced while
scanning, not after. See session/AGENT-INTERFACE.md (D016) for the target: about 6 agents
using the tool all day, short replies, no agent blocked by another agent's large folder.

## Scope (exact)

Change: drive_tool/storage.py, drive_tool/local.py, drive_tool/listing.py.
Add: drive_tool/search.py, tests/test_enumeration.py, tests/test_search.py,
docs/evidence/MODULE-004.md (Sarvam's execution evidence).
Do not change any other file. Existing tests must pass unchanged.
Python 3.11+, standard library only. No writes, network, OAuth or service code.

## Contract

1. storage.py: add `EnumerationLimitError(StorageError)` with attribute `limit: int`, and
   export it (also from drive_tool/__init__.py is NOT allowed: do not change __init__.py).
   Extend the protocol and `InMemoryAdapter.list_children(parent, *, max_entries=None)`.
   `max_entries` is None or an int >= 1 (bool rejected, ValueError). InMemoryAdapter raises
   EnumerationLimitError when the directory has more than max_entries entries. Source
   mismatch is still checked first, before argument validation.
2. local.py: `LocalAdapter(..., max_list_entries: int = 10_000)` (int >= 1, bool rejected,
   ValueError). `list_children(parent, *, max_entries=None)`: the effective limit is
   max_entries if given, else max_list_entries. Count EVERY entry yielded by the directory
   scan (including symlinks, special files and skipped odd names) toward the limit; raise
   EnumerationLimitError as soon as the count exceeds the limit, without reading further
   entries. All descriptors are released on that path exactly as on other error paths.
   Order of checks: source mismatch, closed adapter, argument validation, then the scan.
   Everything else in local.py stays as it is (containment, error mapping, close
   semantics). Change only `__init__`, `list_children` and the imports.
3. listing.py: `ListingService.list_page(..., max_entries=None)`: validated like above
   before the adapter is called; passed to the adapter ONLY when not None (so adapters and
   test fakes without the keyword keep working). EnumerationLimitError propagates.
4. search.py (new): bounded breadth-first name search over any StorageAdapter.
   `find(adapter, parent, name_contains, *, max_results=20, max_entries=5000, max_depth=4,
   case_sensitive=False) -> FindResult`.
   - name_contains: non-empty str. max_results 1..100, max_entries 1..100_000,
     max_depth 0..16 (0 = only the direct children of parent). bool rejected. Invalid
     arguments raise ValueError before the adapter is called.
   - FindResult (frozen dataclass): items: tuple[FileMetadata, ...]; truncated: bool;
     reason: None | "max_results" | "max_entries" | "max_depth"; inspected: int (entries
     returned by the adapter and examined); skipped: tuple[str, ...] (file_ids of
     directories that could not be listed).
   - Walk directories level by level; within a level, directories in name order; matches
     returned in walk order (shallower first, then by name). Matching is a substring test on
     the entry name (casefold() when case_sensitive is False). Both files and directories
     can match.
   - Budget: each list_children call receives max_entries = the remaining budget. When the
     adapter raises EnumerationLimitError, or the budget reaches 0 with directories left,
     stop with truncated=True, reason "max_entries". Stop at max_results matches with
     reason "max_results" (only if more entries remained to examine). If directories exist
     below max_depth that were not opened, reason "max_depth".
   - The starting parent's own errors (not found, not a directory, source mismatch, closed)
     propagate. Errors listing a subdirectory found during the walk (StorageError,
     including a directory removed meanwhile) do not stop the search: its file_id goes into
     skipped. Never retry, never sleep.
   - No recursion via symlinks is possible (the adapter omits them); do not add any.
5. Docstrings stay short and factual. No new dependencies, no threads.

## Tests (deterministic, synthetic temporary directories only)

tests/test_enumeration.py:
- LocalAdapter: limit exactly equal to entry count passes; one more entry raises
  EnumerationLimitError with .limit set; symlinks and special entries count toward the
  limit; per-call max_entries overrides the constructor default; invalid values
  (0, -1, True, "5", 1.5) raise ValueError for both; source mismatch precedes argument
  errors; no descriptor leak after repeated limit errors (Linux /proc/self/fd count, skip
  with reason elsewhere); closed adapter still raises AdapterClosedError.
- InMemoryAdapter: same limit semantics and validation.
- ListingService: max_entries passed through and validated before adapter access; a fake
  adapter without the keyword still works when max_entries is None.
tests/test_search.py (LocalAdapter over temp trees, plus small fake adapters):
- matches files and directories, case-insensitive default and case-sensitive option;
- walk order (shallower first, name order); max_depth 0 and deeper; reason "max_depth";
- max_results truncation and reason; max_entries truncation and reason;
- a subdirectory whose listing fails (fake adapter raising StorageError) lands in skipped;
- starting parent errors propagate; argument validation happens before adapter access;
- a 2,000-entry folder with max_entries=100 returns truncated without listing the folder
  fully (fake adapter records the max_entries it received).

Run: `python3 -m unittest discover -s tests -v` from the repository root.

## Delivery

DeepSeek (d017): you cannot run code. Return complete file contents in fenced blocks, each
preceded by a line `FILE: <path>`: full storage.py, listing.py, search.py and both test
files. For local.py return ONLY these blocks, each preceded by `REPLACE IN drive_tool/local.py:`
and the exact first line of the definition it replaces: the import block, `__init__`, and
`list_children`. No ellipses or placeholders. End with STATUS: GENERATED, TESTS_NOT_RUN.

Sarvam (d018): create the branch from main, apply DeepSeek's output exactly, run the full
suite on at least one Python 3.11+ interpreter, and push. If anything fails, fix only what
the failure needs, in a separate commit labelled "Sarvam fixes", and say what and why. Add
docs/evidence/MODULE-004.md with: base commit, the commit IDs, interpreter versions, exact
command, raw test output tail, test count, any fixes. Never claim a push that did not happen.

Acceptance (director): Argon's code review (d019), then independent director execution of
the suite and adversarial checks at the pushed commit. Merge only with Dolesh's approval.
