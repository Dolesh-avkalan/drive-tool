D060 architectural and security review of unaccepted draft D059 (e9f857fa) against the accepted client (drive_tool/service_client.py @ cbf60867) and tested backend drop (drive_tool/file_service.py @ 8da43959) is complete (TESTS_NOT_RUN).

## Part 1 — Executive Security & Architecture Review of D059
### 1.1 Flawed Assumptions in D059 (Rejected & Corrected)

1. Rejected — 6-thread pool cannot emit immediate 503 busy when all 6 operations are blocked (§7, D1):

- Flaw: D059 specifies a total pool of 6 threads and recommends immediate 503 busy on the 7th concurrent request. If all 6 threads are blocked inside LocalFileService syscalls, no thread is free to accept(), read headers, and write 503 busy; the 7th client stalls in the kernel listen backlog until its socket timeout fires, raising DriveClientError(code="timeout", ...) instead of DriveClientError(code="busy", outcome_uncertain=False, retry_safe=True).

- Correction: Separate 1 dedicated accept/admission thread from 6 fixed operation worker threads (7 fixed adapter threads total, created once at startup; zero per-request thread spawning, zero waiting queue). When any of the 6 workers is idle, the accept thread hands the accepted socket directly to an idle worker in $O(1)$. When all 6 workers are occupied, the accept thread handles the 7th connection inline under a bounded rejection budget (BUSY_REJECT_TIMEOUT_SECONDS = 0.5), drains the bounded request so the client does not hit a TCP RST, writes 503 Service Unavailable with {"ok":false,"error":{"code":"busy","outcome_uncertain":false,"retry_safe":true}}, and closes the socket.

2. Rejected — Unauthenticated callers can trigger 400 invalid before auth check (§5, §13-A2, D2, A4):

- Flaw: D059 assumes that because LocalFileService lacks a public auth-only hook, the adapter must validate query parameters and JSON schema before calling LocalFileService, allowing unauthenticated or unauthorized callers to probe request-schema validity (400 invalid vs 403 denied). D059 also fails to notice that LocalFileService._authenticate_and_authorize catches (TypeError, ValueError) from PolicyTable.authenticate(token) and maps them to invalid if a syntactically malformed token reaches the service.

- Correction:

- Header Auth Gate (before body read): The adapter enforces that Authorization appears exactly once and matches ^Bearer ([\x21-\x7e]{32,256})$ (after stripping optional leading/trailing SP/HTAB). Missing, duplicate, non-Bearer, or syntactically invalid tokens immediately return 403 denied (outcome_uncertain=False, retry_safe=False) before reading any request body and before PolicyTable.authenticate is ever called.

- Service Auth-First Delegation on Schema Failure: In drive_tool/file_service.py (8da43959), list, find, read, and save all execute self._authenticate_and_authorize(token, source_id, op) as their first statement, before _parse_canonical_parts(file_id) or any parameter checks, and _authenticate_and_authorize authenticates token before authorizing (agent_id, source_id, op). When a request has a syntactically valid Authorization header but a malformed JSON body or /v1/save query string, the adapter extracts source_id if unambiguously present as a str (else None) and calls LocalFileService.<op>(token, source_id, _MISSING, ...) with an internal non-str sentinel _MISSING = object(). This guarantees:

1. Draining/closed service $\rightarrow$ 503 unavailable.

2. Unknown token $\rightarrow$ 403 denied (even if JSON/query was malformed).

3. Valid token + unauthorized (source_id, op) $\rightarrow$ 403 denied (even if file_id or other parameters were malformed).

4. Valid token + authorized (source_id, op) + malformed JSON/query $\rightarrow$ 400 invalid at _parse_canonical_parts(_MISSING) before any lease acquisition or filesystem I/O.

3. Rejected — Immediate sock.close() on early rejection causes TCP RST and clobbers error envelopes (§4, §9):

- Flaw: On loopback TCP, calling sock.close() while unread request body bytes remain in SO_RCVBUF forces the kernel to emit TCP RST. When DriveClient is sending a payload (especially POST /v1/save), a TCP RST destroys the unread HTTP error response in flight and causes http.client to raise ConnectionResetError, which DriveClient._wrap_transport maps to DriveClientError(code="unavailable", outcome_uncertain=True, retry_safe=False) instead of the server's denied, too_large, invalid, or busy envelope.

- Correction: For every response, after writing and flushing the HTTP response bytes, the adapter calls sock.shutdown(socket.SHUT_WR) (emitting TCP FIN), performs a bounded post-response drain of up to MAX_DRAIN_BYTES = 65536 within POST_RESPONSE_DRAIN_SECONDS = 0.05, and then calls sock.close().

4. Rejected — Sequential adapter drain then service drain doubles shutdown timeout (§10, D5):

- Flaw: D059 waits up to drain_budget for workers in the adapter and then calls LocalFileService.close(timeout=drain_budget), doubling the worst-case timeout to 2 * drain_budget and delaying LocalFileService._draining = True.

- Correction: DriveHTTPAdapter.close(*, timeout: float = 5.0) -> bool uses a single monotonic deadline deadline = time.monotonic() + float(timeout). It marks the adapter draining, closes the listener socket first, joins the accept thread, immediately invokes self._service.close(timeout=max(0.0, deadline - time.monotonic())) (which sets service._draining = True and waits on active leases), and joins the 6 workers within the remaining budget.

