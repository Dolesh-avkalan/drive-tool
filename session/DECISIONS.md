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

## D010 — Two concurrent modules (2026-10-06)
Owner authorizes two modules at a time, superseding the one-at-a-time limit for this batch. Browser Sarvam continues its MODULE-001 thread for easy MODULE-002 (pagination); Gemini 3.1 Pro gets harder MODULE-003 (bounded read-only local adapter) in another tab. Both branch from pinned MODULE-001, use disjoint files, and do not merge/deploy.
Gemini cannot push; API messenger captures its complete output and asks browser Sarvam to assemble, execute tests, and push MODULE-003 on its own branch. API messenger does not invent or independently implement missing code. Model selection must be visibly verified; no silent substitute.

## D011 — Local browser relay; cloud-box gate later (2026-10-14)

Owner explicitly resumed Drive Tool after browser migration and reiterated that Codex directs while Gemini and browser Sarvam do implementation and test execution. Use the authenticated local headed Brave host and scripted relay without routine API-agent repair. Gemini's current selected model is `4 argon`; Sarvam handles implementation/assembly/testing. Use fresh contexts for independent verification and preserve useful implementation threads for follow-ups. This supersedes the old VPS messenger requirement and old Gemini 3.1 Pro selection in D002/D003/D010. The later cloud-box test is a separate integration gate; preserve the existing VPS.

## D012 — Predictable modular core (2026-10-14)

Owner's priorities: stable, light, modular, and almost boring behavior. Keep storage contract, adapters, listing, jobs, API and policy separated. Prefer standard library and explicit bounded behavior, deterministic errors and narrow scopes. Correct concrete correctness/resource defects before adding connectors or writes. Require raw evidence and independent review for containment and descriptor ownership; 82 passing worker tests alone do not establish acceptance. Do not add frameworks or rewrite modules without a demonstrated need. Director owns specifications, acceptance and integration; workers implement and execute.

## D013 — Codex leads its development team (2026-10-15)

Dolesh explicitly requests that Codex take project responsibility and address Gemini/Sarvam as its team, by name or concrete role, without calling them workers or calling him the owner. New briefs should say "I will get approval" or explain a dependency and parallel work, rather than attributing blame or coordination to Dolesh. Codex owns assignments, review, design and delivery. This supersedes earlier role labels in new communication; historical evidence remains unchanged. User authority and approval requirements are preserved.

## D014 — Direct access without full-drive mapping or manual staging (2026-10-15)

Drive Tool must let any authorized agent locate and read permitted files in place. Known references resolve directly without recursive enumeration; unknown-file search is bounded and reports continuation/exhaustion honestly. No mandatory drive mirror/index or copy into a special folder. The underlying source permission and per-agent policy both apply; a reference is not a grant. Streaming, limits, precise failures, cancellation and multi-agent isolation require separate modules. Requirements and explicit current gaps are in session/FILE-ACCESS-REQUIREMENTS.md. This is a design requirement, not a completed connector/service claim.

## D015 — Read-only core merged into main (2026-10-15)

Dolesh approved merging the accepted read-only core (MODULE-001, MODULE-002, MODULE-003 at
80bf548, integration candidate 99e1025) into main; merged as a709f0d with a merge commit that
preserves worker history. Feature branches remain as historical evidence. The merge is a library
baseline only; deployment, agent-facing listing (needs an enumeration budget), Google Drive,
writes and the API remain separate gated work. Supersedes the "separate feature branches, not
merged" boundary in CORE-ACCEPTANCE.md.
