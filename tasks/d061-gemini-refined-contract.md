D061 architectural and security refinement is complete (TESTS_NOT_RUN), incorporating all seven director concerns and pinning the accepted D058 (14729120c0f32ee5d64c1daf93b27fa1b3270d6b) and D062 backend state with zero backend code changes.

## 1. D058 / D062 Backend Verdict & D061 Concern Resolutions

1. D058 / D062 Backend Behaviors (Verdict: Non-Blocking — Documented in Contract §5.3):

- Whitespace-only find query (not name_contains.strip()) → 400 invalid: Safe defense-in-depth consistent with D050; prevents degenerate scans after authentication and authorization.

- list with offset beyond directory entry count → 400 invalid: Matches D050 cursor pagination (offset=0 then server-returned next_offset).

- Default read length (262,144 B) exceeding a smaller grant.max_read_bytes → 413 too_large: Required by D050 pre-lease budget enforcement; callers using grants with max_read_bytes less than 262,144 must pass an explicit length within the grant cap.

2. Concern (1) — No Future-Pipelining Proof: Removed non-blocking socket probes and timing claims. Each connection processes at most one Content-Length-framed request, never parses or executes a second request, and always closes the socket. Only trailing bytes already present in the read buffer when \r\n\r\n or Content-Length completes are opportunistically rejected as 400 invalid before dispatch.

3. Concern (2) — Absolute Monotonic Reject/Drain Budgets & Best-Effort Early Envelopes: Replaced per-read timeouts with absolute monotonic deadlines (time.monotonic() + budget). On 7th-connection saturation, the accept thread never drains a 1 MiB body: it enforces an absolute BUSY_REJECT_BUDGET_SECONDS = 0.05 (50 ms) and MAX_BUSY_READ_BYTES = 16384 (16 KiB) cap. Early-rejection envelope delivery is explicitly documented as best-effort: if an oversized or stalled sender leaves unread bytes in SO_RCVBUF past the drain cap, the OS may emit TCP RST, which DriveClient safely maps to DriveClientError(code="unavailable", outcome_uncertain=True, retry_safe=False) on save.

4. Concern (3) — Bounded Cross-Platform accept() Wakeup: The listener socket uses ACCEPT_POLL_INTERVAL_SECONDS = 0.05 (50 ms) poll timeouts and close() performs a best-effort non-blocking loopback wake connection before closing the listener socket.

5. Concern (4) — Finite Timeouts & Single Shared close() Deadline: close(*, timeout: float = 5.0) -> bool rejects bool, non-numbers, negative values, NaN, and Infinity (not math.isfinite(timeout) or timeout < 0.0). A single deadline = time.monotonic() + float(timeout) governs shutdown with zero extra grace period. self._service.close(timeout=remaining) is called immediately after closing the listener—before joining threads—so LocalFileService enters _draining = True promptly and accept/worker joins cannot starve service drain. Active backend descriptors are never force-closed.

6. Concern (5) — Verified Read / Zero-Length / EOF Semantics: Removed redundant adapter re-derivation of eof_observed. Documented exact client/backend rules: requested length == 0 is rejected as 400 invalid; a 0-byte response body (Content-Length: 0, X-Drive-EOF-Observed: true) is valid when reading an empty file or at/past EOF with length at least 1; exact-fill windows carry X-Drive-EOF-Observed: false. The adapter validates only field types and the 1 MiB wire cap before serializing RangeReadResult verbatim.

7. Concern (6) — Explicit Lexical Header Gate vs. Service Authentication & Zero-FS-IO Proof: Clarified that header checks validate lexical framing only (Bearer  plus 32..256 visible ASCII chars) and do not authenticate whether a token is known. When body JSON or /v1/save query parsing fails, the adapter invokes the public LocalFileService method with file_id=None and candidate_source_id (str | None), accompanied by a line-by-line proof against file_service.py showing authentication and authorization run first and _parse_canonical_parts(None) halts with invalid before _acquire_binding_lease or any filesystem I/O.

8. Concern (7) — Exact Error & Save Uncertainty Matrix: Specified HTTP status, code, outcome_uncertain, and retry_safe across all failure points, distinguishing pre-operation wire/grant cap rejection (413 too_large, outcome_uncertain=False even on save) from post-lease save failures (outcome_uncertain=True, retry_safe=False).

## 2. Final Revised CONTRACT.md