5. Rejected — Unchecked JSON duplicate keys, NaN/Infinity, query duplicate keys, and trailing pipelined bytes (§2, §4):

- Flaw: Python's default json.loads accepts duplicate keys (last-key-wins) and NaN/Infinity; D059 does not forbid duplicate query keys on /v1/save, query strings on /v1/list|find|read, obs-fold headers, whitespace before :, or trailing pipelined bytes after Content-Length.

- Correction: Enforce object_pairs_hook rejecting duplicate JSON keys, parse_constant rejecting NaN/Infinity, strict single-occurrence query parsing on /v1/save, no query strings on /v1/list|find|read, RFC 9112 header line validation, and rejection (400 invalid) if any bytes beyond Content-Length are present before service dispatch.

6. Rejected — Unchecked outbound response size and uncaught non-ServiceError exceptions (§3, §6):

- Flaw: DriveClient caps JSON replies at _MAX_JSON_BODY = 65536 and raises _ProtocolError $\rightarrow$ unavailable (retry_safe=True) if a 200 OK JSON body exceeds 65,536 bytes. Additionally, _translate_storage_exception in file_service.py re-raises unknown non-StorageError/OSError/ValueError exceptions (else: raise exc).

- Correction:

- If a serialized 200 OK JSON response body exceeds MAX_JSON_BODY_BYTES = 65536, the adapter does not send 200 OK; it emits 413 too_large (outcome_uncertain=False, retry_safe=False for list/find, outcome_uncertain=True, retry_safe=False for save).

- Before emitting 200 OK for /v1/read, the adapter verifies the outbound RangeReadResult satisfies all DriveClient._build_read_reply invariants; on violation it emits 503 unavailable (outcome_uncertain=False, retry_safe=True).

- Any unexpected Exception escaping LocalFileService is caught by the adapter and mapped to a redacted 503 unavailable envelope (outcome_uncertain=is_save, retry_safe=not is_save).

7. Rejected — Missing concrete Python API in drive_tool/http_adapter.py (§12, D7):

- Correction: CONTRACT.md §1.2 defines the exact exported class DriveHTTPAdapter, constructor signature, properties, and lifecycle methods.

### 1.2 Backend Change Assessment (drive_tool/file_service.py @ 8da43959 & active D058)

- Verdict: Zero code changes are required to drive_tool/file_service.py (8da43959) or drive_tool/service_client.py (cbf60867).

- Pinned Backend Requirement for D058: Independent backend work in D058 must preserve the exact D050 / 8da43959 execution order in LocalFileService.list, find, read, and save:

1. self._authenticate_and_authorize(token, source_id, operation) executes as the very first statement of each method before validating file_id or any operation parameter.

2. Inside _authenticate_and_authorize, self._ensure_not_draining() executes first (unavailable), self._policy_table.authenticate(token) executes second (denied), and self._policy_table.authorize(agent.agent_id, source_id, operation) executes third (denied on unauthorized grant/operation; invalid on non-string/malformed source_id).

