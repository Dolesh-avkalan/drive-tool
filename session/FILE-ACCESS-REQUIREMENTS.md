# Direct access to permitted files

Design requirement established 2026-10-15. Codex owns this design; Gemini and Sarvam develop and review the components. This document defines acceptance requirements, not implemented capabilities.

## Agent experience

Any authorized agent should be able to locate and read a permitted file in its existing storage location. The agent should not need to map or index the entire drive, manually relocate the file, understand backend internals, or create a staging copy in a special folder first.

Configure a storage source and its permitted scope once. Individual files are addressed on demand by a stable source/file reference; backend IDs, shared links and paths relative to an authorized local root may be resolved through explicitly specified connector operations. Do not accept arbitrary host paths or URLs as authority. A reference identifies a file but does not grant access to it.

## Required behavior

- Known file: resolve its reference, check permission and scope, retrieve metadata and read its bytes directly from the source. A known-file lookup must not recursively enumerate the drive.
- Unknown file: offer backend-native search where supported, or bounded folder traversal otherwise. Return references and continuation state. Search must expose budgets, truncation and cancellation; an incomplete search is not proof that the file does not exist.
- Permissions: enforce both the underlying source's permission and the agent's configured policy on each operation. Being discoverable or having a link must not bypass permission. Account for moves, shortcuts and revoked access with connector-specific tests.
- Retrieval: support bounded, streaming access without requiring a durable full-file staging copy. Define chunk limits, cancellation, timeouts, cleanup and partial-read behavior. Any export/conversion needed by a source must be explicit and keep the original file in place.
- Reliability: give precise outcomes such as access denied, not found, source unavailable, timeout, budget exhausted and unsupported format. Retry only transient failures within a bounded policy; do not hide permanent failures or silently substitute cached contents.
- Multi-agent use: expose the same storage operations to every authorized agent through a shared authenticated interface with separate identities/scopes, concurrency limits and redacted audit records. File permissions are not coupled to which model performs the task.
- Lightweight operation: no mandatory whole-drive scan, permanent mirror, full-drive index or synchronization as a prerequisite for file access. Optional caching or indexing later must remain an optimization and cannot expand access rights.

## Acceptance examples

1. Given a known permitted local file or Google Drive reference, a new agent can read it without scanning unrelated folders or copying it elsewhere first.
2. Given a name and a permitted search scope, the agent gets bounded search results and can read a selected reference; it can continue an exhausted search explicitly.
3. An unauthorized agent cannot read the same reference. Permission revocation and file relocation produce a defined outcome, not stale authorization or silent fallback.
4. A large file can be consumed incrementally; cancellation releases handles and network resources without leaving a required persistent staging file.
5. Concurrent agents have independent access checks and bounded resource use. A stalled source does not block all other sources indefinitely.

## Current gap

The accepted candidate has local read-only access and source/file references. It lacks reference resolution for external links, bounded enumeration/search, streaming, Google Drive connectivity, shared authenticated API/MCP and per-agent policy. Its 140-test cloud result validates the current read-only core only. These requirements guide subsequent small modules; they are not a reason to rebuild the accepted core or add a speculative orchestration framework.
