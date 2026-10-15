# D044 — cloud Sarvam: service readiness audit (parallel)

Sarvam, I lead Drive Tool. I need a source-grounded readiness audit for the minimal agent service. Local Sarvam in another conversation is already independently testing range reads (D043); do not duplicate that work or edit its branch. Your task is design/source inspection only, TESTS_NOT_RUN. No service implementation, installs, credentials, main/feature source changes, deployment or merge.

First retrieve this brief and confirm GitHub read/publish access in this account. If unavailable, report BLOCKED with what is missing; do not pretend to read files, solicit secrets, switch accounts or change permissions. Read public refs only and publish only the non-secret report below.

Inputs:
1. Dolesh-avkalan/drive-tool accepted implementation base03a8250a5d02823812533290356cf375b7d2c33e: drive_tool/storage.py, local.py, local_write.py, listing.py, search.py. These are accepted local library code.
2. Pending range candidate121faf848b2f33f80721239fc7716adba7a9ba62: local.py and docs/evidence/MODULE-005B.md. Independent verification pending; do not accept it by implication.
3. Director requirements tasks/d041-service-contract-review.md at994049485885994fe8653d8b4ec7a25fb578816d.
4. DeepSeek design proposal in Dolesh-avkalan/drop at0f352c7b1e98168126d115a73e46052767a41572, drive-tool/module-006/d042-deepseek-service-contract.md. It is a proposal, not an accepted contract. D039 Gemini design is historical, no need to repeat it.

Return a concise implementation-readiness report with:
- Exact existing public signatures/return fields/error types and source references for list/search/read/save/mkdir/lifecycle, and precise mapping for four service client methods. Do not invent mtime metadata, LocalAdapter.find or src/ layout. Distinguish accepted versus pending read_range.
- A minimal safe policy choice: grant-specific anchored adapters remapping source-relative IDs, or whole-source-root grants only for first release. Assess whether existing constructor/rootfd behavior supports the former without a new resolver. Avoid lexical prefix permission claims under ancestor rename. No code required; identify where source confirms or contradicts the design.
- A consistent small resource budget for6agents: fixed connection cap, no unbounded queue, per-agent/source/global active limits, 1MiB read/write caps, fixed request-header/body/response byte caps, and aggregate-memory arithmetic including concurrently buffered bodies and completed timed-out work. Do not rely on HTTP inactivity timeouts for absolute slow-trickle deadlines. Specify duplicate Content-Length/Transfer-Encoding rejection and Connection:close behavior.
- Critique DeepSeek's shutdown paragraph: Python non-daemon threads can prevent normal process exit after a grace period. Choose one honest minimal lifecycle rule without claiming cancellable kernel I/O or closing active descriptors. Distinguish a graceful timeout report from forced process termination and uncertain writes. No supervisor framework.
- A preimplementation acceptance matrix (about12 rows) linking auth/policy non-leakage, framing/trickle, saturation, retained quotas on timeout, delayed/post-publish saves, missing-parent side effects, observed EOF, redaction, drain and smoke600-before-soak10000 to measurable assertions. These are test specifications, NOT executed results. Separate hardware-sensitive benchmarks from correctness.

Keep core service/client small, Python3.11+ stdlib, loopback synthetic roots only. Defer MCP, Google OAuth, real roots, public remote transport, durable jobs/idempotency/indexing. Do not propose speculative infrastructure. Raise concrete implementation blockers with a minimal scope alternative; don't broaden the project.

Publish REPORT.md only under Dolesh-avkalan/drop main drive-tool/module-006/d044-service-readiness/ (do not overwrite D042), with input pins, exact references, findings/decisions/options and TESTS_NOT_RUN. No scripts/tests/code needed. Short chat summary including report commit and blockers; if publish unavailable return report text with PUBLISH_BLOCKED. I choose final contract and integration.