3. Non-string file_id (such as the adapter's _MISSING sentinel) raises ServiceError("invalid", outcome_uncertain=False, retry_safe=False) immediately after _authenticate_and_authorize and before acquiring any binding lease.

4. LocalFileService.read returns a RangeReadResult with exact attributes data: bytes, offset: int, observed_size: int, observed_mtime_ns: int, eof_observed: bool.

### 1.3 Resolution Summary Table for D059 Open Items (D1–D9 & A1–A10)

| Item | Final Decision in D060 |
| --- | --- |
| D1 (Admission & backlog) | Fixed 1 accept thread + 6 operation worker threads; MAX_IN_FLIGHT_OPERATIONS = 6; zero wait queue; 7th concurrent request rejected inline with 503 busy (outcome_uncertain=False, retry_safe=True); LISTEN_BACKLOG = 16. |
| D2 (Authorization errors) | Missing, duplicate, non-Bearer, or token outside ^[\x21-\x7e]{32,256}$ $\rightarrow$ 403 denied (outcome_uncertain=False, retry_safe=False) before reading the request body. |
| D3 (Content-Type / Expect) | Fail closed (400 invalid): exact single Content-Type (application/json for list/find/read; application/octet-stream for save, no parameters); any Expect header rejected as 400 invalid (never emit 100 Continue). |
| D4 (Socket inactivity budget) | DEFAULT_SOCKET_TIMEOUT_SECONDS = 5.0 (configurable via socket_timeout: float = 5.0, finite float > 0.0). Per-socket operation timeout, not a filesystem syscall cancellation mechanism. |
| D5 (Drain budget & shutdown) | close(*, timeout: float = 5.0) -> bool shares a single monotonic deadline across listener close, accept-thread join, LocalFileService.close(timeout=remaining), and worker joins. Returns False without force-closing descriptors if any operation remains stuck at deadline. |
| D6 (Framing bounds) | MAX_REQUEST_LINE_BYTES = 8192, MAX_HEADER_BLOCK_BYTES = 16384, MAX_HEADER_COUNT = 64, MAX_JSON_BODY_BYTES = 65536, MAX_SAVE_BODY_BYTES = 1048576, MAX_READ_BODY_BYTES = 1048576. |
| D7 (Module & API scope) | Single production module drive_tool/http_adapter.py exporting DriveHTTPAdapter. |
| D8 (MODULE-006 scope) | In scope now for synthetic loopback REST acceptance; public exposure, TLS, OAuth, real roots/tokens, and MCP wrapper remain out of scope. |
| D9 / A1–A10 | All backend assumptions resolved and pinned against cbf60867 and 8da43959 (see CONTRACT.md §9). |

## Part 2 — Final Adopted CONTRACT.md

```markdown
# CONTRACT.md — Bounded Loopback HTTP Adapter (D060)

**Status:** Approved architecture and security contract (D060). Static specification only (`TESTS_NOT_RUN`).
**Scope:** Connect the frozen, accepted loopback HTTP client (`drive_tool/service_client.py`) to the synchronous local file-service layer (`drive_tool/file_service.py`) through a single dependency-free standard-library module `drive_tool/http_adapter.py`.

---

## 0. Source Pins & Change Boundaries

| Component | Repository @ Commit | Path | Status in D060 |
|---|---|---|---|
| Accepted HTTP Client | `Dolesh-avkalan/drive-tool` @ `cbf60867a0ca6e10206d540aed93f40e24b44a5d` | `drive_tool/service_client.py` | **Frozen — zero changes allowed** |
| Storage & `RangeReadResult` | `Dolesh-avkalan/drive-tool` @ `cbf60867a0ca6e10206d540aed93f40e24b44a5d` | `drive_tool/local.py`, `drive_tool/storage.py` | **Frozen — zero changes allowed** |
| Backend Service Contract | `Dolesh-avkalan/drive-tool` @ `8eac68b98481b86e7ae164a5835063584c15fdcb` | `tasks/d050-file-service-implementation.md` | **Frozen reference** |
| Tested Backend Drop (D058 active) | `Dolesh-avkalan/drop` @ `8da43959e93d36649add7422a256b7688d70c84e` | `drive_tool/file_service.py` | **Unchanged — pinned invariants in §9** |
| Bounded HTTP Adapter | Target repo (`drive-tool`) | `drive_tool/http_adapter.py` | **Single new production module** |
| Adapter Test Suite | Target repo (`drive-tool`) | `tests/test_http_adapter.py` | **New synthetic test module** |

No third-party dependencies, web frameworks, `asyncio` loops, or `http.server.BaseHTTPRequestHandler` subclasses may be used. The adapter must be implemented directly over Python standard library `socket`, `threading`, `time`, `math`, `json`, `re`, and `urllib.parse`.

---

## 1. Topology, Ownership & Module API (`drive_tool/http_adapter.py`)

### 1.1 Ownership Split
1. **`DriveHTTPAdapter` (`drive_tool/http_adapter.py`) owns:**
   - The IPv4 loopback listening socket bound strictly to `127.0.0.1:<port>`.
   - 1 dedicated accept/admission thread and 6 fixed operation worker threads.
   - Bounded HTTP/1.1 request framing, header validation, body reading, response serialization, per-connection socket timeouts, and the ordered shutdown sequence.
2. **`LocalFileService` (`drive_tool/file_service.py`) owns:**
   - The `PolicyTable`, per-`(agent_id, alias)` `LocalAdapter`/`LocalWriter` bindings, active operation leases, path containment, and service drain.
3. **Separation Rule:** `drive_tool/http_adapter.py` must never import or instantiate `LocalAdapter`, `LocalWriter`, `ListingService`, `search`, or `PolicyTable`, must never inspect filesystem paths, and must never access the host filesystem.

### 1.2 Public Python API
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

### 1.3 Constructor Validation & Startup Rollback

1. Service / Policies argument:

- Exactly one of policies or service must be non-None. Providing both or neither raises ValueError.

- If service is provided, isinstance(service, LocalFileService) must be True (else TypeError).

2. Port validation:

- port must be a non-bool int in 0..65535 (0 selects an ephemeral loopback port; non-int or bool raises TypeError; out of range raises ValueError).

3. Socket timeout validation:

- socket_timeout must be a non-bool int or float satisfying math.isfinite(socket_timeout) and socket_timeout > 0.0 (non-number or bool raises TypeError; <= 0.0 or non-finite raises ValueError).

4. Startup ordering & rollback:

- Step 1: If policies was passed, construct LocalFileService(policies). Any exception raised by LocalFileService(policies) propagates unchanged.

- Step 2: Create an AF_INET, SOCK_STREAM socket, bind to ("127.0.0.1", port), call listen(LISTEN_BACKLOG), and record the bound port via getsockname()[1]. (Do not set SO_REUSEPORT; on POSIX, SO_REUSEADDR may be set only when port != 0, or omitted so active port collisions always fail fast).

- Step 3: Start the 6 fixed worker threads and 1 accept thread.

- Rollback: If Step 2 or Step 3 raises any BaseException, close the listening socket (if opened), stop any started threads, call service.close(timeout=5.0), and re-raise the startup exception.

## 2. Exact Constants & Bounds

| Constant | Value | Purpose |
| --- | --- | --- |
| HOST | "127.0.0.1" | Mandatory IPv4 loopback bind address |
| MAX_IN_FLIGHT_OPERATIONS | 6 | Fixed worker count and maximum concurrent operations |
| LISTEN_BACKLOG | 16 | Kernel listen() backlog cap |
| MAX_REQUEST_LINE_BYTES | 8192 | Max byte length of request line including \r\n |
| MAX_HEADER_BLOCK_BYTES | 16384 | Max cumulative bytes of all header lines including final \r\n\r\n |
| MAX_HEADER_COUNT | 64 | Max number of HTTP header lines per request |
| MAX_JSON_BODY_BYTES | 65536 | Max request/response JSON body bytes (matches client _MAX_JSON_BODY) |
| MAX_SAVE_BODY_BYTES | 1048576 | Max /v1/save request body bytes (matches client _MAX_SAVE_BYTES) |
| MAX_READ_BODY_BYTES | 1048576 | Max /v1/read response body bytes (matches SERVICE_MAX_READ_BYTES) |
| DEFAULT_SOCKET_TIMEOUT_SECONDS | 5.0 | Default per-socket inactivity/operation timeout |
| DEFAULT_DRAIN_TIMEOUT_SECONDS | 5.0 | Default shutdown/drain timeout budget |
| BUSY_REJECT_TIMEOUT_SECONDS | 0.5 | Max socket budget for inline 503 busy rejection on the accept path |
| POST_RESPONSE_DRAIN_SECONDS | 0.05 | Bounded drain window after SHUT_WR before sock.close() |
| MAX_DRAIN_BYTES | 65536 | Max unread socket bytes drained during post-response teardown |

## 3. HTTP Framing, Header Validation & Smuggling/Pipelining Defenses

Every accepted connection handles at most one HTTP/1.1 request and is unconditionally closed afterward.

### 3.1 Request Line Parsing

1. Read until \r\n while enforcing len(request_line_with_crlf) <= MAX_REQUEST_LINE_BYTES.

- If EOF occurs with 0 bytes read: close socket quietly (no service call).

- If EOF occurs after >= 1 bytes before \r\n: close socket (no service call).

- If > MAX_REQUEST_LINE_BYTES bytes accumulate without \r\n: respond 400 invalid and close.

- Bare \n without preceding \r inside the line: respond 400 invalid and close.

2. Decode as ASCII (0x20..0x7E inside the line before \r\n; any NUL, control char, or byte >= 0x80 $\rightarrow$ 400 invalid).

3. Split on exact single space 0x20 into [method, target, version] (must have exactly 2 spaces; leading/trailing/multiple spaces $\rightarrow$ 400 invalid):

- version must be exact "HTTP/1.1" (anything else, including "HTTP/1.0", $\rightarrow$ 400 invalid).

- method must be exact "POST" (anything else $\rightarrow$ 400 invalid).

- target must not contain # (fragment) and must match one of:

- Exact "/v1/list" (no ? query string allowed)

- Exact "/v1/find" (no ? query string allowed)

- Exact "/v1/read" (no ? query string allowed)

- "/v1/save?<query>" where <query> is non-empty ("/v1/save" or "/v1/save?" $\rightarrow$ 400 invalid)

- Any other path or query placement $\rightarrow$ 400 invalid.

### 3.2 Header Block Parsing

1. Read header lines terminated by \r\n until the empty line \r\n.

- Total bytes of all header lines (including the terminating \r\n\r\n) must not exceed MAX_HEADER_BLOCK_BYTES = 16384.

- Total number of non-empty header lines must not exceed MAX_HEADER_COUNT = 64.

- Bare \n without \r, obs-fold continuation lines (starting with SP 0x20 or HTAB 0x09), or NUL/control bytes (0x00..0x1F except 0x09 in value, or >= 0x7F) $\rightarrow$ 400 invalid.

2. Each header line must contain : with a non-empty field-name consisting strictly of RFC 9110 tchar characters (!#$%&'*+-.^_|~0-9A-Za-z) and **zero whitespace between field-name and :** (RFC 9112 §5.1). Lowercase the field-name for matching; strip leading and trailing optional whitespace (SP/HTAB`) from the field-value.

