# D108 target runner security review

Gemini 4 Argon/high, static TESTS_NOT_RUN.

Verdict: ACCEPT (TESTS_NOT_RUN)

- Runner (run_target_http.py) SHA256: 4eaf7f530bb25646e2c58e61d05eded952fdf53172f2db212c4e43945d72fdbf

- Gate Harness (http_gate_d101.py) SHA256: 3754ca19b4a72797ac16a1e32300ddf399e7117e2dec63def6693d2512db8d7b

- Candidate Source Commit: 337e4c713829af76615472eb47b81244ff399528 (SOURCEHASH: b8b44c4c056e1925957445cc9b532c53194920124a469df54e49053a7dcbb71c)

- Blocking Findings: None (0 blockers).

### Static Security Audit Summary

- Workspace Containment & Environment Isolation:

- make_workspace() resolves paths via os.path.realpath, enforces that base_dir is /tmp or a descendant of /tmp, requires the workspace ws to be a strict descendant of base_dir (ws.startswith(base + os.sep)), enforces permissions 0700, and retains all artifacts without destructive cleanup.

- build_env() confines TMPDIR, TEMP, and TMP to <ws>/tmp, sets PYTHONDONTWRITEBYTECODE=1, PYTHONHASHSEED=0, and PYTHONNOUSERSITE=1, and replaces any inherited PYTHONPATH exclusively with the verified src directory.

- Identity & Payload Verification (Fail-Closed):

- verify_identities() walks the extracted tree using os.lstat before any target code executes, rejecting symlinks and non-regular files, enforcing exact byte size, Git blob SHA-1, and SHA-256 across all 27 pinned files in EXPECTED_BLOBS (including drive_tool/http_adapter.py @ 41279e6290617968c3b4f3f91fc3cf9a7dab505a and tests/test_partial_scan_review_fixes.py @ c01003e7015915db3dcac15a168ff7aae3d423db).

- Any missing pinned file, unexpected file under drive_tool/ or tests/, or unexpected importable/executable file anywhere in the archive (.py, .pyc, .pyo, .pth, .so, .sh, .bash, .pyw, sitecustomize.py, usercustomize.py) aborts execution with exit code 1.

- verify_gate_payload() verifies http_gate_d101.py against GATE_SHA256 (3754ca19b4a72797ac16a1e32300ddf399e7117e2dec63def6693d2512db8d7b) prior to execution.

- Archive Acquisition & Extraction Guards:

- acquire_archive() enforces MAX_ARCHIVE_BYTES (50 MiB) on both local and downloaded archives and bounds curl via --max-time and process-group timeout.

- safe_extract() enforces MAX_MEMBERS (2000), MAX_FILE_BYTES (5 MiB), and MAX_TOTAL_UNCOMPRESSED (64 MiB), rejects absolute paths, .. traversal components, symlinks, hardlinks, and special files, verifies os.path.realpath containment within the extraction root, and writes files/directories with 0600/0700 permissions.

- CLI, Subprocess & Process-Group Control:

- Stdlib-only execution with no shell invocation (shell=False), no network listener exposure outside the local loopback gate harness, and no credential/privilege escalation.

- run() launches children with start_new_session=True, drains stdout/stderr continuously to prevent pipe deadlocks while capping retained memory at MAX_SUBPROCESS_OUTPUT (8 MiB), and terminates the entire process group via os.killpg(..., signal.SIGKILL) on timeout.

- Resource, Time & Acceptance Propagation:

- Stage timeouts (--download-timeout 120, --suite-timeout 300, --gate-timeout 600) are clamped to the remaining --overall-deadline (1080s), operating inside the documented timeout 1200 outer operator bound in README.md.

- suite_ok() requires raw exit 0, zero timeouts/failures, ran == 581, len(skips) == 3, and exact membership in ALLOWED_SKIP_IDS; any failure halts prior to the gate stage.

- gate_ok() requires raw exit 0, result == "PASS", exact stage execution (selfcheck, smoke600, soak10000, saturation_6plus1), empty problems, zero unexpected_other_error / unexpected_unavailable, and exact bucket counts (580/20, 9680/320, 300/50).

- Post-run disk usage is audited via dir_allocated_size(ws) against MAX_WORKSPACE_BYTES (128 MiB), and any preflight, suite, gate, or resource violation strictly exits 1.

Chat: https://gemini.google.com/app/73bf1f2b23f723b6
