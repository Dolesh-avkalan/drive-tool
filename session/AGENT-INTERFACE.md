# Agent interface and runtime design — light, reliable, boring

Design requirement established 2026-10-15 (D016). Director-owned design; Gemini and Sarvam
implement and test. This defines acceptance requirements, not implemented capabilities. It
refines FILE-ACCESS-REQUIREMENTS.md for the agent-facing layer.

## Target workload

About 6 agents use the tool all day at random times. Every call must behave the same way at
any hour: no warm-up, no per-agent session to keep alive, no call that slows the others, no
state an agent has to remember. Getting or saving a file is one call and a short reply.

## Token budget (hard requirements)

- At most 5 agent tools: `find`, `list`, `read`, `save`, plus `move`/`trash` only when that
  milestone is accepted. All tool descriptions together <= 400 tokens.
- One call per normal action. `save` takes a destination path and creates missing folders; it
  needs no prior list/stat. Only destructive actions (trash, overwrite) need an explicit flag;
  there is no preview-then-confirm round trip for normal saves and reads.
- Replies are short by default: one line per entry (`path  size  modified`), at most 20 entries
  unless the agent asks for more (cap 100), and a single `more: <offset>` hint when truncated.
- Errors are one line from a fixed set of codes, with the fix in the text when known:
  `not_found`, `exists`, `denied`, `too_large`, `busy`, `timeout`, `unavailable`, `invalid`.
  Example: `not_found: p/reports/q3.pdf (closest: p/reports/2026/q3.pdf)`.
- Agents never see audit, job, policy or adapter internals.

## Runtime shape

- One small long-running service process per machine, standard library first, minimal pinned
  dependencies. REST is the core; MCP is a thin wrapper over the same calls.
- Calls are stateless: bearer token -> agent identity; IDs are readable paths (`p/a/b.txt`);
  continuation is a plain offset. A service restart loses nothing an agent holds; in-flight
  calls fail with `unavailable` and are safe to retry.
- Adapters are opened at service start and kept open for the life of the process. They are
  closed only at shutdown after in-flight calls drain (removes the close-during-use hazard
  documented in MODULE-003).
- Per-agent config file loaded at start: allowed sources/roots, read or read+write, byte caps.
  Policy is checked on every call; a path is never a grant.

## Concurrency and limits (so one agent never breaks the rhythm)

- Fixed worker pool; every operation has its own descriptors (MODULE-003 already does this) and
  no global lock on reads.
- Per-agent in-flight cap (default 2) and per-source cap. When full, return `busy` at once with
  a retry hint instead of queueing without bound.
- Every call has a deadline (default 30 s); listing and find have entry budgets enforced inside
  the adapter while scanning (not after materializing the directory) and report truncation.
- Reads are bounded; larger files use stateless range reads (`offset`, `length`), no stream
  sessions to manage.
- Saves: unique temp file in the destination folder, fsync, atomic no-overwrite publish
  (link or rename-no-replace), temp cleanup on any failure. An optional request id makes a
  retried save idempotent. Default is never overwrite; collision returns `exists`.

## Operations

- Audit is an append-only JSON-lines file, one line per call (time, agent, op, ref, outcome,
  bytes, ms), rotated daily, no file contents or secrets.
- `/health` endpoint; supervised restart (systemd or equivalent).
- No background crawling, indexing, watching or sync in v1. Durable jobs only for later
  Drive transfers; an agent sees a job id only when a call cannot finish within its deadline.

## Acceptance: the boring-day soak test

Before agent rollout, a synthetic soak run on a conformant Linux filesystem: 6 simulated agents
at random intervals, at least 10,000 mixed find/list/read/save calls, with injected faults
(file vanishes, folder swapped for a symlink, 200,000-entry folder, slow reader, service
restart). Pass only if: zero reads outside the root or of wrong content; zero leaked
descriptors or temp files; every error is one of the fixed codes; memory and descriptor count
flat over the run; p99 latency for small read/save within an agreed bound; no agent blocked by
another agent's large folder or slow read.

## Module order

1. MODULE-004 bounded enumeration: entry budget inside `list_children`, `find` by name with
   budget and truncation, typed exhaustion error, tests for the uncovered error-mapping paths.
2. MODULE-005 atomic no-overwrite save (local).
3. MODULE-006 service: 5 tools, per-agent tokens and caps, deadlines, audit, health.
4. Soak test. Google Drive connector after the local routine passes.