### 3.3 Singleton Header & Framing Rules (Evaluated Before Body Read)

All singleton headers below may appear at most once. Evaluation order on headers is fixed so auth failures on syntactically framed requests never leak schema details, while framing/smuggling violations fail closed before reading any body:

1. Transfer-Encoding: Must not be present. Any occurrence (single or duplicate, any value) $\rightarrow$ 400 invalid (outcome_uncertain=False, retry_safe=False).

2. Expect: Must not be present. Any occurrence (including 100-continue) $\rightarrow$ 400 invalid (never emit 100 Continue).

3. Host: Must be present exactly once with a non-empty value (missing or duplicate $\rightarrow$ 400 invalid).

4. Connection: If present, must appear exactly once and its lowercase value must be "close" (duplicate or any other value such as "keep-alive" or "upgrade" $\rightarrow$ 400 invalid).

5. Content-Length:

- Must be present exactly once (missing or duplicate, even with identical values $\rightarrow$ 400 invalid).

- Value must match ^[0-9]+$ (ASCII digits only; no +, -, spaces, or underscores $\rightarrow$ 400 invalid).

- Parse as base-10 integer content_length.

6. Content-Type:

- Must be present exactly once (missing or duplicate $\rightarrow$ 400 invalid).

- For /v1/list, /v1/find, /v1/read: lowercase value must be exact "application/json" (no ; parameters allowed; mismatch $\rightarrow$ 400 invalid).

