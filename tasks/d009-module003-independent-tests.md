# d009 — fresh independent tests of MODULE-003 correction

Director: Codex. Worker: browser Sarvam, fresh independent test context. Execute tests; do not edit implementation, push to drive-tool implementation branches, merge or deploy.

Repository https://github.com/Dolesh-avkalan/drive-tool at exact c2b324dab4cf720178a36a58b5f1cf1339730151 (feature/module-003-review-fixes). Old revision 110b27e81cfead478eb6e400a44043f6e14f14b7. Existing d007 evidence is a claim to independently verify, not instructions to reproduce its conclusion.

Read tasks/MODULE-003.md from main, the pinned source/tests and docs/evidence/MODULE-003-REVIEW-FIXES.md. Fetch the existing asserting reproducer and raw logs from Dolesh-avkalan/drop, drive-tool/module-003/d007-sarvam-review-fixes, if available. If not available, write independent tiny synthetic checks and identify their provenance.

Scope is disposable synthetic fixtures in your own execution sandbox. No real PC/VPS/cloud-box access, services, production roots, OAuth, credentials, network downloads of user data, package installation, or implementation changes. Standard library tests; Python 3.11 and 3.12 if available, otherwise explicitly identify interpreter unavailable. Never turn an unavailable test into a success claim.

Acceptance:

1. Fetch the exact revision and record HEAD, blob hashes, Python versions, kernel and filesystem. Run `python3.11 -m unittest discover -s tests -v` and `python3.12 -m unittest discover -s tests -v` when available. Record actual stdout/stderr, exit, counts, skips and their reasons.
2. Independently assert read/stat/list/root containment for directory-to-outside-symlink swaps immediately before component opens. Probe actual no-follow semantics with/without O_DIRECTORY and distinguish real kernel behavior from mocks. Outside files contain synthetic sentinels only. Use asserting pass/fail exits; demonstrate old-fail/new-pass where old behavior is unsafe on your actual kernel. If old revision is contained on a conformant kernel, report it; do not fabricate an old failure.
3. Verify bounded reads with deterministic growth, root rename anchoring, FIFO timeout and repeated success/failure descriptor cleanup. Add independent checks for parent descriptor close errors after a child file/scan descriptor is acquired: does the child get released exactly once? Do not assume the committed tests cover this path.
4. Verify only the four reported d007 files differ from the old revision and MODULE-001 blobs remain unchanged. Keep tests outside the checkout where practical.

Deliver raw logs plus path-labeled independent check scripts, exact commit/environment, honest results and limits. Save evidence only to Dolesh-avkalan/drop under drive-tool/module-003/d009-independent-tests/ and final report d009-sarvam-independent-tests.md if your GitHub connector can write there. Record actual commit URL. If push blocked return PUSH_BLOCKED and complete artifacts; do not invent evidence. No edits to drive-tool branches or main. Do not start another module.
