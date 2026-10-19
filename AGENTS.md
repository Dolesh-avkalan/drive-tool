# Director contract (read this first; it overrides anything older)

Owner: Dolesh (GitHub `Dolesh-avkalan`). Money is tight: the director (you, whatever model you
are) plans and decides; cheap web agents do the work through the browser host ("the box").
Pickup point and recipe: `Dolesh-avkalan/browser-tool` -> `NEXT.md`, then `HANDOFF.md`.

## How work gets done: one way only, with the director kit
Clone `Dolesh-avkalan/browser-tool` and use `D="python3 browser-tool/browser_host/director.py"`
(no box token needed). Every command ends with a `NEXT:` line: follow it. Plans are checked
for known wrong turns (`WRONG WAY:`); push anyway only on purpose with `--ack`.
1. `$D status`  2. `$D plan new feature ID=... PROJECT=drive-tool REPO=Dolesh-avkalan/drive-tool ...`
   and write the contract into the step texts  3. `$D plan push <id>.md` + `$D plan wait <id>`
4. `$D plan failures <id>` or `$D verify <id>` (byte check), then merge with the printed
   expected head. Details: browser-tool `AGENTS.md`.

Never:
- spawn your own agents, sub-agents or "workers", or let any agent spawn them;
- open the chat websites yourself, or use the box's built-in Sarvam chat agent or any API
  agent for project work;
- paste code or whole files into a chat or task text. Inputs go in as uploaded files:
  `attach:` (repo or `drive:` paths) and `use:` (earlier steps' records). Task text is
  instructions only.

## Fixed roles (not interchangeable workers)
| Who | Does | Never |
|---|---|---|
| DeepSeek | writes code and tests (`expect: FILE:`) | reviews its own work |
| Gemini (`model: 4 Argon`) | security review (`fix: code`) | writes the code |
| Sarvam | independent review (`fix: tests`/`code`); publishes the branch + PR | is the only gate |
| box | runs the real test suite in its sandbox; only its result counts | — |
| Grok | last resort only (small usage limit, temporary account) | default choice |
A reviewer's CHANGES REQUIRED goes to its `fix:` step or to the director; never re-ask
another agent until one says ACCEPT. `branchtest` after `publish` needs `use: publish`.

## What the director reads and does
- Reads: `head -3 status/box.md` on browser-bus, then plan summaries. Not raw chats, not
  whole result files, not this repo's history.
- Does itself: contracts (exact files, functions, errors, acceptance), plans, the
  byte-identical check of the published branch against the reviewed records, merges, and
  diagnosing a test that fails twice (reproduce locally as non-root, then hand the agent the
  exact change).
- Does NOT write this repo's code or tests: agents do, through plans.

## This repo (drive-tool)
- Public repository: never commit credentials, cookies, private data or unredacted secrets.
- Merge authorization (Dolesh, D025): merge without asking once the security review, the
  independent review and the box test of the exact branch head pass; use expected-head
  protection; report afterwards. Deployment, credentials, real roots and public exposure
  still need Dolesh.
- Keep modules small; ask for an add-on module rather than a whole 600-1200 line file.
- `session/STATUS.md`: a plain summary of at most 10 lines at the top; update it when
  something meaningful changes (no checkpoint-only commits). Evidence: what was run and seen,
  kept apart from inference.
- Google Drive (milestone 3) waits for Dolesh's decision on the rclone design note.
- Older instructions (Codex-era teamwork rules, D-numbered tasks, the 2026-10-16 PC-shutdown
  note): `session/history/2026-10-19-AGENTS-before-director-contract.md`. History only.
