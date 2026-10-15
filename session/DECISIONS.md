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

## D021 — Reject unqualified completeness after skipped scans (2026-10-15)

Independent d034 confirms the 256-test suite and core scan contract, but reports CHANGES REQUIRED with saved reproductions. Codex chooses an explicit terminal truncated=True/reason=skipped when coverage is incomplete due to skipped folders, preserving specific bound reasons. Malformed ScanFailure.error must fail loudly at every depth. Also adopt narrow generic post-yield StorageError accounting for metadata/stat extension robustness; preserve valid original errors, no double wrapping and cleanup priority. D035 assigned to original implementation chat on a NEW branch, file-based brief pinned at drop 652c41e. No merge/deployment. D033 frontier attempt is honestly BLOCKED by file access, despite outer task status done; later review must receive actual code before any verdict. File-based GitHub briefs are verified working for Sarvam; avoid giant pastes except a necessary compact fallback for sites unable to retrieve attachments/links.

## D022 — MODULE-005A clears library gates at e88c9c7 (2026-10-15)

D036 frontier changed-production-code review ACCEPT and D037 independent execution ACCEPT establish the corrected library candidate e88c9c79fdc27057622d44ba51e008f2e4ea4f4f. D037 raw evidence:274 tests on Python3.11/3.12 zero skips,58 adversarial assertions,10 mutation guards all caught, final regression old-fail5F4E/new-pass18. Reviews and raw artifacts pinned in STATUS. Codex accepts library corrections under the strict D020/D021 contract, subject to implementation merge approval. D038 real target runner additionally reports274/zero skips plus10checks PASS on Python3.14.4/ext4; evidence collection in progress. This is not service acceptance, public deployment, or Google Drive validation. D039 design remains a proposal requiring corrected source/API references, truthful deadlines/isolation/shutdown and mutation semantics before an implementation brief.

## D023 — approved MODULE-005A integration (2026-10-15)

Dolesh explicitly approved merging PR #4 at e88c9c7 in this chat. Codex verified the live PR head and merged with expected_head_sha=e88c9c79fdc27057622d44ba51e008f2e4ea4f4f using a history-preserving merge, commit0d9aa8f593e460e3e016114fcde0dc8fc7160951. Fetched main and verified drive_tool/ and tests/ are identical to the reviewed/tested candidate; no new test run was needed for a merge containing only already-tested implementation plus checkpoint documents. D036/D037/D038 evidence remains pinned in STATUS. No service deployment, real-root configuration or Google Drive connector. Next: director-owned range-read contract, followed by delegated implementation/review and minimal authenticated service.

## D024 — range-read contract and delegated start (2026-10-15)

After PR4 approval/integration, Dolesh asked to start the next module. Codex narrowed D039 proposal to optional FileRef-based LocalAdapter.read_range using pread, explicit per-window caps and observed-only metadata/EOF. No new resolver/protocol/service. Full task pinned18cc50f, exact implementation base03a8250, four-path scope and isolated feature branch. D040 Sarvam implementation/test job dispatched through healthy local VM process22319. Live progress confirms task file retrieved and pinned-source inspection underway. Review/testing/acceptance remain pending. Dolesh questioned director overhead: reduce serial transport/terminal chores and repeated status checks; maintain evidence gates, delegate implementation/tests, no unsolicited tool/framework work. No new merge/deployment authorization implied.

## D025 — standing authorization for confident, verified merges (2026-10-15)

Dolesh explicitly says not to ask for merge approval and to merge when confident. Codex may merge Drive Tool implementation PRs after applicable independent review, test/evidence and acceptance gates pass, verifying the exact candidate head with expected-head protection. No repeated per-PR permission question. This supersedes prior per-merge approval language in CORE-ACCEPTANCE.md, historical briefs and decisions. Report merges afterward. Blocking findings still require resolution; deployment/credentials/real storage/public exposure retain their existing authorization scope. AGENTS, current STATUS and acceptance policy updated; active D040 implementation/testing continues unchanged.

## D026 — parallelize independent work across browser hosts (2026-10-15)

Dolesh asks why the team is not working in parallel. Codex acknowledges unnecessary serial scheduling. Local D040 Sarvam remains active; read-only progress confirms implementation smoke and original274 tests pass, dedicated tests being written (builder progress only, no published acceptance). Cloud D041 Gemini4Argon/high service-contract review dispatched via browser-bus c4ef07c, pinned brief9940494. Cloud health ok:true. Design review independent from pending range source; implementation waits for an accepted source contract. Two hosts permit actual simultaneous work; Gemini/DeepSeek share cloud browser serialization, so a third queued review is not a third parallel execution. No new infrastructure introduced or acceptance gates removed.

## D027 — service policy implementation overlaps range verification (2026-10-15)

Dolesh asks to use DeepSeek after cloud Sarvam completes. D044 delivered source-grounded audit at drop152e5b6 with cloud account GitHub access confirmed; Codex read full report, TESTS_NOT_RUN. Codex chooses a minimal pure authentication/grant-policy boundary independent of range/HTTP work and specifies two files with immutable scoped grants, full bounded digest scan, no permission leakage and no filesystem calls. D045 DeepSeek DeepThink implementation/tests dispatched cloud bus063d7aa with brief0a1c8bf; start/output pending. Local D043 independent verification continues. Cloud Sarvam will assemble/test generated D045 files after availability; cloud browser serializes actions, so do not claim three simultaneous browser executions. No acceptance/merge/deployment yet.

