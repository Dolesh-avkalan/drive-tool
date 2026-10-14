# d013 — assemble and test an isolated read-only core candidate

Director: Codex. Worker: browser Sarvam. PREPARED ONLY: dispatch after d011/d012 acceptance, with any updated source pins supplied by the director. Do not start from this file alone.

Base MODULE-003 candidate: 80bf54885877c475d2ac59ddb3e9f96384600b9e.
MODULE-002 source: bfbae511a538cff22b642fcd771b7a189029f7e8.
Shared MODULE-001 base: 194d4ad57de6dd895c6eaaf7faf4d08583eab02b.
New isolated branch: feature/readonly-core-candidate. Do not merge existing branches or main.

Assembly scope: begin at the accepted MODULE-003 candidate. Add the exact existing drive_tool/listing.py, tests/test_listing.py and docs/evidence/MODULE-002.md from the pinned MODULE-002 revision, preserving their bytes. Verify shared storage.py and __init__.py match the shared MODULE-001 base. Do not implement fixes, add dependencies, modify adapter/listing/storage source, introduce API/OAuth/jobs/writes, deploy, mount host directories or test real user data. If branches are incompatible, report INTEGRATION_BLOCKED and exact evidence; do not invent an interface shim.

Add only tests/test_readonly_core.py and docs/evidence/READONLY-CORE-INTEGRATION.md for integration-specific tests/evidence. Tests must use tiny synthetic directories in your sandbox, not duplicate individual module tests. Verify ListingService with actual LocalAdapter for multi-page nested directory listings, filtering/sorted metadata and parent/source refs, omitted symlinks/special entries, wrong-source error propagation, invalid options avoiding adapter work, and a real adapter listing cleanup failure surfacing through the service unchanged. Assert observable cross-module behavior, not helper structure.

Run the combined suite on actual Python 3.11 and 3.12 if available; record exact assembled tree/hash, environment, commands, stdout/stderr, exits, counts/skips. Independently establish the count; do not infer it by adding previous reports. Confirm original adapter/listing/storage source blobs remain byte-identical to their accepted pins. Original module evidence remains historical; new evidence identifies the assembled candidate and new execution.

Document current resource and feature boundaries: read_bytes is bounded, but listing still materializes a whole direct directory; page size is not an enumeration bound. POSIX capability-gated local adapter; no native Windows filesystem support, Google Drive connector, writes, REST/MCP, jobs, or production-release claims. Synthetic cloud-box tests are a later gate. If the suite fails, report it honestly and hand the defect to the director for a separate scoped task.

Return source/evidence commit URLs on the new branch if GitHub tools are actually available; otherwise PUSH_BLOCKED with complete downloadable patch/source/evidence bundle and manifest/hashes. Preserve raw logs under Dolesh-avkalan/drop drive-tool/readonly-core/d013-integration/ if possible. No merge or deployment. Do not start another module.
