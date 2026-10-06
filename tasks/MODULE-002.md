# MODULE-002 — paginated listing helper (Sarvam, easy)

Repository: https://github.com/Dolesh-avkalan/drive-tool
Worker: browser Sarvam. Continue the exact MODULE-001 conversation, not a new chat.
Base commit: 194d4ad57de6dd895c6eaaf7faf4d08583eab02b (MODULE-001, not main).
Branch: feature/module-002-listing
Commit message: Implement MODULE-002 paginated listing helper

## Scope
Python 3.11+, standard library only. Build a small read-only helper over the existing StorageAdapter contract. Do not modify MODULE-001, add network/storage connectors, deploy anything, or merge branches.

## Files
- drive_tool/listing.py
- tests/test_listing.py
- docs/evidence/MODULE-002.md
Only add these files. Keep existing files unchanged.

## Contract
Frozen dataclass ListingPage(items: tuple[FileMetadata, ...], next_offset: int | None).
ListingService(adapter: StorageAdapter).
list_page(parent: FileRef, *, limit: int = 50, offset: int = 0, name_contains: str | None = None) -> ListingPage.
Obtain adapter.list_children(parent), copy the results, sort by (name, ref.source_id, ref.file_id). Optional filter is a case-sensitive substring of name. Empty filter "" includes all items. Filter before pagination.
limit must be an integer 1..100 and offset an integer >=0; booleans are invalid. Invalid types/ranges raise ValueError. name_contains must be None or str, otherwise ValueError.
Validate arguments before calling the adapter.
offset may equal filtered length, yielding empty tuple and next_offset=None. offset greater than length raises ValueError.
next_offset is offset + number of returned items when more filtered items remain, else None.
Do not cache listing, mutate adapter results, or intercept adapter StorageError exceptions.
This is offset pagination of a fresh listing, NOT a consistent snapshot across calls. Explicitly document that files changing between calls can shift pages; opaque durable cursors are out of scope.

## Acceptance/tests
Use unittest and synthetic fixtures:
- Empty source; sorted first/middle/final pages; offset equal length; offset above length.
- Exact next_offset behavior and tuple/frozen result.
- Filtering before pagination; case sensitivity; empty filter; no matches.
- Invalid limit/offset incl bool, float, str, negative and limit 0/101.
- Invalid filter type.
- Fake adapter call counter proves invalid arguments do not call adapter.
- Adapter errors propagate without changing their type.
- Existing 36 tests still pass.
Run python3 -m unittest discover -s tests -v in your sandbox; record Python version, actual stdout/stderr, exit and source revision. If execution unavailable say TESTS_NOT_RUN.

## Submit
Once done, push only these additions to feature/module-002-listing, created from the exact base above. Never push implementation to main.
Return task ID, actual branch/commit, test command/results, evidence URL and limitations. If push is unavailable report PUSH_BLOCKED and return complete path-labeled source. Never invent test or push evidence.
Do not start another implementation module. The messenger may later ask you to transport/test Gemini's separate MODULE-003; keep its branch isolated.
