Verdict: ACCEPT_PACKAGE (TESTS_NOT_RUN)

- Outer Runner (run_harsh_d110.py) SHA256: f55f1ca20d79f576b32006e7a9745fd9baa4b2af8cf55e6b77cf62e541712c87 (accepted in D113)

- Harsh Payload (harsh_checks_d110.py) SHA256: 5599d32fa9df7918a8809098492284cebbce42b9141c61844c18c069fc17db43 (75545 bytes at drop e96136c9ce73a50f0988c22f636f0a5738204ed6)

- Immutable Candidate Source Commit: 337e4c713829af76615472eb47b81244ff399528

- Designated Target Scope: Disposable instance-20261015-210506 only, TMPDIR-confined workspace, fixture-only paths, loopback-only sockets.

- Blocking Findings: None (0 blockers across Part A + Part B + outer runner).

### Part B & Full-Payload Static Security Audit Summary (Lines 944–END)

- Stage 6 (crash_kill) — Synchronized Child SIGKILL Ownership & Recovery Oracles:

- Process Group Safety: _crash_child() immediately calls os.setsid(). In _run_crash_case(), the parent compares child_pgid = os.getpgid(pid) against own_pgid = os.getpgid(0) before signaling: it invokes os.killpg(child_pgid, signal.SIGKILL) only when child_pgid != own_pgid, falling back to os.kill(pid, signal.SIGKILL) if setsid() has not yet completed. This prevents any pre-setsid() race from killing the parent runner or SSH session, and reaps the child via os.waitpid(pid, 0).

- Deterministic Crash Points & Integrity Verification: Exercises both C1 (mid-write after writing half the payload via writer._os_write) and C2 (post-fsync, pre-publish at writer._os_rename / writer._os_link) across overwrite=False and overwrite=True. Requires confirmed SIGKILL termination, at least one .drive-tool-tmp-* orphan proving an interrupted write, unmodified original target on overwrite=True, absent target on overwrite=False, and successful fresh-service read/list/find/save recovery.

- Stage 7 (resource_pressure) — Child-Only CPU / RAM / FD Bounds & Fork Hygiene:

- Single-Threaded Fork Ordering: _cpu_children(4) is forked before DriveHTTPAdapter starts, and adapter.close(timeout=10.0) (plus thread-reap verification) completes before _r2_child and _r3_child are forked, avoiding multi-threaded os.fork() lock hazards.

- R1 (CPU Pressure): Confined to 4 child processes with os.nice(15) and RLIMIT_CPU = (60, 60), guaranteed killed (SIGKILL) and reaped (os.waitpid) in finally. Requires 300/300 operations across 6 personas to succeed.

- R2 (Memory Pressure) & Honest PARTIAL Propagation: Guarded by host MemAvailable >= 1 GiB (HOST_MEM_FLOOR_KB) before launch and audited against HOST_MEM_ABORT_KB (512 MiB) afterward. Inside _r2_child, oom_score_adj is set to 1000 and RLIMIT_AS is capped at 512 MiB (CHILD_RLIMIT_AS). If host MemAvailable is below 1 GiB or child baseline VmSize lacks 192 MiB headroom under 512 MiB, R2 reports "SKIPPED", causing stage_resource_pressure to return "PARTIAL" (exit code 2, never PASS / exit 0).

- R3 (FD Exhaustion): Confined to a child process that lowers RLIMIT_NOFILE soft limit to min(hard, 24) and holds /dev/null descriptors until <= 3 remain free, verifying zero unmapped exceptions (raw == 0), >= 1 mapped ServiceError, and 20/20 post-restoration recovery operations.

- Stage 8 (enospc) — Kernel RLIMIT_FSIZE & 4-Phase Seam Injection (No Disk Filling):

- Sub-test 2A (_fsize_child): Uses RLIMIT_FSIZE = 8192 with SIGXFSZ ignored in an isolated child process to trigger a real kernel EFBIG mid-write on a 32 KiB save without filling the filesystem, requiring ServiceError and target_exists is False.

- Sub-test 2B (_inject_phase): Deterministically injects OSError(errno.ENOSPC, ...) across temp_create (_os_open), mid_write (_os_write), fsync (_os_fsync), and publish (_os_link / _os_rename) for both overwrite=False and overwrite=True, restoring all 5 writer seams immediately after the call. Requires code == "unavailable", preserved seed SHA-256 on overwrite=True, no published file on overwrite=False, 0 orphan temp files, no FD leak, and full same-instance recovery without automatic retries on uncertain writes.

- CLI, Output & Exit Code Contract (main):

- Enforces fixed --stage allowlist (STAGE_NAMES), wraps stage execution in try...except BaseException to report "ERROR" on unexpected exceptions, emits a single HARSH_JSON: line (and optional --json file), and exits 0 only on "PASS", 2 on "SKIPPED"/"PARTIAL", and 1 on "FAIL"/"ERROR".

### Residual Limitations (Non-Blocking, Documented)

1. Process-Crash vs. Physical Power-Loss Scope: Stage 6 SIGKILL proves user-space process-crash atomicity and recovery across write and link/rename boundaries, not physical power-loss durability across volatile hardware/hypervisor disk caches.

2. Environment-Dependent R2 Memory Sub-Stage: On environments where baseline Python VmSize exceeds 320 MiB or host MemAvailable < 1 GiB, R2 cleanly self-skips and degrades the stage result to PARTIAL (exit 2) rather than masking the skipped coverage as PASS.

3. Child Collection Timeout Reliance on Outer Runner: _collect_child() polls the pipe up to timeout before calling os.waitpid(pid, 0); if an isolated child in R2, R3, or fsize ever wedged in an uninterruptible state past the pipe deadline, process-group termination is enforced by the outer runner (run_harsh_d110.py) stage timeout.
