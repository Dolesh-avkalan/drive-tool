# D041 — minimal agent-service contract review (parallel with D040)

Gemini, I need you to turn the constraints below into a concrete minimal implementation brief for MODULE-006A. This is design work only: no code/tests/installs/credentials/merges/deployment. D040's range-read developer is active on another host; your work must not alter or duplicate that module. I own the final contract and integration. Return <=1600 words, precise choices, blockers and acceptance tests rather than a framework catalog.

## Authoritative existing foundation

Repo Dolesh-avkalan/drive-tool, main91ebbf5 (includes approved PR4; implementation source identical to e88c9c79fdc27057622d44ba51e008f2e4ea4f4f). Python3.11+ stdlib, actual layout drive_tool/ (NOT src/drive_tool/). Existing objects:
- storage.FileRef(source_id,file_id), ROOT_ID='root'; canonical local IDs p/a/b.txt.
- local.LocalAdapter(source_id,root,max_read_bytes=1048576,...) holds anchored rootfd; stat(ref), list_children(parent), read_bytes(ref), optional scan_children(parent,*,max_entries) -> ScanBatch(items,inspected,complete).
- search.find(adapter,parent,query,...) is a MODULE FUNCTION, not LocalAdapter.find. Its bounded raw-entry/depth/result contract reports items/inspected/skipped/truncated/reason. No continuation after prefix/depth budget exhaustion; no fake legacy capability fallback. Do not invent parameter names if omitted here; specify mapping as a source-confirmation prerequisite instead.
- listing helper full bounded list windows; listing/search return existing FileMetadata(ref,name,parent_id,kind,size_bytes). Existing metadata has NO mtime field; do not invent it or add stat calls per result.
- local_write.LocalWriter(adapter,max_write_bytes=...) provides write_bytes(parent,name,data,overwrite=False), mkdir(parent,name). Uses no-replace publish; a file can already exist when parent-fsync fails. No blind safe-write-retry promise.
- Lifecycle close racing active operations is UNSUPPORTED. External mutation, hardlinks/mounts and operators provisioning roots are documented boundaries.
- D040 optional LocalAdapter.read_range(ref,*,offset=0,length=None) -> frozen RangeReadResult(data,offset,observed_size,observed_mtime_ns,eof_observed) is being implemented, not accepted yet. Requires pread, per-window cap even when total file exceeds cap; actual empty pread only determines eof_observed, exact full window means False. Initial observed metadata is not a snapshot/version. Service implementation must wait for accepted exact range source pin; design can proceed now.

## Deliverable / minimal target

Six agents, four normal operations find/list/read/save, short outputs default20/cap100 items. Authenticated REST core plus thin local Python client; loopback synthetic roots only in first candidate. MCP/public remote access/OAuth/durable jobs/indexing deferred. Save can create missing parents in one call, default no overwrite, explicit partial side effects and uncertain outcomes. No real credentials/roots or public service deployment in this task.

Propose the smallest allowed file scope under drive_tool/ plus dedicated tests/docs and isolated feature branch. Exact config shapes, operation wire schema/client methods and fixed eight-code error set invalid/denied/not_found/exists/too_large/busy/unavailable/timeout. Prefer raw binary read/save to giant base64. Static startup per-agent permissions and read/write/byte limits; authenticate and authorize before filesystem side effects. Request paths reference configured sources and canonical IDs, never arbitrary host roots. Nested grants must not leak ancestor/sibling metadata, search or closest-name hints. Explain whether per-grant anchored adapters (remapped IDs) or lexical sub-prefix grants are necessary given ancestor directory renames; do not claim a string prefix alone is a security boundary. Keep configuration minimal.

## Fix concrete holes from the previous proposal

1. Absolute total request-line/header/body deadlines AND byte/count caps. Socket inactivity timeout alone allows endless trickle. Bound accepted connections, work queue, global/per-agent/source slots and buffered request/response bytes. Stdlib HTTP parser limits must be explicitly implemented/used, not assumed. Frame Content-Length/duplicate length/Transfer-Encoding unambiguously; reject unsupported keepalive/pipelining if that simplifies correctness.
2. Response timeout does not cancel a filesystem syscall or stop a later save. Held worker retains all quotas/adapter leases until actual completion. Explain bounded threads and how source/agent saturation affects unrelated callers under a finite global cap; do not promise immunity from arbitrary global saturation. No unbounded executor queue. Distinguish syscall deadlines from response-wait budgets, and network delivery from compute completion.
3. Shutdown cannot promise immediate bounded drain/rebind with uninterruptible I/O. Choose honest graceful-drain behavior, bounded response to shutdown and operator fallback; never close/reuse active descriptors. No process supervisor architecture unless needed for the chosen first scope.
4. Outcome uncertain/retry_safe false on post-publish durability error or lost/timed-out save completion. If no durable idempotency, explicitly defer it; verify-before-retry is guidance, not protection against a still-active save racing the verification. Expose no false known-not-committed claim. Parent mkdir side effects persist on failure, no shared-dir rollback. Missing-parent creation cannot cross grant boundary.
5. Full bounded list offset windows are per-call observations, not snapshots across calls. find has honest truncation/skipped accounting and no cursor. Short replies must not hide incomplete coverage.
6. Preserve observed-only range metadata/eof_observed. Never promise final EOF or coherent file versions under concurrent external mutation.
7. Separate real agent caller from simulated raw-socket HTTP tests. First local client is an actual supported entry point; remote browser teammates cannot call loopback and need a later reviewed transport. No claim of fully usable remote Drive tool yet.

## Acceptance before implementation starts

Choose realistic predeclared bounds and synthetic tests for6 identities/grants, auth/malformed/framing/slow-trickle rejection, policy non-leakage, saturation with a controlled blocked adapter, response timeout while quotas remain held, delayed/post-publish save uncertainty, safe drain, redacted bounded audit logs and health, fd/temp/RSS stability. Stage600-call smoke before10000-call soak. Avoid fake tight hardware-dependent p99 claims; separate correctness thresholds from benchmark observations. Explicitly identify product-scope tradeoffs needed to keep this small (at most two justified module boundaries).

Return a brief suitable for a developer, source-confirmation prerequisites, concrete decisions and honest deferred limitations. This is a contract/design review; TESTS_NOT_RUN. If supplied context cannot justify a security choice, flag it rather than inventing production guarantees. No source change or acceptance of D040 implied.