- For /v1/save: lowercase value must be exact "application/octet-stream" (no ; parameters allowed; mismatch $\rightarrow$ 400 invalid).

7. Authorization (Header Auth Gate):

- Must be present exactly once (missing or duplicate $\rightarrow$ 403 denied, outcome_uncertain=False, retry_safe=False).

- Value must match exact case-sensitive prefix "Bearer " followed by a token matching ^[\x21-\x7e]{32,256}$ (single space separator, 32..256 visible ASCII chars, no spaces). Any violation $\rightarrow$ 403 denied (outcome_uncertain=False, retry_safe=False).

8. Wire Body Cap Check:

- For /v1/list, /v1/find, /v1/read: if content_length > MAX_JSON_BODY_BYTES (65536) $\rightarrow$ 413 too_large (outcome_uncertain=False, retry_safe=False) before reading the body.

- For /v1/save: if content_length > MAX_SAVE_BODY_BYTES (1048576) $\rightarrow$ 413 too_large (outcome_uncertain=False, retry_safe=False) before reading the body.

### 3.4 Bounded Body Read & Anti-Pipelining Check

1. Read exactly content_length bytes from the socket.

- If the peer closes/resets the connection before content_length bytes are received (short read): abort and close the socket immediately without calling LocalFileService (zero filesystem side effects).

- If the socket timeout (socket_timeout) expires before content_length bytes are received: best-effort send 504 timeout (outcome_uncertain=False, retry_safe=True) and close the socket without calling LocalFileService.

2. Pipelining / Smuggling Excess-Byte Check:

- If the read buffer already contains > content_length bytes after the header terminator, or a non-blocking MSG_PEEK / settimeout(0.0) 1-byte check before service dispatch observes trailing unread bytes beyond content_length, reject the request as 400 invalid (outcome_uncertain=False, retry_safe=False) without calling LocalFileService and close the connection.

## 4. Endpoint, Query, JSON & Auth-First Dispatch Contract
### 4.1 Auth-First Delegation Sentinel (_MISSING)

Define module-private _MISSING = object(). Whenever the request body JSON or /v1/save query string fails syntax or schema validation in §4.2 or §4.3, the adapter must not return 400 invalid directly. Instead, it extracts source_id if and only if source_id was unambiguously parsed as a str (otherwise None) and calls:

- /v1/list: service.list(token, source_id, _MISSING)

- /v1/find: service.find(token, source_id, _MISSING, name_contains="")

- /v1/read: service.read(token, source_id, _MISSING)

- /v1/save: service.save(token, source_id, _MISSING, b"")

Because LocalFileService executes _authenticate_and_authorize(token, source_id, op) before inspecting file_id, this guarantees:

1. Draining service $\rightarrow$ 503 unavailable.

2. Unauthenticated token $\rightarrow$ 403 denied.

3. Unauthorized (token, source_id, op) $\rightarrow$ 403 denied.

4. Authenticated & authorized caller with malformed JSON/query $\rightarrow$ 400 invalid (from _parse_canonical_parts(_MISSING) before any lease or filesystem I/O).

### 4.2 JSON Operations (/v1/list, /v1/find, /v1/read)

1. UTF-8 & JSON Parsing:

- Reject UTF-8 BOM (body.startswith(b"\xef\xbb\xbf")) and invalid UTF-8 (body.decode("utf-8", errors="strict")).

- Parse with json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constants) where:

- _reject_duplicate_keys(pairs) raises ValueError if any key name appears more than once in the same object.

- _reject_constants(val) always raises ValueError (rejecting NaN, Infinity, -Infinity).

- Top-level value must be a dict.

2. Exact Key Sets & Service Mapping:

- POST /v1/list:

- Required keys: {"source_id", "file_id", "limit", "offset"}.

- Optional key: "name_contains" (if present in JSON, its value must not be None; DriveClient omits "name_contains" when None).

- No other keys allowed.

- Call: service.list(token, obj["source_id"], obj["file_id"], limit=obj["limit"], offset=obj["offset"], name_contains=obj.get("name_contains")).

- POST /v1/find:

- Required exact keys: {"source_id", "file_id", "name_contains", "limit", "max_entries", "max_depth"}. No other keys allowed.

- Call: service.find(token, obj["source_id"], obj["file_id"], name_contains=obj["name_contains"], limit=obj["limit"], max_entries=obj["max_entries"], max_depth=obj["max_depth"]).

- POST /v1/read:

- Required exact keys: {"source_id", "file_id", "offset", "length"}. No other keys allowed.

- Call: service.read(token, obj["source_id"], obj["file_id"], offset=obj["offset"], length=obj["length"]).

### 4.3 Raw Binary Operation (POST /v1/save?<query>)

1. Query Parsing:

