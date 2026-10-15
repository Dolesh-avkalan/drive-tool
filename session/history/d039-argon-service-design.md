Design Status: Design review brief only for pin e88c9c79fdc27057622d44ba51e008f2e4ea4f4f. No code, package installs, credential generation, deployments, or tests were executed.

## 1. Amendments to D016 Assumptions & Module Split

To keep the service honest against POSIX kernel behavior and the accepted MODULE-005A storage contract (e88c9c79fdc27057622d44ba51e008f2e4ea4f4f), D016 assumptions are amended as follows:

1. Range Reads Require an Adapter Contract Boundary (MODULE-005B): LocalAdapter.read_bytes buffers whole files up to max_read_bytes. Reading slices of larger permitted files without staging or duplicating anchored symlink/traversal checks requires a narrow adapter extension (read_range) that reuses the adapter’s existing openat/O_NOFOLLOW descriptor resolution.

2. Deadlines Are Response Detachments, Not Syscall Cancellation: Python threads cannot preempt a thread blocked in uninterruptible POSIX disk I/O (D state) or abort an in-flight write/fsync. When an operation deadline expires, the HTTP handler detaches and returns timeout, while the worker thread retains its concurrency slot until the kernel call returns.

3. Write Uncertainty (D018) Overrides Clean Rollback: Because LocalWriter.write_bytes can publish a file via atomic rename before a subsequent fsync failure or response timeout, save failures after staging begin carry "outcome_uncertain": true. Created intermediate parent directories are shared state and are never deleted on error.

4. Pagination Boundary: Offset pagination (offset, next_offset) is valid only for complete, bounded list calls backed by list_children. Prefix search (find) and budget-limited directory scans (scan_children) never emit continuation tokens.

### Implementation Split (Contract Boundaries Only)

| Module | Scope & Rationale | Allowed File Scope |
| --- | --- | --- |
| MODULE-005B (Pre-requisite) | Narrow LocalAdapter.read_range extension reusing internal anchored openat/O_NOFOLLOW helpers. | src/drive_tool/local_adapter.pytests/test_local_adapter_range.py |
| MODULE-006A (First Service Target) | Loopback HTTP/1.1 REST service (Python stdlib only; zero third-party runtime dependencies), policy engine, bounded worker/slot limiter, and thin local Python agent client. | src/drive_tool/service/__init__.pysrc/drive_tool/service/config.pysrc/drive_tool/service/policy.pysrc/drive_tool/service/limiter.pysrc/drive_tool/service/server.pysrc/drive_tool/service/client.pytests/test_service_api.pytests/test_service_soak.py |
| MODULE-006B (Deferred) | Thin MCP stdio/SSE tool wrapper over DriveServiceClient, durable request-ID idempotency store, and external TLS/reverse-proxy deployment config. | Deferred until MODULE-006A passes acceptance. |

## 2. Authoritative Design Resolutions (Questions 1–7)
### Q1. Source Configuration, Per-Agent Policy & Adapter Lifecycle

Callers reference configured source_id strings and relative POSIX paths (rel_path), never host paths.

```python
@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    root: Path
    max_read_bytes: int = 10_485_760   # 10 MiB adapter ceiling
    max_write_bytes: int = 10_485_760  # 10 MiB writer ceiling
    max_concurrent_ops: int = 4

@dataclass(frozen=True)
class SourceGrant:
    source_id: str
    allowed_prefixes: tuple[str, ...]  # Canonical relative prefixes; ("",) = whole source
    writable: bool = False
    max_read_window_bytes: int = 1_048_576  # 1 MiB per range call
    max_write_bytes: int = 10_485_760

@dataclass(frozen=True)
class AgentPolicy:
    agent_id: str
    token_sha256: bytes                # 32-byte digest compared via hmac.compare_digest
    grants: Mapping[str, SourceGrant]
    max_concurrent_ops: int = 2

@dataclass(frozen=True)
class AuthorizedContext:
    agent_id: str
    source_id: str
    grant: SourceGrant
    norm_rel_path: str                 # Normalized path verified inside allowed_prefixes

def authorize_request(
    policy: AgentPolicy,
    source_id: str,
    op: Literal["list", "find", "read", "save"],
    rel_path: str,
) -> AuthorizedContext: ...
```

- Validation & Non-Leakage:

