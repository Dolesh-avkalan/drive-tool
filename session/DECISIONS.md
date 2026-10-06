# Decision log

## D001 — Director owns plans and architecture (2026-10-06)
Dolesh explicitly requires Codex to own plans, architecture, task briefs, acceptance criteria, verification, and integration decisions. Implementation and test execution are delegated to browser chatbots. Delegation does not transfer responsibility for acceptance.
Supersedes the earlier suggestion that a chatbot would create the initial architecture.

## D002 — Two Sarvam roles (2026-10-06)
API Sarvam is the messenger/browser operator. Sarvam in the logged-in Indus browser is the initial implementer and test executor.
Supersedes the proposed first task to ChatGPT. No implementation task was sent to ChatGPT.
Other capable chatbots may be selected later for complexity or independent review; do not change the initial arrangement without a reason and owner context.

## D003 — VPS first, stable PC release later (2026-10-06)
Build and test on the Ubuntu VPS, stabilize, then deploy a pinned stable version to Dolesh's PC. VPS local fixtures are not access to the PC drive.
Preserve browser-tool services. PC OS and folders are unresolved.

## D004 — Small modules with public self-contained briefs (2026-10-06)
Store specifications in tasks/. Supply pinned public links through the messenger; if link reading fails, paste the exact brief.
Require task ID, interface, branch, filenames, boundaries, acceptance tests, evidence, and explicit failure statuses.
Public briefs use synthetic examples and never credentials or private data.

## D005 — Start with in-memory read-only contract (2026-10-06)
MODULE-001 exposes stat, list_children, and read_bytes with frozen refs/metadata and explicit errors.
No filesystem access, Google Drive, network service, writes, deletes, or deployment in this module.
The read_bytes interface is a small-module starting point; bounded/streaming access must be specified before production large-file adapters.

## D006 — Feature branch and honest submission (2026-10-06)
Implementation branch is feature/module-001-storage-contract. Worker pushes if actually authorized; otherwise returns PUSH_BLOCKED and files. No fabricated push/test claims.
Actual push succeeded. Hold integration until director acceptance and any required independent review.

## D007 — Persist messenger output instead of ping (2026-10-06)
The host saves API Sarvam replies/events. The director polls them during an active session or on request.
This is not continuous monitoring across sessions. No webhook, ping, or scheduled automation was configured.

## D008 — Separate evidence levels (2026-10-06)
GitHub commit existence was directly verified. Test execution is reported by browser Sarvam and stored as evidence. Initial director code review is separate from independent test execution. Do not collapse these into an unqualified "verified tests passed."

## D009 — Repository checkpoint is mandatory (2026-10-06)
Maintain session/ with current status, decisions, chronological progress, connection guidance, and session history. AGENTS.md points future agents to it. Update after significant progress and before ending a work session. Never store secrets here.
