Status: PART_A_CLEAR_PENDING_B (TESTS_NOT_RUN)

- Reviewed Scope: harsh_checks_d110.py Part A (lines 1–943: frozen constants, helpers, and Stages 1–5) for target payload SHA256 5599d32fa9df7918a8809098492284cebbce42b9141c61844c18c069fc17db43 (paired with D113 outer runner f55f1ca20d79f576b32006e7a9745fd9baa4b2af8cf55e6b77cf62e541712c87 and immutable source 337e4c713829af76615472eb47b81244ff399528).

- Part A Blocking Findings: None (0 blockers). Whole-package verdict withheld pending Part B (Stages 6–8 and CLI/main).

### Part A Static Audit Summary (Lines 1–943)

- Containment, Loopback & Filesystem Bounds:

- Stdlib-only imports; no credentials, cloud APIs, privileged calls, or external network bindings.

- All HTTP listeners bind to ephemeral loopback (127.0.0.1, port=0), and _raw_exchange() connects exclusively to 127.0.0.1 with explicit socket timeouts and a 4 MiB response read cap.

- All fixtures and stage directories use tempfile (TMPDIR-confined under the outer runner), enforce MAX_FIXTURE_BYTES (32 MiB), and guarantee adapter.close(timeout=5.0) plus shutil.rmtree(..., ignore_errors=True) in finally blocks.

- Timeouts, Concurrency & Resource Hygiene:

- Every barrier (_EntryGate.release.wait(30.0)), thread join (20.0s–60.0s), client/socket exchange (1.0s–15.0s), and stage loop (admission_exhaustion budget deadline) is strictly time-bounded.

- Post-stage checks verify adapter.close(timeout=10.0) is True, zero lingering drive-http* threads (_adapter_threads()), and file descriptor stability (fd1 <= fd0 + FD_BASELINE_SLACK).

- Credible, Fail-Closed Stage Oracles (Stages 1–5):

- Stage 1 (selfcheck): Verifies frozen constants (MAX_CONCURRENT == 6, distinct 32–256 byte synthetic tokens, TEMP_PREFIX), _scan_temp() accuracy, /proc/meminfo MemAvailable readability, and fixture size limits.

- Stage 2 (http_hostile): Sends 15 malformed, oversize, duplicate-header, bare-LF, truncated, and slow-partial HTTP framing probes; fails on any probe exceeding 8.0s or 1 MiB response size, or if a subsequent valid DriveClient.list() recovery call fails.

- Stage 3 (admission_exhaustion): Uses deterministic _EntryGate(6) synchronization across 50 cycles; requires all 6 * cycles occupiers to return HTTP 200, all 1 * cycles excess requests to return code == "busy" (zero unavailable or other errors), zero barrier misses, and 60/60 post-overload recovery successes.

- Stage 4 (isolation): Verifies cross-agent read/write denial (code == "denied"), tests 9 path-traversal/NUL/reserved-temp/symlink-escape IDs at both client and raw server HTTP endpoints (/v1/read, /v1/list, /v1/save), and verifies the external sentinel file remains unmodified and unlisted.

- Stage 5 (concurrent_ops): Verifies atomic single-winner creation under overwrite=False (1 winner, 5 exists, 0 other), zero torn reads (len == 4096 and valid SHA-256) under concurrent 2-writer/3-reader overwrite contention, 6/6 multi-agent distinct creates, and zero orphan .drive-tool-tmp-* files.

Ready for Part B (lines 944 to end).