- Rejects null bytes, leading /, . or .. segments, and backslashes with invalid before touching the filesystem.

- Segment-boundary prefix enforcement: a grant for "reports/2026" permits "reports/2026" and "reports/2026/q3.txt", never "reports/2026_private".

- No Metadata Leakage: If source_id is ungranted, rel_path falls outside allowed_prefixes, or op == "save" on a read-only grant, authorize_request raises denied immediately before any adapter call.

- Scoped Descendants & Hints: For find, the search root is norm_rel_path (which must itself lie inside an allowed_prefixes entry). If not_found occurs, any closest-name hint computation may only inspect entries inside the target's parent directory if and only if that parent directory is also within allowed_prefixes; otherwise hints are omitted (hints=()).

- Safe Adapter Drain: AdapterRegistry wraps each (LocalAdapter, LocalWriter) pair in a reference-counted lease (acquire_lease(source_id) -> ContextManager[SourceLease]). On shutdown, the registry sets draining = True (new leases raise unavailable) and waits for active leases to hit 0 before releasing resources.

### Q2. Bounded Concurrency, Deadlines & Slow-Client Bounds

```python
@dataclass(frozen=True)
class ServiceLimits:
    max_connections: int = 24
    listen_backlog: int = 16
    global_workers: int = 12
    per_agent_max_ops: int = 2
    per_source_max_ops: int = 4
    slot_acquire_timeout_ms: int = 200
    header_timeout_s: float = 2.0
    max_header_bytes: int = 8192
    max_headers: int = 32
    json_body_max_bytes: int = 65_536
    body_read_timeout_s: float = 5.0
    op_deadline_ms: int = 2000         # 3000 ms for find
```

- Slow-Header / Slow-Body Defense:

- A pre-handler connection semaphore (max_connections = 24) rejects excess sockets immediately.

- Socket read timeout is set to header_timeout_s = 2.0 during request line and header parsing (<= 8 KiB, <= 32 headers).

- Transfer-Encoding: chunked is rejected with invalid; exact Content-Length is required. Payloads exceeding route caps (64 KiB for JSON endpoints, min(grant.max_write_bytes, 10 MiB) for save) are rejected with too_large before reading the body stream.

- Request body reads enforce a wall-clock deadline (body_read_timeout_s = 5.0); expiration returns timeout and closes the socket (Connection: close).

- Worker Isolation & Stuck Syscall Reality:

- The HTTP thread acquires (global_slot, agent_slot, source_slot) within slot_acquire_timeout_ms = 200 (returning busy if any semaphore is full) and transfers slot ownership to the worker thread executing the adapter call.

- The HTTP thread waits on Future.result(timeout=op_deadline_ms / 1000). If the future times out, the HTTP thread immediately sends error="timeout" to the caller, while the worker thread retains the agent and source slots until the blocked OS syscall finishes. A stuck disk or wedged directory only exhausts that specific agent/source quota (busy) without starving other agents or leaking unbounded threads.

### Q3. Bounded Range Reads (MODULE-005B + MODULE-006A)

No second path resolver is permitted. MODULE-005B adds read_range directly to LocalAdapter, reusing its existing anchored openat/O_NOFOLLOW file-opening helper and fstat validation:

```python
# MODULE-005B: src/drive_tool/local_adapter.py
@dataclass(frozen=True)
class RangeReadResult:
    data: bytes
    offset: int
    bytes_read: int
    total_size: int
    eof: bool
    mtime_ns: int

class LocalAdapter:
    def read_range(
        self,
        rel_path: str,
        offset: int = 0,
        length: int = 262_144,  # Default 256 KiB, capped at self.max_read_bytes
    ) -> RangeReadResult: ...
```

- Semantics: Validates offset >= 0 and 1 <= length <= self.max_read_bytes (invalid otherwise). Opens target via the existing anchored no-follow descriptor helper, verifies regular file via fstat(fd), rejects offset > st_size with invalid (offset == st_size returns b"", eof=True), and reads via os.pread(fd, length, offset) until length bytes or EOF.

- Service Exposure (POST /v1/read): Accepts JSON { "source_id": str, "rel_path": str, "offset": int = 0, "length": int = 262144 } where length <= min(grant.max_read_window_bytes, 1_048_576). Success returns 200 OK with raw application/octet-stream bytes (no base64 inflation or disk staging) and response headers X-File-Size, X-Read-Offset, X-Bytes-Read, X-EOF (true/false), and X-Mtime-Ns.

