# d008 — independent acceptance review of MODULE-003 correction

Director: Codex. Reviewer: Gemini 4 Argon. Review only; do not implement, execute untrusted links, push, merge, or deploy.

Pinned repository: https://github.com/Dolesh-avkalan/drive-tool
Correction: c2b324dab4cf720178a36a58b5f1cf1339730151, feature/module-003-review-fixes.
Old revision: 110b27e81cfead478eb6e400a44043f6e14f14b7.
Inline source and tests below are authoritative copies from the correction commit; public links are optional context. Read them rather than guessing unavailable files.

The director owns the design. It must stay small, standard-library only, modular, bounded, predictable and fail closed. Storage adapters must stay separate from listing, jobs, HTTP, OAuth and deployment. No feature expansion or speculative framework. This round accepts or rejects the read-only local adapter, not the entire product.

Prior review found a real directory/symlink swap escape on a gVisor kernel that ignored O_NOFOLLOW when combined with O_DIRECTORY. The worker now removes O_DIRECTORY, immediately fstat-checks opened descriptors, adds a runtime no-follow probe and adjusts descriptor/error handling. Worker reports 82 tests passing on Python 3.11/3.12, deterministic old-fail/new-pass race checks and descriptor-close mutation detection. Treat all execution as worker-reported, not independent proof.

Review the actual correction against the appended original contract and tests. Priorities:

1. Atomic containment during root establishment, intermediate traversal, stat, listing and final read; root anchoring after rename; correct platform gate. Identify remaining routes to outside bytes/metadata/listings under the documented threat model.
2. Descriptor ownership on every exception, especially cleanup of newly opened child descriptors if parent close fails; scan descriptors versus scandir's duplicate; close failure without double-close. Identify concrete leaks with exact paths rather than hypothetical concerns.
3. Bounded allocation/read behavior, FIFO nonblocking behavior, source-mismatch precedence, explicit errors and preserved causes, compatibility with MODULE-001.
4. Whether regression tests really assert safety and detect the former issue. Separate mocked mechanism tests from actual kernel evidence. Flag self-fulfilling test assumptions.
5. Simplicity and resource overhead: distinguish must-fix defects from optional cleanup. No sweeping rewrite unless needed for a demonstrated correctness problem. Hard links, mounts and operator-controlled root provisioning are explicit boundaries; do not mislabel excluded threats as a passed guarantee.

Return ACCEPT, ACCEPT WITH NITS, or CHANGES REQUIRED. For each blocking finding give file/function, concrete execution path/reproducer outline, minimal required fix and acceptance test. Report what you read, tests NOT RUN unless you truly executed them, residual uncertainty, and cloud-box acceptance gates. Keep your review bounded and actionable. No credentials, private files or production access.
