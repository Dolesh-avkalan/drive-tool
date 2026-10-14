# Current project status

Checkpoint: 2026-10-14. Historical milestone detail remains in PROGRESS.md and session/history/.

## Delivered, awaiting acceptance

- MODULE-001: feature/module-001-storage-contract, 194d4ad57de6dd895c6eaaf7faf4d08583eab02b.
- MODULE-002: feature/module-002-listing, bfbae511a538cff22b642fcd771b7a189029f7e8.
- MODULE-003: feature/module-003-local-readonly, 110b27e81cfead478eb6e400a44043f6e14f14b7. Source, tests and assembly evidence directly retrieved. This supersedes earlier missing-submission status.

## Evidence and review

Sarvam's d002 report records MODULE-001 (36 tests) and MODULE-002 (71 tests) passing on Python 3.11/3.12. Argon's d003 review rates both ACCEPT WITH NITS. Director read the reports and storage/listing source; execution remains delegated.
MODULE-003 assembly evidence reports 67 passing tests on Python 3.11/3.12 after separate fixes. Those results are worker reports, not director execution. Containment and descriptor-lifetime review remain acceptance gates. Generation provenance labels need reconciliation.

## Active review-only tasks

- d005: Gemini 4 Argon review of pinned MODULE-003 against the brief, especially directory symlink swap protection on platforms ignoring no-follow opens.
- d006: browser Sarvam independent rerun of the pinned suite and deterministic synthetic regressions, separating actual behavior from simulated platform behavior.

d005 and d006 completed. Gemini requests changes; Sarvam's saved actual-kernel reproducer exposed outside synthetic bytes, metadata and listings despite 67 passing tests. MODULE-003 is not accepted. d007 is queued for corrections on NEW branch feature/module-003-review-fixes, based on 110b27e; old branches unchanged. Atomic no-follow opens without O_DIRECTORY plus immediate descriptor type checks are the selected correction direction, subject to worker regression evidence and independent re-review. Neither task authorizes implementation-branch changes. The director plans and reviews; browser workers implement and execute tests.

## Resumed on local browser host — 2026-10-14

Owner resumed development after completing the persistent Windows browser VM setup. Codex remains director; Gemini and browser Sarvam implement and execute tests. Owner requires stable, light, modular, predictable behavior. Cloud-box testing is a later gate after local development evidence and review; no cloud deployment is authorized by this checkpoint.

d007's actual branch head was fetched: `c2b324dab4cf720178a36a58b5f1cf1339730151` on `feature/module-003-review-fixes`; fixes commit `6a7d6d4ffd58ebf66f6a5c99388000aa710e98b4`. Exactly four paths differ from the old revision: local adapter, local tests, assembly evidence and review-fix evidence. Worker reports 82 tests passing on Python 3.11/3.12, actual gVisor containment, old-fail/new-pass assertions, and a descriptor-close mutation check. These remain worker reports; MODULE-003 is unaccepted.

d008 independent source/security review was dispatched through the local scripted relay to Gemini model `4 argon`, thinking high, in a fresh context, with the exact pinned source/tests inline. d009 independent Sarvam execution brief is prepared, including parent-close failures after child file/scan acquisition. Read-only director inspection identified these cleanup paths as a concern requiring review and execution evidence. No Drive Tool tests were executed by the director.

## Next

Collect d008; dispatch and collect d009; compare actual artifacts with reports; delegate minimal corrections for confirmed blockers. Then prepare an isolated integration candidate and owner merge recommendation. No implementation merge/deployment without owner approval. Google Drive credentials and cloud-box testing remain later milestones.