### Q4. Saves, Missing-Parent Creation & Write Uncertainty (D018)

```python
@dataclass(frozen=True)
class SaveReply:
    source_id: str
    rel_path: str
    bytes_written: int
    created_parents: bool
    mtime_ns: int

def handle_save(
    ctx: AuthorizedContext,
    content: bytes,
    overwrite: bool = False,
    create_parents: bool = False,
) -> SaveReply: ...
```

- Defaults & Parent Creation: overwrite and create_parents default to False. If the target exists and overwrite=False, LocalWriter.write_bytes raises AlreadyExistsError -> exists. If the parent directory is missing and create_parents=False, returns not_found. When create_parents=True, missing ancestor segments strictly beneath the matched allowed_prefixes root are created top-down via LocalWriter.mkdir (max depth 16).

- No Shared-Parent Rollback: Created intermediate directories are never removed if a later mkdir or write_bytes fails.

- Uncertain Outcomes & Retry Policy:

- Per D018, LocalWriter.write_bytes can publish the destination entry before a post-rename fsync error, or a worker timeout can fire while the write finishes in the background.

- Whenever save fails with an I/O/durability error after staging starts or hits op_deadline_ms, the error envelope returns code="unavailable" or "timeout" with "outcome_uncertain": true and "retry_safe": false.

- Idempotency Decision: Durable request-ID idempotency is explicitly deferred. An in-memory cache across restarts is dishonest; callers receiving "outcome_uncertain": true must inspect target state via list or read before retrying.

### Q5. Search (find) and Folder Listing (list)

Both endpoints enforce default=20, 1 <= limit <= 100 (invalid outside range) and emit concise entry records (name, rel_path, kind, size, mtime_ns).

```python
@dataclass(frozen=True)
class ListReply:
    items: tuple[EntryMeta, ...]
    total_entries: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None
    complete: Literal[True]

@dataclass(frozen=True)
class FindReply:
    items: tuple[EntryMeta, ...]
    truncated: bool
    complete: bool
    reason: str | None
    inspected: int
    skipped: int
```

- list (POST /v1/list): Calls LocalAdapter.list_children(norm_rel_path) for full bounded directories, sorts by (name,), and slices items[offset : offset + limit]. If offset > len(all_items), returns invalid. If the folder exceeds list_children's cap, list returns error="too_large" (offset pagination is never synthesized over partial scans).

- find (POST /v1/find): Calls LocalAdapter.find (or scan_children for single-directory prefix scan when max_depth=1) using strict budget accounting (max_results=limit, max_inspected=2000). Any budget hit (truncated=True), exact scan budget exhaustion (ScanBatch.complete == False), or skipped entry/subtree (skipped > 0) sets complete=False. No offset or continuation token is returned.

### Q6. Transport, Error Taxonomy & Caller Paths

- Fixed 8-Code Error Envelope (JSON):
{ "ok": false, "error": { "code": "<fixed_code>", "message": str, "outcome_uncertain": bool, "retry_safe": bool, "hints": list[str] } }

- Codes & HTTP statuses: invalid (400), denied (401/403), not_found (404), exists (409), too_large (413), busy (429), unavailable (503), timeout (504).

- Four Agent Tools (REST Endpoints on 127.0.0.1:<port>):

1. POST /v1/list — JSON request/reply.

2. POST /v1/find — JSON request/reply.

3. POST /v1/read — JSON request -> 200 application/octet-stream + X-Drive-* headers (or JSON error envelope).

4. POST /v1/save — Query params ?source_id=...&rel_path=...&overwrite=false&create_parents=false + raw application/octet-stream body -> 200 JSON SaveReply.

- (Non-tool endpoint) GET /v1/health — Returns { "status": "ok"|"draining", "active_ops": int } with zero path/config disclosure.

- Caller Paths:

- Actual Agent Caller Path: Local agent worker processes on the same host instantiate DriveServiceClient(base_url="[http://127.0.0.1](http://127.0.0.1):...", token=os.environ["DRIVE_AGENT_TOKEN"]), exposing 4 methods (list, find, read, save) with concise structured return types.

- Simulated HTTP Test Clients: Raw socket and http.client harnesses used in tests to inject malformed headers, slow trickles, oversized bodies, and concurrent bursts.