```markdown
# CONTRACT.md — Bounded Loopback HTTP Adapter (D061)

**Status:** Final architecture and security contract (D061) for director adoption. Static specification only (`TESTS_NOT_RUN`).
**Scope:** Connect the frozen, accepted loopback HTTP client (`drive_tool/service_client.py`) to the accepted synchronous local file-service layer (`drive_tool/file_service.py`) through a single dependency-free standard-library module `drive_tool/http_adapter.py`.

---

## 0. Source Pins & Change Boundaries

| Artefact | Repository @ Commit | Path | Status |
|---|---|---|---|
| Accepted HTTP Client (D047) | `Dolesh-avkalan/drive-tool` @ `cbf60867a0ca6e10206d540aed93f40e24b44a5d` | `drive_tool/service_client.py` | **Frozen — zero changes** |
| Storage & `RangeReadResult` | `Dolesh-avkalan/drive-tool` @ `cbf60867a0ca6e10206d540aed93f40e24b44a5d` | `drive_tool/local.py`, `drive_tool/storage.py` | **Frozen — zero changes** |
| Service Contract (D050) | `Dolesh-avkalan/drive-tool` @ `8eac68b98481b86e7ae164a5835063584c15fdcb` | `tasks/d050-file-service-implementation.md` | **Frozen reference** |
| Accepted Backend (D058 / D062) | `Dolesh-avkalan/drive-tool` @ `14729120c0f32ee5d64c1daf93b27fa1b3270d6b` (matches drop `8da43959e93d36649add7422a256b7688d70c84e`) | `drive_tool/file_service.py` | **Frozen — zero changes** |
| Bounded HTTP Adapter | Target repo (`drive-tool`) | `drive_tool/http_adapter.py` | **Single new production module** |
| Adapter Conformance & Soak Tests | Target repo (`drive-tool`) | `tests/test_http_adapter.py` | **New synthetic test module** |

No third-party libraries, web frameworks, `asyncio`, or `http.server.BaseHTTPRequestHandler` subclasses may be used. Implement directly over Python standard library `socket`, `threading`, `time`, `math`, `json`, `re`, and `urllib.parse`.

---

## 1. Topology, Ownership & Public API (`drive_tool/http_adapter.py`)

### 1.1 Ownership Split
1. **`DriveHTTPAdapter` (`drive_tool/http_adapter.py`) owns:**
   - The IPv4 loopback listening socket bound strictly to `127.0.0.1:<port>`.
   - 1 dedicated accept/admission thread and 6 fixed operation worker threads.
   - Bounded HTTP/1.1 framing, lexical header validation, bounded body reads under absolute monotonic deadlines, response serialization, and ordered shutdown coordination.
2. **`LocalFileService` (`drive_tool/file_service.py`) owns:**
   - Token authentication (`PolicyTable.authenticate`), grant authorization (`PolicyTable.authorize`), per-`(agent_id, alias)` `LocalAdapter`/`LocalWriter` bindings, active operation leases, path containment, and service drain.
3. **Isolation Rule:** `drive_tool/http_adapter.py` must never import or touch `LocalAdapter`, `LocalWriter`, `ListingService`, `search`, or `PolicyTable`, must never inspect or resolve filesystem paths, and must never perform filesystem I/O.

### 1.2 Public Module Exports & Class Signature
```python
__all__ = [
    "DriveHTTPAdapter",
    "HOST",
    "LISTEN_BACKLOG",
    "MAX_IN_FLIGHT_OPERATIONS",
    "MAX_REQUEST_LINE_BYTES",
    "MAX_HEADER_BLOCK_BYTES",
    "MAX_HEADER_COUNT",
    "MAX_JSON_BODY_BYTES",
    "MAX_SAVE_BODY_BYTES",
    "MAX_READ_BODY_BYTES",
    "DEFAULT_SOCKET_TIMEOUT_SECONDS",
    "DEFAULT_DRAIN_TIMEOUT_SECONDS",
    "ACCEPT_POLL_INTERVAL_SECONDS",
    "BUSY_REJECT_BUDGET_SECONDS",
    "MAX_BUSY_READ_BYTES",
    "POST_RESPONSE_DRAIN_SECONDS",
    "MAX_DRAIN_BYTES",
]
```

```python
class DriveHTTPAdapter:
    """Bounded loopback HTTP/1.1 adapter over LocalFileService."""

    def __init__(
        self,
        policies: tuple[AgentPolicy, ...] | None = None,
        *,
        service: LocalFileService | None = None,
        port: int = 0,
        socket_timeout: float = 5.0,
    ) -> None: ...

    @property
    def host(self) -> str: ...          # Always "127.0.0.1"

    @property
    def port(self) -> int: ...          # Bound TCP port in 1..65535

    @property
    def socket_timeout(self) -> float: ...

    @property
    def service(self) -> LocalFileService: ...

    def close(self, *, timeout: float = 5.0) -> bool: ...

    def __repr__(self) -> str: ...
