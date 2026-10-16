# D107 final HTTP delta review

Exact production blob: 41279e6290617968c3b4f3f91fc3cf9a7dab505a. Gemini 4 Argon/high. Static review; tests not run by reviewer.

Verdict: ACCEPT (TESTS_NOT_RUN)

### Delta Verification (8cd137f..41279e6 in drive_tool/http_adapter.py)

- Caller-Budget Timed Lock in close():

- deadline = time.monotonic() + timeout_f is established before lock acquisition, ensuring a single end-to-end caller budget covers both _close_lock contention and subsequent worker/listener drain waits.

- Non-positive remaining cleanly routes to acquire(blocking=False) (avoiding threading.Lock.acquire negative-timeout errors), while positive remaining uses acquire(timeout=remaining).

- Lock release is strictly guarded by if not acquired: return False followed immediately by try ... finally: self._close_lock.release(), preventing unacquired-release exceptions, lock leaks, duplicate listener wakeups, or cross-thread FD closes.

- Phased Budget in _busy_reject() (10ms / 20ms / 20ms within 50ms Ceiling):

- Phase 1 (Pre-Read): Capped at min(now + 0.010, busy_deadline - 0.020), preventing an idle or slow-loris peer from exhausting the entire 50ms budget before the 503 response is written.

- Phase 2 (Write Reserve): Retains the guaranteed reserve inside busy_deadline to flush the static JSON 503 payload best-effort over the transport.

- Phase 3 (Post-Write Drain): Capped at min(busy_deadline, time.monotonic() + 0.020) and shares the cumulative total_read < MAX_BUSY_READ_BYTES (16 KiB) bound with Phase 1, preserving the 50ms outer ceiling and 16 KiB per-rejection memory/read cap.

Chat: https://gemini.google.com/app/73bf1f2b23f723b6
