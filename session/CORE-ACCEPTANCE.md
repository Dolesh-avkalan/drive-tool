# Core acceptance and next design gates

Director-owned design, resumed 2026-10-14 under the owner's stable/light/modular requirement.

## Current acceptance boundary

Update 2026-10-15 (D015): the integrated read-only core (99e1025) is merged into main as
a709f0d. The paragraph below describes the pre-merge state; the scope limits still apply.

MODULE-001 storage contract/in-memory adapter, MODULE-002 offset listing helper and MODULE-003 bounded read-only POSIX adapter form the initial core. They are separate feature branches, not an accepted service or release. No writes, OAuth, network collection, durable jobs, REST/MCP or deployment is implied by accepting these modules.

Before recommending integration:

- Resolve concrete MODULE-003 containment and descriptor ownership defects using narrow worker changes and deterministic regressions.
- Retrieve fresh independent review and execution evidence at exact source commits; distinguish actual kernels, mocked semantics, skipped tests and inferred portability.
- Assemble a separate candidate from accepted branches and delegate the combined suite. MODULE-001 has 36 tests, MODULE-002 adds 35 and corrected MODULE-003 currently adds 46 according to reports; an integrated candidate must establish its actual count rather than add numbers and declare success.
- Clarify StorageAdapter.source_id as a read-only property in the shared protocol before relying on static structural conformance. Keep root-ID normalization an adapter responsibility. Preserve concrete storage error propagation.
- Review MODULE-002's nits against the precise contract; invalid listing options must fail before adapter access. Do not create a second inconsistent ref-validation or source-mismatch policy in the listing layer.

## Resource gate before agent-facing service

The present `list_children` materializes direct children and MODULE-002 sorts/filters that full list. Page size <=100 does not bound enumeration cost. Do not claim streaming pagination or large-directory memory bounds. A later narrow task must bound inspected entries and returned metadata at the local adapter boundary, with a typed exhaustion error, deterministic tests and explicit defaults. A service-level length check after materialization is insufficient. Recursive search must have its own traversal budget, depth limit and cancellation before it exists.

Reads remain <= configured byte limit with max+1 detection. Future streaming must define chunk bounds, ownership, cancellation, partial results and cleanup explicitly. Avoid buffering a whole upload/download to simplify an implementation. No background crawling/indexing or automatic synchronization in v1.

## Cloud-box test gate

Owner plans a cloud-box test after confidence in local worker artifacts. Prepare a pinned candidate and a disposable synthetic-only test plan first. Request actual cloud-box connection details when needed; do not assume the old browser VPS is the test machine. No Google credentials or real user storage is needed for core containment/resource tests. On a conformant Linux filesystem, test the same race/resource mechanisms plus restart behavior when there is a service to restart. A sandbox's passing tests are not cloud-box execution evidence.

## Integration control

Codex owns acceptance and integration decisions. Browser workers implement, assemble and execute. No implementation merge or deployment without the owner's approval of a concrete candidate. Session checkpoint/specification commits may preserve progress without merging implementation. Keep main, accepted artifacts and release claims distinct.
