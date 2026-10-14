# d010 — minimal MODULE-003 resource ownership and probe fixes

Director: Codex. Implementer/test executor: browser Sarvam. Do not begin until the director dispatches this brief after d009 is complete. Gemini d008's static review is CHANGES REQUIRED; its proposed code is illustrative, not an implementation to copy blindly.

Exact base: c2b324dab4cf720178a36a58b5f1cf1339730151 in Dolesh-avkalan/drive-tool.
New branch: feature/module-003-resource-fixes. Preserve prior branches and main.
Allowed paths: drive_tool/local.py, tests/test_local.py, new docs/evidence/MODULE-003-RESOURCE-FIXES.md only. No MODULE-001/002 edits, other modules, interfaces, caching/frameworks, dependencies, installation or deployment. Use synthetic fixtures in your sandbox.

## Director design and required behavior

1. Once an operation opens a child file or scan descriptor, it owns that descriptor immediately. Its cleanup must cover the parent-close handoff as well as subsequent type checks, reading and scanning. In list_children and read_bytes, parent-close failure after successful child acquisition currently exits before the child's cleanup scope starts. Fix the lifetime scope using simple explicit ownership; do not double-close an ambiguously closed parent, do not obscure the original LocalStorageError/cause, and close each acquired child exactly once. Keep root descriptor ownership intact. Do not rewrite unrelated traversal code.
2. The runtime no-follow probe must exercise the actual production open flags, not a separately reconstructed bitmask. Make the production helper(s) the source of truth. File and directory paths currently share the same safe flags; preserve this and test any distinction explicitly. No O_DIRECTORY-based weakening, lstat-then-open fallback, or cached successful result that masks later test mutation.
3. Split static platform prerequisites from runtime no-follow behavior in tests. The probe/platform-denial tests must run when static POSIX facilities exist, even if the runtime probe returns False. Operational containment tests may explicitly skip when the adapter is unsupported, with a precise reason. An unsupported kernel is a legitimate fail-closed outcome, not an automatic product defect: assert UnsupportedPlatformError, rather than require unsafe execution or unconditionally failing the whole suite on a correctly rejected platform. Separate actual enforced behavior, injected denial and static absence in evidence.

## Acceptance and meaningful regressions

- Before changing implementation, add independent regressions that reproduce child leaks on the exact base: read_bytes on p/a/file.txt and p/a/b/file.txt, and list_children on p/a/b and p/a/b/c. Fail the parent-close handoff after child acquisition as well as earlier traversal closes. Identify descriptors by actual ownership/acquisition rather than assuming reused integer fd values uniquely identify lifetimes. Assert every acquired child released exactly once, original failure/cause preserved, root remains usable/owned, no parent double-close.
- Model Linux close-error semantics accurately: inject an error after the real close where testing ownership transfer, and separately document behavior where close reports uncertainty. Do not retry an ambiguous descriptor. Do not claim release of a truly unclosed descriptor when the OS reports an ambiguous close error.
- Verify probe follows patched production flags: removing O_NOFOLLOW from the actual helper makes the probe detect following and constructor fail closed. Use real tiny symlink fixtures when possible. Probe tests themselves must not be skipped by the result they test.
- Run full committed suite with actual Python 3.11 and 3.12 if available; report missing versions honestly. Run deterministic root/read/stat/list swap checks, repeated descriptor cleanup and bounded-read/FIFO checks. Preserve all existing behavior/tests except correcting the self-fulfilling gate. Record actual counts, skips, stderr and exits.
- Demonstrate new regressions fail on c2b324d and pass on the fix. Include a targeted mutation that removes the new child cleanup or decouples the production flags and show the relevant regression catches it. Keep all mutation/base experiments in throwaway copies.

## Evidence and submission

Push the permitted files only to the new branch, with fixes/tests and evidence separated if practical. Never merge. Record exact base and resulting commit, blob hashes, Python/kernel/filesystem and commands. Use TESTS_NOT_RUN or PUSH_BLOCKED when accurate. Save final answer and raw logs/check scripts to Dolesh-avkalan/drop under drive-tool/module-003/d010-resource-fixes/ plus d010-sarvam-resource-fixes.md, if authorized connector capabilities allow it; return actual commit URLs. Only synthetic public artifacts.

Director acceptance and a fresh independent re-review/execution gate remain after submission. Do not declare production security, conformant cloud-kernel execution, or full Drive Tool completion.
