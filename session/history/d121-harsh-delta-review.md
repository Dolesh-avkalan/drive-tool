Verdict: CHANGES REQUIRED (TESTS_NOT_RUN)

- Baseline Reviewed: D117 accepted outer runner f55f1ca20d79f576b32006e7a9745fd9baa4b2af8cf55e6b77cf62e541712c87, original payload 5599d32fa9df7918a8809098492284cebbce42b9141c61844c18c069fc17db43, immutable source 337e4c713829af76615472eb47b81244ff399528.

- Delta Reviewed: D119 patch (d119_concurrent_lease_and_r2.patch at drop 248d1f9def06746a7947d814ff1f76a0a63906a1).

### Concrete Blockers in the Proposed Delta
#### 1. stage_concurrent_ops: Unhashable dict Crash on Any Writer Failure

- Finding: _exc_detail(exc, "overwrite") returns a dict, which writer(i) appends to write_err. However, lines 908 and 982 still execute sorted(set(write_err)). Whenever write_err is non-empty (the exact failure case _exc_detail was added to diagnose), set(write_err) raises TypeError: unhashable type: 'dict', crashing the stage into an unhandled ERROR instead of recording a clean FAIL with diagnostics and resource checks. (In repro_d and repro_e, write_err remained empty, so this code path was never exercised.)

- Minimal Correction: Guard write_err.append(...) with a lock/bound (or break on first writer error as before) and serialize/deduplicate write_err without passing dict objects into set(...) (e.g., deduplicate by a tuple key or store write_err[:5] + write_err_count matching torn_reads / torn_count).

#### 2. stage_concurrent_ops: CONC_LEASES = 5 Weakens 6-Way Races in Sub-Tests (a) and (c)

- Finding: Sub-test (a) (6 one-shot overwrite=False creators) and sub-test (c) (6 one-shot multi-agent creates) do not run reconnect loops; each thread sends exactly one request (6 == MAX_CONCURRENT). Wrapping (a) and (c) in _LeaseLimiter(capacity=5, hold=0.055) blocks the 6th thread until one of the first 5 completes its request and finishes its 55 ms post-response hold, turning a 6-way concurrent race into a 5-way race plus a delayed 6th request.

- Minimal Correction: Keep sub-tests (a) and (c) at full 6-way (MAX_CONCURRENT = 6) concurrency without the 5-slot cap; insert a >= 55 ms drain pause between sub-test (a), the target2 seed write, sub-test (b), and sub-test (c) so prior server workers finish _drain_and_close (POST_RESPONSE_DRAIN_SECONDS = 0.050), and apply _LeaseLimiter(CONC_LEASES=5, CONC_LEASE_HOLD=0.055) specifically to the looping threads in sub-test (b) (2 writers + 3 readers = 5 threads). Document that the 55 ms hold prevents client reconnect overlap with the server's 50 ms drain window under normal scheduling, not as a universal OS scheduler guarantee.

#### 3. stage_resource_pressure (R2): 96 MiB vs. 192 MiB Ballast Drift and Unchecked mem_error Oracle

- Finding:

1. Ballast Drift: Both the new code comment ("so its VmSize starts small and the 192 MiB ballast fits under the UNCHANGED 512 MiB RLIMIT_AS cap") and the D119 report ("after 192 MiB ballast = 228552 (< 512 MiB)") state a 192 MiB ballast matching headroom_kb = 192 * 1024, but _R2_CHILD_SRC allocates only ballast = bytearray(96 * 1024 * 1024) (96 MiB). Meanwhile, the per-op workload in R2 remains 50 iterations of 16 B read + 256 B save (pre-MemoryError) and 20 iterations of 256 B save + 16 B read (recovery).

2. Post-Cleanup vm_kb Reporting: _R2_CHILD_SRC changes "vm_kb" from the baseline vm_kb to _vmsize_kb() evaluated after del ballast, grow, hiding both baseline and peak VmSize under ballast.

3. Missing MemoryError Gate in Parent Oracle: stage_resource_pressure sets r2["result"] = "PASS" if (r2.get("ok") == 50 and r2.get("recovery_ok") == 20) else "FAIL" without checking r2.get("mem_error") == "MemoryError".

- Minimal Correction: Align _R2_CHILD_SRC ballast allocation with the documented 192 MiB contract (192 * 1024 * 1024, touched across pages; or explicitly justify 96 MiB in both the spec and comments without claiming 192 MiB), report both vm_kb_before and vm_kb_ballast (or vm_kb + vm_kb_peak), and require r2.get("mem_error") == "MemoryError" alongside ok == 50 and recovery_ok == 20 for r2["result"] == "PASS".

#### 4. stage_resource_pressure (R2): subprocess.Popen / os.waitpid Lifecycle Conflict, Process Group Isolation, and Dead Code

- Finding:

1. _R2_CHILD_SRC drops os.setsid() and subprocess.Popen omits start_new_session=True, leaving the child in the parent's process group.

2. Passing proc.pid to _collect_child() reaps the child via raw os.waitpid(proc.pid, ...) behind subprocess.Popen's back, leaving proc.returncode is None and causing Popen.__del__ to emit ResourceWarning: subprocess ... is still running and pollute subprocess._active.

3. If _collect_child() times out waiting on rfd, it still calls blocking os.waitpid(pid, 0) without killing the child process group first.

4. The original def _r2_child(...) is left behind as dead duplicate code.

- Minimal Correction: Start the clean-exec R2 child with start_new_session=True, reap via proc.wait() (or kill its process group on timeout before waiting) so subprocess.Popen tracks exit status cleanly, ensure _collect_child kills timed-out child process groups before blocking wait, and remove the dead _r2_child function.