## D028 — range reads independently accepted and merged (2026-10-15)

D043 reproduced351tests/zero skips3.11/3.12,7mutants caught for intended assertions,34new adversarial probes bothversions. Evidence drop24a9a45349f2e38eef91e7bb8626aa944cbfe9db; reviewer ACCEPT WITH NITS, no blocker. Codex read full production diff/review/raw logs and accepts121faf848b2f33f80721239fc7716adba7a9ba62. Two maintenance/ordering nits do not change current behavior; retain as observations, no speculative refactor. PR5 created/attached and merged under standingD025 with expectedhead121faf8, merge1515182d0f273518d20a2ef80b5182cf6b36def2. Fetched main and verified drive_tool/tests identical to testedcandidate. No deployment; no new actualcloud/3.14 range-delta test claimed. D045 service policy generation pending.

## D029 — two implementations active across hosts (2026-10-15)

Dolesh points out only one teammate active. Codex fixes scheduling by assigning local Sarvam D047 Python client/fake-server tests against a concrete four-method wire contract, disjoint from cloud D046 policy assembly. Briefd14b1b7, basecedaa13, branchfeature/module-006a-client, two new files; process43959. Live local read-only progress confirms brief/source connector activity and busy=true. Live cloud viewer independently confirms D046 active with verbatim initial policy and separate fixes/final evidence underway. NUL/backslash concern is not confirmed by builder probes; immutable lookup violation confirmed. Two browser hosts now doing separate implementation/test work simultaneously. No third same-host concurrency claim, no source implementation by Codex, no merge/deployment until evidence gates.

## D030 — task-appropriate model selection; policy frontier review complete (2026-10-15)

Dolesh says Argon need not always run high and Gemini3.8 is decent, leaving choice to Codex. Adopt lighter/lower-effort options for routine work and reserve frontier/high for hard architecture/security. Verify actual model/effort, no silent mismatch or acceptance-gate changes. D0484argon/high static policy review completed ACCEPT WITH NITS/0blockers, sole undefined Mapping annotation import is not runtime failure; Codex read complete report. D049 fresh execution remains pending/unverified; local D047 client task still running per last process poll. Cloud viewer currently displays Sarvam Code waitlist, not evidence D049 started. Do not conflate Code product access with Cowork/GitHub execution access confirmed in D046.

## D031 — useful queued work for Gemini and DeepSeek (2026-10-15)

Dolesh asks for work for idle Gemini/DeepSeek. Codex specifies D050 minimal synchronous file-service operation layer (two files, adapter leases/grant anchors/short result/error contract, no HTTP socket yet) for4Argon/medium, and D051 independent static review of intermediate published client81a6485 for DeepSeek. Briefs pinned8eac68b, cloud bus6d06e5c. Both queued behind D049, not claimed active. Cloud journal proves D049 prompt submitted16:55:19 after90s cooldown; selected viewer Code tab did not show active owned chat. No relay bug or restart identified/needed. Local D047 still running per poll and its intermediate production594lines supplied to reviewer; later byte changes require delta review. Control shell session recreated from observed iframe URL, routine existing Google auth refreshed, SSH existing key reused. No credentials printed, no infra/source implementation by Codex.

## D032 — policy integrated; client handed to fresh independent context (2026-10-15)

User says both Sarvam jobs done. D049 independent report/raw413/6semanticmutants/103probes read, drop20321d4, ACCEPT WITH NITS no blocker; D048 frontier0blockers. Codex accepts dc3c2af7 and creates/attaches/merges PR6 via expectedhead underD025, mergee55c9f741a528ce9298b5041012373195e878eaf. Fetchedmain source/tests exactly match reviewedcandidate. No deployment/3.14/realservice claim; annotation nit retained as nonblocking observation.

D047 local completed zero repairs, report/raw412/61new/6mutants3.11/3.12 at dropc2c90a3; finalhead81a6485 same productionblob as queuedD051. D052 fresh local independent execution/integrated candidate assembly dispatched process23827, briefd65d30a, basee55c9f7+twoexactclientfiles, no fixes/merge. D050 cloud launch visibleArgonMedium selected; prompt submission pendingverification, D051 queued. Do not claim all jobs concurrent on one host.

## D033 — same-chat completion heartbeat (2026-10-15)

Dolesh identifies the orchestration gap: once director turn ends, teammate completion does not wake it; he has been manually prompting. Codex checked official scheduled-task documentation and used app automation_update to create ACTIVE same-thread heartbeat Drive Tool team handoffs (id drive-tool-team-handoffs), every5minutes. It checks current pinned results/evidence, continues scoped reviews/corrections/assembly and verified merges underD025, keeps unchanged state quiet, notifies meaningful completion/failure/input only. Scope is current local agent-service+synthetic reliability milestone; no new OAuth/real roots/public deployment. Disable when milestone/no pending authorized work or user stop. Existing app/PC must stay running; no instant callback or delivery guarantee. This closes handoff wake-up gap without building a new scheduler framework. Existing browser jobs are not restarted or duplicated.