```

### 1.3 Constructor Validation & Rollback

1. Service / Policies: Exactly one of policies or service must be non-None (providing both or neither raises ValueError). If service is passed, isinstance(service, LocalFileService) must hold (else TypeError).

2. Port: port must be a non-bool int in 0..65535 (non-int or bool raises TypeError; out-of-range raises ValueError).

3. Socket Timeout: socket_timeout must be a non-bool int | float with math.isfinite(socket_timeout) and socket_timeout > 0.0 (non-number or bool raises TypeError; <= 0.0, NaN, or Infinity raises ValueError).

4. Startup & Rollback:

- Step 1: If policies is provided, instantiate LocalFileService(policies).

- Step 2: Open an AF_INET, SOCK_STREAM socket (never set SO_REUSEPORT), configure settimeout(ACCEPT_POLL_INTERVAL_SECONDS), bind to ("127.0.0.1", port), call listen(LISTEN_BACKLOG), and read getsockname()[1].

- Step 3: Start the 6 worker threads and 1 accept thread.

- Rollback: If Step 2 or Step 3 raises any BaseException, close the listener socket, stop any started threads, call service.close(timeout=5.0), and re-raise the original exception.

## 2. Exact Constants & Bounds

| Constant | Value | Purpose |
| --- | --- | --- |
| HOST | "127.0.0.1" | Mandatory IPv4 loopback bind address |
| MAX_IN_FLIGHT_OPERATIONS | 6 | Fixed worker thread count and max concurrent operations |
| LISTEN_BACKLOG | 16 | Kernel listen() backlog argument |
| MAX_REQUEST_LINE_BYTES | 8192 | Max request line length including \r\n |
| MAX_HEADER_BLOCK_BYTES | 16384 | Max cumulative header bytes from first header to \r\n\r\n |
| MAX_HEADER_COUNT | 64 | Max number of HTTP header lines per request |
| MAX_JSON_BODY_BYTES | 65536 | Max request/response JSON body bytes (DriveClient._MAX_JSON_BODY) |
| MAX_SAVE_BODY_BYTES | 1048576 | Max /v1/save request body bytes (DriveClient._MAX_SAVE_BYTES) |
| MAX_READ_BODY_BYTES | 1048576 | Max /v1/read response body bytes (SERVICE_MAX_READ_BYTES) |
| DEFAULT_SOCKET_TIMEOUT_SECONDS | 5.0 | Default per-request socket deadline budget |
| DEFAULT_DRAIN_TIMEOUT_SECONDS | 5.0 | Default shutdown/drain deadline budget |
| ACCEPT_POLL_INTERVAL_SECONDS | 0.05 | Periodic accept() wakeup interval to check _draining |
| BUSY_REJECT_BUDGET_SECONDS | 0.05 | Absolute monotonic budget for inline 503 busy rejection |
| MAX_BUSY_READ_BYTES | 16384 | Max total bytes read/drained by accept thread on 503 busy |
| POST_RESPONSE_DRAIN_SECONDS | 0.05 | Absolute monotonic budget for post-response SHUT_WR drain |
| MAX_DRAIN_BYTES | 65536 | Max unread request bytes drained by a worker after response write |

## 3. HTTP Framing, Lexical Header Rules & Single-Request-Per-Connection Semantics
### 3.1 Absolute Request Read Deadline

When a worker picks up a client socket conn, it establishes an absolute monotonic deadline request_deadline = time.monotonic() + self._socket_timeout for reading the entire HTTP request (request line + headers + Content-Length body). Before every conn.recv(...), the worker computes remaining = request_deadline - time.monotonic():

- If remaining <= 0.0 (or socket.timeout / TimeoutError fires): best-effort send 504 Gateway Timeout (code="timeout", outcome_uncertain=False, retry_safe=True) and close conn without calling LocalFileService.

- Otherwise set conn.settimeout(remaining) before calling conn.recv(...).
A slow-header or slow-body peer trickling 1 byte at a time is strictly terminated at request_deadline.

### 3.2 Request Line Parsing

1. Read until the first \r\n with len(request_line_with_crlf) <= MAX_REQUEST_LINE_BYTES (8192):

- EOF at 0 bytes read or before \r\n: close socket without calling LocalFileService.

- Over 8192 bytes without \r\n, bare \n without \r, or any byte outside visible ASCII + space (0x20..0x7E) before \r\n: respond 400 invalid and close.

2. Split on single ASCII space 0x20 into exactly 3 fields [method, target, version]:

- method must be "POST", version must be "HTTP/1.1", and target must not contain #.

- target must be one of:

- Exact "/v1/list", "/v1/find", or "/v1/read" (no ? query string permitted).

- "/v1/save?<query>" with non-empty <query> ("/v1/save" or "/v1/save?" $\rightarrow$ 400 invalid).

- Any mismatch $\rightarrow$ 400 invalid.

### 3.3 Header Block Parsing

1. Read header lines ending in \r\n up to the terminating empty line \r\n:

- Total header block bytes (including final \r\n\r\n) must be <= MAX_HEADER_BLOCK_BYTES (16384).

- Number of non-empty header lines must be <= MAX_HEADER_COUNT (64).

- Bare \n, obs-fold lines starting with SP/HTAB, or control/non-ASCII bytes (0x00..0x1F except HTAB 0x09 in values, or >= 0x7F) $\rightarrow$ 400 invalid.

2. Each header line must have a non-empty RFC 9110 tchar field-name, zero whitespace before :, and optional leading/trailing SP/HTAB stripped from the field-value.

### 3.4 Deterministic Pre-Body Header Checks

Evaluated in exact order before reading the request body:

1. Transfer-Encoding: Any occurrence $\rightarrow$ 400 invalid (outcome_uncertain=False, retry_safe=False).

2. Expect: Any occurrence $\rightarrow$ 400 invalid (never send 100 Continue).

3. Host: Must appear exactly once with a non-empty value (else 400 invalid).

4. Connection: If present, must appear exactly once with lowercase value "close" (duplicate or any other value $\rightarrow$ 400 invalid).

5. Content-Length: Must appear exactly once and match ^[0-9]+$ (missing, duplicate, signed, or non-digit $\rightarrow$ 400 invalid). Parse as content_length = int(raw, 10).

6. Content-Type: Must appear exactly once (missing or duplicate $\rightarrow$ 400 invalid):

- /v1/list, /v1/find, /v1/read: lowercase value must be exact "application/json" (no ; parameters; else 400 invalid).

- /v1/save: lowercase value must be exact "application/octet-stream" (no ; parameters; else 400 invalid).

7. Lexical Authorization Header Framing Check:

- Note: This step validates wire header framing and token character syntax only; it does not authenticate whether the token belongs to a configured agent (LocalFileService performs authentication).

- Authorization must appear exactly once and match exact case-sensitive prefix "Bearer " followed by 32..256 visible ASCII characters (^Bearer ([\x21-\x7e]{32,256})$).

- Missing, duplicate, non-Bearer, or lexically invalid token $\rightarrow$ 403 denied (outcome_uncertain=False, retry_safe=False) before reading the body.

8. Wire Payload Size Gate:

- For /v1/list, /v1/find, /v1/read: if content_length > MAX_JSON_BODY_BYTES (65536) $\rightarrow$ 413 too_large (outcome_uncertain=False, retry_safe=False) before reading the body.

- For /v1/save: if content_length > MAX_SAVE_BODY_BYTES (1048576) $\rightarrow$ 413 too_large (outcome_uncertain=False, retry_safe=False) before reading the body.

### 3.5 Bounded Body Read & Single-Request-Per-Connection Rule

1. Exact Body Read: Using any body bytes already read into the header buffer after \r\n\r\n, continue reading from conn under request_deadline until content_length body bytes have been collected:

- If the peer closes/resets the socket before content_length bytes arrive (truncated/short body): close conn immediately without calling LocalFileService.

- If request_deadline expires before content_length bytes arrive: best-effort send 504 timeout (outcome_uncertain=False, retry_safe=True) and close conn without calling LocalFileService.

2. One Request Per Connection (No Pipelining Execution):

- TCP stream timing cannot prove that a client will not transmit future pipelined bytes after content_length. The adapter makes no timing-dependent promise about bytes not yet received.

- Instead, the adapter enforces two strict invariants:

1. If the buffered recv() that completed the header block or body read already contains > content_length bytes beyond \r\n\r\n, reject immediately with 400 invalid (outcome_uncertain=False, retry_safe=False) without calling LocalFileService.

2. The adapter never parses or executes a second request on any connection; after handling the single framed request, it always sends Connection: close and closes the socket.

## 4. Payload Parsing, Auth-First Public Service Delegation & Zero-FS-IO Proof
### 4.1 Why Public Delegation with file_id=None Is Used

LocalFileService (14729120c0f32ee5d64c1daf93b27fa1b3270d6b) is frozen and exposes no public auth-only method; PolicyTable is encapsulated inside self._policy_table. To ensure an unauthenticated token or an unauthorized (token, source_id, op) request never receives 400 invalid when its JSON body or query string is malformed—and never triggers filesystem I/O—the adapter always delegates through the four public LocalFileService methods (list, find, read, save).

### 4.2 Exact Payload Parsing Rules

1. POST /v1/list, POST /v1/find, POST /v1/read (JSON Body):

- Reject UTF-8 BOM (body.startswith(b"\xef\xbb\xbf")) and invalid UTF-8 (body.decode("utf-8", errors="strict")).

- Parse with json.loads(text, object_pairs_hook=_no_dup_keys, parse_constant=_no_nan_inf) where duplicate keys in any JSON object or NaN/Infinity/-Infinity raise ValueError.

- Top-level value must be a dict.

- Extract candidate_source_id = obj.get("source_id") if isinstance(obj, dict) and isinstance(obj.get("source_id"), str) else None.

- Validate exact key set per endpoint:

- /v1/list: required {"source_id", "file_id", "limit", "offset"}, optional "name_contains" (if present, value must not be None), no extra keys.

- /v1/find: exact keys {"source_id", "file_id", "name_contains", "limit", "max_entries", "max_depth"}.

- /v1/read: exact keys {"source_id", "file_id", "offset", "length"}.

2. POST /v1/save?<query> (Query Controls + Raw Bytes Body):

- Parse <query> with urllib.parse.parse_qsl(query, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict").

- If parse_qsl succeeds and "source_id" appears exactly once in the key list, set candidate_source_id to that string value; otherwise candidate_source_id = None.

- Full query validity requires the exact four keys {"source_id", "file_id", "overwrite", "create_parents"} each appearing once (no duplicates, no missing keys, no extra keys such as token/authorization/bearer), with overwrite in {"true", "false"} and create_parents in {"true", "false"}.

### 4.3 Exact Public Calls on Valid vs. Invalid Payloads & Proof of Zero Filesystem I/O

1. When payload/query syntax and key sets are valid:

- /v1/list: service.list(token, obj["source_id"], obj["file_id"], limit=obj["limit"], offset=obj["offset"], name_contains=obj.get("name_contains"))

- /v1/find: service.find(token, obj["source_id"], obj["file_id"], name_contains=obj["name_contains"], limit=obj["limit"], max_entries=obj["max_entries"], max_depth=obj["max_depth"])

- /v1/read: service.read(token, obj["source_id"], obj["file_id"], offset=obj["offset"], length=obj["length"])

- /v1/save: service.save(token, q["source_id"], q["file_id"], body, overwrite=(q["overwrite"] == "true"), create_parents=(q["create_parents"] == "true"))

2. When JSON or /v1/save query parsing fails (invalid UTF-8, invalid JSON, duplicate keys, missing/extra keys, invalid overwrite/create_parents):
Invoke the corresponding public method with file_id=None:

- /v1/list: service.list(token, candidate_source_id, None)  (passing None as the 3rd positional argument file_id)

- /v1/find: service.find(token, candidate_source_id, None, name_contains="a")

- /v1/read: service.read(token, candidate_source_id, None)

- /v1/save: service.save(token, candidate_source_id, None, b"")

3. Line-by-Line Proof Against drive_tool/file_service.py (14729120c0f32ee5d64c1daf93b27fa1b3270d6b) That file_id=None Never Performs Filesystem I/O:

- Every operation (list, find, read, save) executes agent, grant = self._authenticate_and_authorize(token, source_id, "<op>") as its first statement:

1. self._ensure_not_draining() runs first: if draining or closed, raises ServiceError("unavailable", outcome_uncertain=False, retry_safe=True).

2. self._policy_table.authenticate(token) runs second (before inspecting source_id): if token is unknown, raises PolicyDeniedError $\rightarrow$ ServiceError("denied", outcome_uncertain=False, retry_safe=False).

3. self._policy_table.authorize(agent.agent_id, source_id, "<op>") runs third:

- If candidate_source_id is None or lexically invalid, raises TypeError/ValueError $\rightarrow$ ServiceError("invalid", outcome_uncertain=False, retry_safe=False).

- If candidate_source_id is lexically valid (e.g. "docs") and unauthorized for (agent.agent_id, source_id, "<op>"), raises PolicyDeniedError $\rightarrow$ ServiceError("denied", outcome_uncertain=False, retry_safe=False).

- The second statement of every operation is parts = _parse_canonical_parts(file_id, allow_root=...):
4. The first check in _parse_canonical_parts is if not isinstance(file_id, str): _raise_service_error("invalid"). When file_id is None, it unconditionally raises ServiceError("invalid", outcome_uncertain=False, retry_safe=False) before self._acquire_binding_lease(...) and before any LocalAdapter or LocalWriter method can ever be called.

## 5. Response Contract, Read/EOF Semantics & Error Flags
### 5.1 JSON Success Responses (list, find, save)

- Encode {"ok": True, "result": result} via json.dumps(envelope, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8").

- Outbound Size Check:

- If len(raw_json) > MAX_JSON_BODY_BYTES (65536): do not send 200 OK; instead send 413 Payload Too Large with code="too_large".

- For list and find, set outcome_uncertain=False, retry_safe=False.

- For save (where LocalFileService.save already executed before response serialization), set outcome_uncertain=True, retry_safe=False.

- Otherwise send HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: <len(raw_json)>\r\nConnection: close\r\n\r\n followed by raw_json.

### 5.2 Binary Read Success Response (read) & Zero-Length / EOF Semantics

1. Verified Client & Backend Read/EOF Rules:

- Zero-length request (length == 0) is invalid: Both DriveClient.read and LocalFileService.read require 1 <= length <= 1048576 (further capped by grant.max_read_bytes). A request with length == 0 is rejected by LocalFileService.read with ServiceError("invalid", outcome_uncertain=False, retry_safe=False).

- Zero-length response body (len(result.data) == 0) is valid at/past EOF: Reading an empty file (0 bytes) or reading at offset >= file_size with length >= 1 returns result.data == b"" (Content-Length: 0) and result.eof_observed == True, which DriveClient._build_read_reply accepts (received == 0 < request_length and eof_observed is True).

- Short window vs. exact-fill window: LocalAdapter.read_range sets eof_observed = True iff len(result.data) < length (EOF was encountered inside the requested window) and eof_observed = False whenever len(result.data) == length (even if offset + length == observed_size).

- Adapter Serialization Rule: The adapter does not re-derive eof_observed or enforce len(data) < length. It verifies only that type(res.data) is bytes, len(res.data) <= MAX_READ_BODY_BYTES, type(res.offset) is int and res.offset >= 0, type(res.observed_size) is int and res.observed_size >= 0, type(res.observed_mtime_ns) is int and res.observed_mtime_ns >= 0, and type(res.eof_observed) is bool (returning 503 unavailable, outcome_uncertain=False, retry_safe=True if violated), and serializes those exact fields into the single response headers:

- Content-Type: application/octet-stream

- Content-Length: <len(res.data)>

- X-Drive-Offset: <decimal res.offset>

- X-Drive-Observed-Size: <decimal res.observed_size>

- X-Drive-Observed-Mtime-Ns: <decimal res.observed_mtime_ns>

- X-Drive-EOF-Observed: <"true" if res.eof_observed else "false">

- Connection: close

### 5.3 Pinned D058 / D062 Backend Behavioral Invariants (Documented for Callers & Tests)

1. Whitespace-only find name_contains: LocalFileService.find rejects whitespace-only name_contains (e.g. "   ") with 400 invalid (outcome_uncertain=False, retry_safe=False).

2. Out-of-range list offset: LocalFileService.list rejects a positive offset beyond the directory entry count with 400 invalid (outcome_uncertain=False, retry_safe=False). Callers must paginate using the returned next_offset (int | None).

3. Default read length vs. small grant max_read_bytes: DriveClient.read defaults to length=262144 (256 KiB). When calling read against a grant with max_read_bytes < 262144, callers must specify length <= grant.max_read_bytes; otherwise LocalFileService.read rejects the request before filesystem access with 413 too_large (outcome_uncertain=False, retry_safe=False), even if the target file is smaller than max_read_bytes.

### 5.4 Complete Error Envelope & Save Uncertainty Matrix

Every non-200 response emits Content-Type: application/json, exact Content-Length, Connection: close, and body:

```json
{"ok":false,"error":{"code":"<code>","outcome_uncertain":<bool>,"retry_safe":<bool>}}
```

| Failure Origin & Condition | code | HTTP Status | outcome_uncertain (list/find/read) | outcome_uncertain (save) | retry_safe |
| --- | --- | --- | --- | --- | --- |
| Pre-dispatch framing/header/query/JSON error or ServiceError("invalid") | invalid | 400 Bad Request | False | False (pre-lease) / True (post-lease) | False |
| Missing/malformed Authorization or ServiceError("denied") | denied | 403 Forbidden | False | False (pre-lease) / True (post-lease) | False |
| ServiceError("not_found") | not_found | 404 Not Found | False | True (raised after lease in save) | False |
| ServiceError("exists") | exists | 409 Conflict | False | True (raised after lease in save) | False |
| Pre-operation wire cap (Content-Length > cap) or pre-lease grant cap (len(data) > grant.max_write_bytes, length > read_cap) | too_large | 413 Payload Too Large | False | False (operation not started) | False |
| Post-lease WriteLimitExceededError in save or outbound save JSON > 65536 B | too_large | 413 Payload Too Large | False | True (write started/completed) | False |
| Adapter 7th-connection saturation rejection (before operation) | busy | 503 Service Unavailable | False | False | True |
| Service draining/closed before lease (ServiceError("unavailable")) | unavailable | 503 Service Unavailable | False | False | True |
| Post-lease StorageError/OSError in save or unexpected non-ServiceError exception in save | unavailable | 503 Service Unavailable | False | True | False (True on read-only ops) |
| Pre-dispatch request socket read timeout | timeout | 504 Gateway Timeout | False | False | True |

When catching ServiceError from LocalFileService, the adapter copies exc.code, exc.outcome_uncertain, and exc.retry_safe verbatim.

## 6. Concurrency, 6-Slot Admission, Best-Effort Drain & Uncertain Save
### 6.1 Fixed Thread Topology & Bounded 503 busy Rejection

1. 7 Fixed Threads Total: 1 accept thread + 6 operation worker threads (MAX_IN_FLIGHT_OPERATIONS = 6), created once in __init__. Zero per-connection threads and zero request waiting queue.

2. O(1) Worker Handoff vs. Inline Busy Rejection:

- When listener.accept() returns conn:

- Under the pool lock, if _draining is True, close conn immediately.

- If an idle worker is available, pop the idle worker and hand conn to it.

- If all 6 workers are busy (7th concurrent connection): the accept thread handles conn inline under a strict absolute monotonic deadline busy_deadline = time.monotonic() + BUSY_REJECT_BUDGET_SECONDS (0.05s):

1. Perform at most one bounded read of up to MAX_BUSY_READ_BYTES (16384 bytes) within busy_deadline to absorb standard request headers/small bodies. Never loop to drain a 1 MiB body on the accept thread.

2. Send the pre-encoded 503 Service Unavailable response with {"ok":false,"error":{"code":"busy","outcome_uncertain":false,"retry_safe":true}} within busy_deadline.

3. Call conn.shutdown(socket.SHUT_WR), read/discard up to the remaining MAX_BUSY_READ_BYTES budget while time.monotonic() < busy_deadline, and call conn.close().

- Because busy_deadline is an absolute 50 ms monotonic wall-clock cap and total bytes read on the accept path are capped at 16 KiB, a slow or 1 MiB attacker trickle cannot stall the accept loop beyond 50 ms per rejected connection.

### 6.2 Best-Effort Post-Response Teardown & Honest Transport Semantics

1. Worker Post-Response Teardown: After writing any response on a worker thread, the worker establishes an absolute monotonic deadline drain_deadline = time.monotonic() + POST_RESPONSE_DRAIN_SECONDS (0.05s), calls conn.shutdown(socket.SHUT_WR), reads and discards at most MAX_DRAIN_BYTES (65536 bytes) in a loop bounded by time.monotonic() < drain_deadline, and then calls conn.close().

2. Best-Effort Envelope Delivery Caveat: For well-formed requests and payloads up to 64 KiB, this teardown prevents TCP RST and delivers the structured JSON error envelope to DriveClient. However, bounded early rejection cannot guarantee error-envelope delivery when a sender transmits an oversized payload (> 64 KiB unread bytes remaining in SO_RCVBUF) or stalls past 0.05s. In that case, closing the socket with unread bytes may cause the OS TCP stack to emit RST, which DriveClient safely maps to DriveClientError(code="unavailable", outcome_uncertain=True, retry_safe=False) on save (and outcome_uncertain=False, retry_safe=True on read-only ops).

3. Save Disconnect Rules:

- Disconnect before all Content-Length bytes arrive $\rightarrow$ LocalFileService.save is not called; zero filesystem side effects occur.

- Disconnect during or after LocalFileService.save $\rightarrow$ the adapter never rolls back, deletes, or retries; any committed write/parent creation stands on disk, and the client observes outcome_uncertain=True, retry_safe=False.

## 7. Shutdown, Bounded accept() Wakeup & Shared Drain Deadline
### 7.1 Bounded accept() Loop Wakeup

- The listening socket uses listener.settimeout(ACCEPT_POLL_INTERVAL_SECONDS) (0.05s).

- In the accept thread loop, socket.timeout simply loops and re-checks self._draining.

- During close(), after setting self._draining = True, the caller performs a best-effort non-blocking loopback connect to ("127.0.0.1", self._port) (immediately closing the wake socket) before calling listener.close(), ensuring accept() wakes promptly across all platforms and never blocks longer than 50 ms even if listener.close() does not wake a blocked accept() syscall on that OS.

### 7.2 Single Monotonic close() Sequence

DriveHTTPAdapter.close(*, timeout: float = 5.0) -> bool:

1. Validate timeout: If type(timeout) is bool or not isinstance(timeout, (int, float)) or not math.isfinite(float(timeout)) or float(timeout) < 0.0, raise ValueError("timeout must be a finite non-negative number").

2. Single Shared Deadline: Compute deadline = time.monotonic() + float(timeout) once at entry. Zero extra grace time is added beyond deadline.

3. Stop Admissions & Wake Idle Threads:

- Under the adapter lock, set self._draining = True and wake all idle worker condition waiters.

- Perform the best-effort loopback wake connect and close the listening socket so no new connections can be accepted.

4. Mark Backend Service Draining Immediately (Before Thread Joins):

- Immediately call service_closed = self._service.close(timeout=max(0.0, deadline - time.monotonic())).

- This sets LocalFileService._draining = True right away (causing any worker that has not yet acquired a binding lease to fail fast with unavailable), waits up to the remaining deadline for active backend leases to reach 0, and closes LocalAdapter instances if and only if leases drained.

5. Join Adapter Threads Within Remaining Deadline:

- Join the accept thread with timeout=max(0.0, deadline - time.monotonic()).

- For each of the 6 worker threads, call worker.join(timeout=max(0.0, deadline - time.monotonic())).

6. Return Value & Non-Destructive Incomplete Drain:

- If service_closed is True, the accept thread has exited, and all 6 worker threads have exited, set self._closed = True and return True.

- Otherwise return False without force-closing active backend file descriptors. Subsequent calls to close(timeout=...) are idempotent and complete cleanup once stuck operations finish.

## 8. Redaction Invariants

1. Error envelopes contain only code, outcome_uncertain, and retry_safe—never exception strings, stack traces, host paths, configured roots, bearer tokens, or payload bytes.

2. DriveHTTPAdapter.__repr__ exposes only host, port, socket_timeout, and closed status.

3. No adapter logging of tokens, roots, file paths, or request/response bodies is permitted.

## 9. Synthetic Acceptance Test Gates (tests/test_http_adapter.py)

All tests run exclusively against synthetic temporary directories, synthetic policies/tokens, ephemeral loopback ports (port=0), the accepted DriveClient (cbf60867), and raw loopback sockets.

### 9.1 Stage 1 — Conformance & Security Gate

1. End-to-End DriveClient Conformance:

- Exercise list, find, read, and save via DriveClient.

- Verify read on: (a) empty 0-byte file (data == b"", eof_observed is True), (b) short window (len(data) < length, eof_observed is True), (c) exact-fill window where offset + length == file_size (len(data) == length, eof_observed is False), and (d) explicit length <= grant.max_read_bytes when grant cap < 262144.

- Verify D058/D062 backend behaviors through DriveClient: whitespace-only find $\rightarrow$ invalid; out-of-range list offset $\rightarrow$ invalid; default read on small grant (max_read_bytes < 262144) $\rightarrow$ too_large.

2. Framing, Header & Single-Request Enforcement (Raw Sockets):

- Reject non-POST, non-HTTP/1.1, request line > 8192 B, header block > 16384 B, > 64 headers, bare \n, obs-fold, whitespace before :, missing/duplicate Host, Connection: keep-alive, duplicate Connection, Transfer-Encoding: chunked, Expect: 100-continue, missing/duplicate/non-digit Content-Length, missing/duplicate/wrong/parameterized Content-Type $\rightarrow$ 400 invalid.

- Send two back-to-back requests in one TCP payload; assert the second request is never executed (and already-buffered trailing bytes after Content-Length fail closed with 400 invalid).

- Reject query string on /v1/list|find|read, and reject missing, duplicate, or extra query keys (including token=) on /v1/save.

- Reject duplicate JSON keys, NaN/Infinity, UTF-8 BOM, invalid UTF-8, and extra/missing JSON keys.

3. Auth-First Ordering & Zero Filesystem I/O:

- Missing, duplicate, non-Bearer, or lexically invalid Authorization header $\rightarrow$ 403 denied before body read.

- Unknown token with malformed JSON body, duplicate JSON keys, missing source_id, or invalid /v1/save query $\rightarrow$ 403 denied (never 400 invalid), with instrumentation proving zero LocalAdapter/LocalWriter calls.

- Valid token with unauthorized (source_id, op) and malformed file_id or missing fields $\rightarrow$ 403 denied (never 400 invalid), with zero LocalAdapter/LocalWriter calls.

- Two agents with identical source_id alias mapped to distinct synthetic roots remain strictly isolated.

4. Bounds, Pre- vs. Post-Operation Save Uncertainty & Disconnects:

- Pre-dispatch Content-Length > 65536 on JSON ops and > 1048576 on /v1/save (with small drainable test payload or raw header check), and save with len(data) > grant.max_write_bytes $\rightarrow$ 413 too_large with outcome_uncertain=False, retry_safe=False.

- Fault-injected post-lease save failure $\rightarrow$ outcome_uncertain=True, retry_safe=False.

- Client disconnect before sending full Content-Length on /v1/save $\rightarrow$ zero filesystem mutation.

- Client disconnect after LocalFileService.save commits $\rightarrow$ file remains on disk; no rollback or retry.

5. 6-Slot Admission, Trickle Protection & Absolute Deadlines:

- Block 6 worker operations on a barrier; verify a 7th concurrent DriveClient call immediately receives DriveClientError(code="busy", outcome_uncertain=False, retry_safe=True) even if a raw socket concurrently trickles bytes at the busy listener; release barrier and verify all 6 operations succeed.

- Verify slow-header and slow-body trickle clients (1 byte every 0.2s against socket_timeout=0.5) are terminated at the absolute deadline (~0.5s), not kept alive per-recv.

- Verify threading.active_count() remains constant at startup + 7 threads.

6. Shutdown, Drain & Redaction:

- Reject close(timeout=-1), close(timeout=float("nan")), close(timeout=float("inf")), and close(timeout=True) with ValueError.

- With 1 operation blocked in flight, close(timeout=0.1) marks service draining promptly, returns False within ~0.1s (no 2x timeout or extra grace), leaves active adapter fd open, rejects new requests, and returns True on a subsequent close() once the operation finishes.

- Verify error envelopes and repr(adapter) never leak tokens, roots, paths, or exception text.

### 9.2 Stage 2 — 600-Call Smoke Gate (Gated on §9.1)

- 1 agent, 600 mixed list/find/read/save calls (sequential + bursts up to 6) over bounded synthetic fixtures. Assert FD count returns to baseline, zero temp-file residue, constant thread count, bounded RSS, and adapter.close() is True.

### 9.3 Stage 3 — 10,000-Call Six-Agent Soak Gate (Gated on §9.2)

- 6 agents $\times$ distinct aliases/roots, 10,000 total calls (~1,667/agent) with concurrency up to 6 over reused bounded fixtures. Assert zero unexpected error codes, zero cross-root leakage, stable FD count, zero temp-file growth, constant thread count, bounded RSS, and adapter.close() is True.

## 10. Explicit Residual Limits & Non-Goals

1. No Hard Filesystem Syscall Cancellation: socket_timeout enforces an absolute monotonic deadline on socket read/write phases only. Once a worker invokes LocalFileService, Python cannot preempt an in-flight kernel filesystem syscall; a wedged syscall occupies 1 of the 6 worker slots until the OS returns.

2. Best-Effort Early-Rejection Envelope Delivery on Large Unread Streams: If a client streams a payload larger than the bounded drain cap (64 KiB on workers, 16 KiB on busy reject) or stalls past 50 ms during early rejection, closing the socket may trigger a kernel TCP RST; DriveClient safely treats any resulting transport error on save as outcome_uncertain=True, retry_safe=False.

3. No Process-Exit Guarantee Under Uninterruptible Kernel Wait: If close(timeout=...) returns False because a worker thread remains blocked in the kernel, non-daemon worker threads can delay process termination until the syscall finishes.

4. No Request-ID Idempotency or Replay: Uncertain saves (outcome_uncertain=True, retry_safe=False) are never automatically retried or deduplicated.

5. Loopback Synthetic Scope Only: Plain HTTP on 127.0.0.1 only; no TLS, no public binding, no HTTP keep-alive/pipelining, no proxy/redirects, no OAuth/real credentials, and no MCP wrapper.