- Remote Teammates & Public Transport: Loopback is inaccessible off-host. Remote browser teammates, TLS termination, and real storage roots require a separately reviewed deployment module.

### Q7. Evidence Gate & Pre-Declared Measurable Bounds

All thresholds are fixed prior to implementation:

1. Multi-Agent & Isolation Gate (tests/test_service_api.py):

- 6 synthetic agents (agent_1..agent_6) across 3 synthetic POSIX roots with read-only, read-write, and nested prefix grants.

- Pass Criteria: 100% rejection (denied) on cross-prefix access, .. traversal, symlink escape, write on read-only grant, and ungranted source_id. not_found closest-name hints return [] whenever parent lies outside grant prefix.

2. Auth & Protocol Abuse Gate:

- Missing/corrupt/wrong-agent bearer tokens -> 100% denied (401/403).

- Header > 8 KiB, header trickle > 2.0s, body trickle > 5.0s, save body > 10 MiB, limit > 100, offset < 0, or chunked encoding -> 100% deterministic invalid, too_large, or timeout.

3. Saturation, Slow Syscall & Write Uncertainty Gate:

- Inject a controlled barrier inside a wrapped test adapter holding agent_1 / src_slow for 2.5s (> 2.0s deadline). Verify agent_1 receives timeout at ~2.0s, agent_1's 3rd concurrent call fails fast with busy in < 250ms, and agent_2 on src_fast completes list/read with p99 < 50ms.

- Inject post-publish fsync OSError(EIO) in LocalWriter: verify response is unavailable with outcome_uncertain=True, retry_safe=False, and pre-created shared parent directories remain intact.

4. Two-Stage Soak, Leak, Redaction & Restart Gate (tests/test_service_soak.py):

- Stage 1 (Smoke Prerequisite): 600 operations across 6 concurrent agent clients (100 ops/agent across find, list, read, save, plus 20% fault/rejection cases). Must pass with 0 unexpected errors before Stage 2 runs.

- Stage 2 (10,000-Call Soak): 10,000 requests across 6 concurrent agent threads.

- Pre-Declared Quantitative Bounds:

- FD Leak: Open file descriptor count after server drain equals post-warmup baseline (delta_fds == 0).

- Temp File Leak: 0 leftover temporary staging files in any synthetic root after worker drain.

- Memory Growth: RSS increase between call 1,000 and call 10,000 <= 8 MiB; tracemalloc retained heap delta <= 512 KiB.

- Audit Redaction: 100% of audit records include request_id, agent_id, source_id, op, status, duration_ms, outcome_uncertain; 0 occurrences of bearer tokens, raw file bytes, or host root paths in logs or /v1/health.

- Clean Restart: Graceful shutdown during active load drains in-flight leases and rebinds 127.0.0.1:<port> immediately without EADDRINUSE or lingering worker threads.

## 3. Testable Guarantees vs. Honest Limitations

| Dimension | Testable Guarantee (Enforced by Code & Gate) | Honest Limitation (Explicit Non-Guarantee) |
| --- | --- | --- |
| Scope & Path Safety | All reads/writes/scans/hints are confined to configured source_id + allowed_prefixes using anchored O_NOFOLLOW resolution. | Does not prevent external host processes outside the service from mutating underlying POSIX directories concurrently. |
| Timeouts & Concurrency | HTTP replies return within op_deadline_ms + transport overhead; slow clients and excess per-agent/per-source concurrency are rejected via bounded semaphores. | A timed-out HTTP call does not kill an in-flight kernel filesystem syscall; that worker slot remains occupied until the OS call returns. |
| Save & Retry Safety | Default overwrite=False; failed/timed-out writes after staging start explicitly flag outcome_uncertain: true, retry_safe: false; shared parents are never deleted on rollback. | Blind retry on timeout or unavailable is unsafe (D018); durable request-ID idempotency across restarts is deferred. |
| Search & Listing | list offset pagination is exact for bounded folders; find reports strict inspected/skipped/complete accounting and caps items at <= 100. | No continuation cursor exists for truncated find queries or directories exceeding list_children caps. |
| Transport & Access | Authenticated loopback HTTP/1.1 with constant-time token verification and bounded memory per request. | Loopback-only (127.0.0.1); not directly reachable by remote browser teammates without a separate reviewed proxy/tunnel deployment. |