- Parse <query> with urllib.parse.parse_qsl(query, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict").

- Reject duplicate keys, missing keys, or extra keys: the key sequence must contain each of {"source_id", "file_id", "overwrite", "create_parents"} exactly once (automatically rejecting token, authorization, bearer, or any other parameter).

- overwrite must be exact "true" (True) or "false" (False).

- create_parents must be exact "true" (True) or "false" (False).

- source_id and file_id are decoded once by parse_qsl and passed verbatim without stripping p/ or normalizing paths.

2. Service Mapping:

- Call: service.save(token, q["source_id"], q["file_id"], body, overwrite=overwrite_bool, create_parents=create_parents_bool).

## 5. Response Serialization, Observation Headers & Error Envelope Contract
### 5.1 JSON Success Responses (list, find, save)

- Serialize payload {"ok": True, "result": result} as UTF-8 JSON via json.dumps(envelope, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8").

- Outbound Size Gate: If len(raw_json) > MAX_JSON_BODY_BYTES (65536), do not emit 200 OK; instead emit 413 too_large (outcome_uncertain=(op == "save"), retry_safe=False).

- Otherwise emit:

- Status line: HTTP/1.1 200 OK\r\n

- Headers:

- Content-Type: application/json\r\n

- Content-Length: <len(raw_json)>\r\n

- Connection: close\r\n

- Body: raw_json

### 5.2 Binary Read Success Response (read)

1. Outbound RangeReadResult Validation (Before Writing Headers):
Verify all of the following on res = service.read(...):

- type(res.data) is bytes and len(res.data) <= request_length and len(res.data) <= MAX_READ_BODY_BYTES

- type(res.offset) is int and res.offset == request_offset and res.offset >= 0

- type(res.observed_size) is int and res.observed_size >= 0

- type(res.observed_mtime_ns) is int and res.observed_mtime_ns >= 0

- type(res.eof_observed) is bool

- (len(res.data) < request_length) == res.eof_observed (short window iff eof_observed is True; exact fill iff eof_observed is False)
If any check fails, emit 503 unavailable (outcome_uncertain=False, retry_safe=True) instead of 200 OK.

2. Wire Format:

- Status line: HTTP/1.1 200 OK\r\n

- Headers (exact single occurrence of each):

- Content-Type: application/octet-stream\r\n

- Content-Length: <len(res.data)>\r\n

- X-Drive-Offset: <decimal res.offset>\r\n

- X-Drive-Observed-Size: <decimal res.observed_size>\r\n

- X-Drive-Observed-Mtime-Ns: <decimal res.observed_mtime_ns>\r\n

- X-Drive-EOF-Observed: <"true" if res.eof_observed else "false">\r\n

- Connection: close\r\n

- Body: res.data

### 5.3 Error Envelope & Status Mapping (All Non-200 Responses)

- Body format:

```json
{"ok":false,"error":{"code":"<code>","outcome_uncertain":<bool>,"retry_safe":<bool>}}
```

- Headers: Content-Type: application/json, exact Content-Length, Connection: close.

- Exact HTTP status mapping:

| code | HTTP Status Line | Adapter-Synthesized outcome_uncertain | Adapter-Synthesized retry_safe |
| --- | --- | --- | --- |
| invalid | 400 Bad Request | False | False |
| denied | 403 Forbidden | False | False |
| not_found | 404 Not Found | From ServiceError | From ServiceError |
| exists | 409 Conflict | From ServiceError | From ServiceError |
| too_large | 413 Payload Too Large | False (pre-dispatch) / From ServiceError | False |
| busy | 503 Service Unavailable | False | True |
| unavailable | 503 Service Unavailable | From ServiceError (or is_save on unexpected exc) | From ServiceError (or not is_save on unexpected exc) |
| timeout | 504 Gateway Timeout | False (pre-dispatch read timeout) | True |

- When catching ServiceError from LocalFileService, copy exc.code, exc.outcome_uncertain, and exc.retry_safe verbatim.

- When catching any unexpected non-ServiceError Exception from LocalFileService, emit code="unavailable", outcome_uncertain=(op == "save"), retry_safe=(op != "save").

## 6. Concurrency, 6-Slot Admission, Socket Budget & Uncertain Save
### 6.1 Fixed Thread Pool & Immediate Saturation Rejection

1. Threads: At startup, DriveHTTPAdapter spawns 1 accept thread and 6 worker threads (MAX_IN_FLIGHT_OPERATIONS = 6). No thread is ever spawned per connection or per request.

2. Idle Worker Handoff (Zero Queue):

- Each of the 6 workers owns a dedicated 1-connection slot and registers itself in an idle worker stack/list under a lock when free.

- When the accept thread receives a connection conn from listener.accept():

- If _draining is True: immediately reject or close conn.

- Under the pool lock, if at least one worker is idle, pop one idle worker and hand conn directly to it.

- If all 6 workers are busy (7th concurrent connection): the accept thread handles conn inline with conn.settimeout(BUSY_REJECT_TIMEOUT_SECONDS) (0.5s), drains up to MAX_SAVE_BODY_BYTES of the incoming request so DriveClient does not hit a TCP RST, sends 503 Service Unavailable with {"ok":false,"error":{"code":"busy","outcome_uncertain":false,"retry_safe":true}}, performs SHUT_WR + bounded drain, and closes conn.

### 6.2 Socket Inactivity Budget vs. Filesystem Operations

- Each worker sets conn.settimeout(self._socket_timeout) (default 5.0s) covering request line read, header read, body read, and response write.

- Once a worker calls LocalFileService.<op>(...), the synchronous filesystem call runs to completion without cancellation. A blocked filesystem syscall holds that worker slot until the OS syscall returns; if all 6 workers become blocked, subsequent requests receive 503 busy.

### 6.3 Connection Teardown & Uncertain Save Semantics

1. Graceful TCP Teardown (Preventing RST): After sending any complete response (200 or error), call conn.shutdown(socket.SHUT_WR), set timeout to POST_RESPONSE_DRAIN_SECONDS (0.05s), read/discard up to MAX_DRAIN_BYTES (65536) until EOF or timeout/error, and then call conn.close().

2. Save Disconnect Rules:

- Disconnect before full request body (< Content-Length bytes received): Worker closes socket without calling LocalFileService.save. Zero filesystem changes occur.

- Disconnect during or after LocalFileService.save: Worker never attempts rollback, deletion, or retry. Any completed write or parent directory creation stands on disk; client observes transport failure (DriveClientError(code="unavailable"|"timeout", outcome_uncertain=True, retry_safe=False)).

- Storage error after lease acquisition in LocalFileService.save: ServiceError carries outcome_uncertain=True, retry_safe=False, which the adapter serializes verbatim into the error envelope.

## 7. Startup, Shutdown & Drain Ownership

DriveHTTPAdapter.close(*, timeout: float = 5.0) -> bool executes under a single deadline deadline = time.monotonic() + float(timeout):

1. Validate timeout: if type(timeout) is bool or not isinstance(timeout, (int, float)) or math.isnan(float(timeout)) or float(timeout) < 0.0, raise ValueError("timeout must be a non-negative number").

2. Set adapter _draining = True and close the listening socket (so no new connections are accepted and accept() wakes immediately).

3. Wake any idle workers and join the accept thread up to max(0.0, deadline - time.monotonic()).

4. Call service_closed = self._service.close(timeout=max(0.0, deadline - time.monotonic())).

- This immediately marks LocalFileService as draining (unavailable for any worker that has not yet acquired a binding lease) and waits for active leases to reach 0.

5. Join worker threads within the remaining time budget (with a up to 0.05s post-service flush grace if service_closed is True).

6. If service_closed is True and all worker threads have exited, mark _closed = True and return True.

7. If service_closed is False or any worker remains blocked in an active operation at deadline, do not force-close active descriptors and return False. Subsequent calls to close(timeout=...) are idempotent and complete cleanup once stuck operations finish.

## 8. Redaction & Security Invariants

1. Error responses contain only {"ok":false,"error":{"code":...,"outcome_uncertain":...,"retry_safe":...}} — never exception messages, tracebacks, file paths, host roots, bearer tokens, or request body snippets.

2. DriveHTTPAdapter.__repr__ includes only host, port, socket_timeout, and closed state.

3. No logging of tokens, roots, paths, or payloads is permitted. All adapter internal exceptions are raised from None.

## 9. Backend Contract Compatibility & D058 Pin Requirements

drive_tool/file_service.py (8da43959) requires no modifications. Independent backend implementation D058 must preserve these exact D050 invariants:

1. Public API: LocalFileService(policies), .list(...), .find(...), .read(...), .save(...), .close(*, timeout: float = 5.0) -> bool, and ServiceError(code, *, outcome_uncertain, retry_safe).

2. In .list, .find, .read, and .save, authentication and authorization (_authenticate_and_authorize(token, source_id, op)) run before file_id parsing or any parameter validation:

- Draining/closed service raises ServiceError("unavailable", outcome_uncertain=False, retry_safe=True).

- Unknown token raises ServiceError("denied", outcome_uncertain=False, retry_safe=False) regardless of source_id or file_id.

- Valid token with non-string/invalid source_id raises ServiceError("invalid", outcome_uncertain=False, retry_safe=False); valid token with unauthorized (source_id, op) raises ServiceError("denied", outcome_uncertain=False, retry_safe=False) regardless of file_id.

- Non-string file_id (such as _MISSING) raises ServiceError("invalid", outcome_uncertain=False, retry_safe=False) before acquiring a binding lease.

3. LocalFileService.read returns RangeReadResult with fields data, offset, observed_size, observed_mtime_ns, eof_observed where (len(data) < length) == eof_observed.

## 10. Synthetic Acceptance Test Gates (tests/test_http_adapter.py)

All tests use synthetic temporary roots, synthetic tokens/policies, ephemeral loopback ports (port=0), the real DriveClient (cbf60867), and raw loopback socket clients.

### 10.1 Conformance & Security Gate (Mandatory Before Smoke/Soak)

1. End-to-End DriveClient Wire Conformance:

- list (with and without name_contains, pagination next_offset), find (truncated, reason, inspected, skipped), read (exact-fill eof_observed=False, short window eof_observed=True, observation headers), and save (overwrite=False|True, create_parents=False|True).

2. Framing, Duplicate Headers & Smuggling/Pipelining (Raw Sockets):

- Wrong method (GET, PUT), wrong HTTP version (HTTP/1.0), overlong request line (> 8192), overlong header block (> 16384), > 64 headers, bare \n, obs-fold header line, space before :, missing/duplicate Host, Connection: keep-alive, duplicate Connection, any Transfer-Encoding: chunked, any Expect: 100-continue, missing/duplicate/non-decimal Content-Length, wrong/duplicate/parameterized Content-Type $\rightarrow$ 400 invalid and socket closed.

- Trailing pipelined bytes after Content-Length $\rightarrow$ 400 invalid before any LocalFileService call.

- Query string on /v1/list, /v1/find, /v1/read $\rightarrow$ 400 invalid; duplicate/missing/extra query keys (including token=) on /v1/save $\rightarrow$ 400 invalid (or 403 denied if token is bad).

- Duplicate JSON keys ({"source_id":"a","source_id":"b",...}), NaN/Infinity, UTF-8 BOM, invalid UTF-8, unknown JSON keys $\rightarrow$ 400 invalid (or 403 denied if token is bad).

3. Auth-Before-Operation-IO & Auth-Before-Schema-Validation:

- Missing Authorization, duplicate Authorization, non-Bearer, or token outside 32..256 visible ASCII $\rightarrow$ 403 denied before body read.

- Unknown token with malformed JSON body, unknown JSON keys, or invalid /v1/save query booleans $\rightarrow$ 403 denied (never 400 invalid), with probe counter proving zero adapter/filesystem calls.

- Valid token with unauthorized source_id or unauthorized operation and invalid file_id/limit/overwrite $\rightarrow$ 403 denied (never 400 invalid), with zero adapter/filesystem calls.

- Two agents granted the same source_id alias mapped to distinct roots $\rightarrow$ strict root isolation.

4. Bounds & Error Semantics:

- Content-Length > 65536 on JSON endpoints or > 1048576 on /v1/save via DriveClient / raw socket $\rightarrow$ 413 too_large (outcome_uncertain=False, retry_safe=False) delivered cleanly without TCP RST.

- read with length > grant.max_read_bytes and save with len(data) > grant.max_write_bytes via DriveClient $\rightarrow$ DriveClientError(code="too_large", outcome_uncertain=False, retry_safe=False).

- Outbound list/find result exceeding 65536 serialized JSON bytes $\rightarrow$ DriveClientError(code="too_large", outcome_uncertain=False, retry_safe=False).

5. Uncertain Save & Disconnect Safety:

- Raw socket disconnects after sending < Content-Length bytes on /v1/save $\rightarrow$ file is not created/modified on disk.

- Client disconnects while response is being written after save commits $\rightarrow$ file remains committed on disk; no rollback or retry.

- Fault-injected storage failure inside LocalWriter.write_bytes $\rightarrow$ DriveClientError(code="unavailable", outcome_uncertain=True, retry_safe=False).

6. Concurrency, 6-Slot Admission & Inactivity Timeout:

- Hold 6 operations in flight via a synchronization barrier inside the service; issue a 7th concurrent DriveClient call (list and save) and assert it immediately raises DriveClientError(code="busy", outcome_uncertain=False, retry_safe=True) while the 6 operations remain blocked; release the barrier and verify all 6 succeed.

- Assert threading.active_count() never increases beyond the fixed startup pool.

- Slow-header and slow-body raw-socket connections exceeding socket_timeout are terminated and release their worker slot.

7. Lifecycle, Drain & Redaction:

- Constructor rollback when policies is invalid or port is already in use closes all opened LocalAdapters.

- close(timeout=0.05) while an operation is blocked in flight closes the listener immediately, returns False, keeps the active LocalAdapter open, rejects new requests with unavailable, and returns True on a subsequent close() after the operation finishes.

- Malformed requests echoing tokens and root paths produce responses containing neither.

### 10.2 Stage 2 — 600-Call Smoke Gate (Gated on §10.1 Passing)

- 1 agent, 600 calls across list, find, read, save (sequential + bursts up to 6 concurrent calls) over bounded synthetic fixtures.

- Assertions: FD count (len(os.listdir("/proc/self/fd")) on Linux) returns to baseline, zero leftover temp files, constant threading.active_count(), bounded RSS delta, adapter.close() returns True.

### 10.3 Stage 3 — 10,000-Call Six-Agent Soak Gate (Gated on §10.2 Passing)

- 6 agents $\times$ distinct aliases/roots, 10,000 total calls (~1,667 per agent), mixed operations with sustained concurrency up to 6 over bounded reused fixtures.

- Assertions: zero unrecognized error codes, zero cross-root leakage, stable FD count, zero temp-file accumulation, constant thread count, bounded RSS, adapter.close() returns True.

## 11. Explicit Residual Limits & Non-Goals

1. No Hard Syscall Cancellation: socket_timeout bounds socket I/O inactivity only; once a worker enters LocalFileService, a blocked kernel filesystem syscall cannot be preempted by Python and will occupy 1 of the 6 worker slots until the OS call returns.

2. No Process-Exit Guarantee Under Wedged Kernel I/O: If close(timeout=...) returns False because a worker thread remains blocked in an uninterruptible kernel syscall, non-daemon worker threads can delay process exit until the syscall completes.

3. No Request-ID Idempotency or Replay: Disconnected or post-lease failed save requests report outcome_uncertain=True, retry_safe=False; callers must not assume verify-then-retry is race-safe under concurrent writers.

4. Loopback Synthetic Transport Only: No TLS, no non-loopback binding, no HTTP keep-alive/pipelining, no proxy/redirect support, no OAuth/credential loader, and no MCP wrapper.
