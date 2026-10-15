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

## D016 — Light, reliable agent interface; quality review findings (2026-10-15)

Dolesh's requirement: about 6 agents use the tool all day at random times; it must be a light,
solid, boring routine and must not cost agents many tokens to get or save files. Adopted the
design in session/AGENT-INTERFACE.md: at most 5 tools, one call per normal action, short
replies and a fixed error-code set, stateless calls, adapters held open for the service
lifetime, per-agent caps and deadlines, scan-time enumeration budgets, atomic no-overwrite
saves, and a 6-agent soak test as the rollout gate. Module order: MODULE-004 bounded
enumeration, MODULE-005 save, MODULE-006 service, then soak. Google Drive follows.

## D017 — MODULE-004 accepted at 70a7a00 (2026-10-15)

Director acceptance of feature/module-004-bounded-enumeration at 70a7a00 (bounded list_children with
EnumerationLimitError, ListingService passthrough, bounded breadth-first find). Basis: Argon code review
ACCEPT WITH NITS (d022, no blocking findings); proof tests for all five nits (d023 DeepSeek, d024 Sarvam,
one test-fixture path fix in 2bb2c3a); independent director execution 175/175 on Python 3.13.16 and an
independent mutation check (6 targeted mutations, each caught by its proof test), matching Sarvam's table.
Known design gap, deferred to a later module: find() in a folder larger than its entry budget returns no
items (listing is all-or-nothing). Merge into main only with Dolesh's approval.

## D018 — MODULE-005 accepted at a65da4e (2026-10-15)

Director acceptance of feature/module-005-local-write at a65da4e: LocalWriter (write_bytes with temp file +
fsync + atomic no-replace link publish, overwrite via rename only onto a regular file; mkdir), temp files
hidden from listings, AlreadyExistsError/ReadOnlyError in storage.py. Basis: Argon security/code review
ACCEPT WITH NITS (d028, no blocking findings), nits closed (d029 DeepSeek proof tests + 2 small code edits,
d030 Sarvam, 8 mutations all caught); independent director run 204/204 on Python 3.13.16, director mutation
checks (link->rename, no temp cleanup, no O_NOFOLLOW, no temp filter, no parent fsync, no O_CLOEXEC, short temp
name, cleanup-flag order: all caught; one surviving mutation is an unreachable defensive branch), adversarial
probes (symlinked parent refused and outside dir untouched; 20 concurrent writers -> exactly 1 winner, 19
AlreadyExistsError, winner's exact bytes; 4 s folder<->symlink swap race: 549 writes, 0 outside; no temp files
or descriptors left). Rule decided: with overwrite=False, if the parent-directory fsync fails after the link has
published the file, the error stands (durability not confirmed) and the file stays; a retry gets
AlreadyExistsError. Request-id idempotency for that case belongs to the service layer (MODULE-006). Merge only
with Dolesh's approval.

## D019 — MODULE-005 merged; resume through the local team relay (2026-10-15)

Codex fetched Claude's handoff, verified MODULE-004 is merged and MODULE-005 PR #3 is open at a65da4e. Checked the source delta, raw Sarvam logs and byte-identical proof/evidence artifacts; Claude's independent 204-test/mutation/race results remain attributed to Claude. Dolesh explicitly approved PR #3 in this chat. Merged using expected head a65da4e as a84f6b3328d2caeb8d71f7c009e370dfea9cf452. No deployment. D031 fresh Argon design review is dispatched through the healthy local VM relay for partial bounded enumeration and accurate global raw-entry budget accounting, without changing the old listing contract. Cloud relay handoff tasks are complete. Historical status preserved; current STATUS rewritten for a clear resumption point.

## D020 — Strict partial-scan contract and truthful service continuation (2026-10-15)

D031 Argon (4 argon/high, zero repairs) proposes ScanBatch/scan_children and identifies the offset/sorted-list/raw-budget contradiction. Codex accepts the capability direction but rejects three holes: uncharged B+1 witness, uncharged mid-scan failures, and metadata-count legacy fallback. MODULE-005A defines strict no-lookahead scan bounds, conservative exact-budget EOF uncertainty, ScanFailure with actual consumed raw count and original StorageError, and explicit ScanUnsupportedError for legacy adapters without accountable scan capability. Existing list_children exact-limit/full-list contract stays unchanged. Service spec now limits offset pagination to complete bounded listings, reports bounded search truncation honestly, and removes the unsafe blanket promise that interrupted writes can be blindly retried (D018). D032 fresh Sarvam implementation/testing is active on a separate branch from 356a4d8, no merge/deployment. No external search completeness or snapshot guarantee is made.
