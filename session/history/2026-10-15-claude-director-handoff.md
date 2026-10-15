# Director handoff — Claude to Codex, 2026-10-15 (~13:35 UTC)

Dolesh asked Claude (a cloud Claude Code session) to direct drive-tool today and to hand over to Codex
when he left the office. This note is what Codex needs to continue. Everything below was checked
against git, the relay bus and the drop repo unless marked "reported".

## State in one paragraph

main = af42edb + session/spec commits: read-only core (MODULE-001..003) and MODULE-004 (bounded
listing + bounded find) are merged. MODULE-005 (safe local writes) is being built through the relay
chain d025..d028; d025 and d026 were queued at 13:28 UTC and run unattended; d027 and d028 are HELD
and must be released by the director after checking the previous output. Project is roughly 12% of
the README milestones (local core ~60%: writes and the agent service are missing).

## Where things are

- Briefs: tasks/MODULE-004.md (done), tasks/MODULE-005.md (active). Design target for the agent layer:
  session/AGENT-INTERFACE.md (D016: 5 tools, short replies, ~6 agents all day, soak test gate).
- Decisions today: D015 core merged, D016 agent interface, D017 MODULE-004 accepted. History in
  PROGRESS.md (all 2026-10-15 entries).
- Relay used today: the browser-tool box `https://34-89-54-103.sslip.io` (GCP VM "directortemp"),
  NOT a local VM. Task bus = branch `browser-bus` of Dolesh-avkalan/browser-tool:
  `tasks/<id>.md` -> `tasks/<id>.result.md`; held tasks sit in `hold/` (the relay ignores that folder).
  Outputs land in the public drop repo Dolesh-avkalan/drop under `drive-tool/module-00N/`.
- Agents on the box: Gemini (`model: 4 Argon`, `effort: high`; 3.1 Pro is refused on this VM),
  DeepSeek (`deepthink: on|off`, `search: on|off`, both off unless asked), Sarvam (pushes to GitHub
  itself: it is in the relay's pushers set). Team roles Dolesh set today: Argon = heavy thinking
  (design review, code review), DeepSeek = code and tests, Sarvam = routine assembly, test runs,
  mutation checks, pushes, evidence.

## MODULE-005 chain: what to do next

| Task | Agent | State at handoff | Output (drop repo) |
|---|---|---|---|
| d025 | Argon high | queued/running | drive-tool/module-005/d025-argon-design-review.md |
| d026 | DeepSeek DeepThink | queued after d025 | drive-tool/module-005/d026-deepseek-implementation.md |
| d027 | Sarvam | HELD in hold/d027.md | d027-sarvam-assemble-test.md + branch feature/module-005-local-write |
| d028 | Argon high | HELD in hold/d028.md | d028-argon-code-review.md |

Claude may keep releasing these while its session lives: check the bus for which results exist first.

1. When `tasks/d026.result.md` exists: check the d026 file is complete (FILE blocks for
   drive_tool/local_write.py, tests/test_local_write.py, drive_tool/storage.py; two `EDIT
   drive_tool/local.py:` blocks; ends with STATUS: GENERATED). Quick check used today: extract the
   blocks with a regex and `ast.parse` each; method-level blocks need wrapping in a class. Headings
   outside code blocks lose markdown underscores (`__init__` shows as `init`); code inside is exact.
   If Argon's d025 raised something that should override DeepSeek, decide before releasing d027.
2. Release d027: `git mv hold/d027.md tasks/d027.md`, commit, push to browser-bus. Then verify
   Sarvam's claims against git, not its reply: branch files byte-identical to d026 (except labelled
   "Sarvam fixes" commits), only the two local.py edits, and run the suite yourself.
3. Release d028 (Argon review of the pushed branch). Then director acceptance: independent run,
   your own mutation check (today's harness: apply one targeted mutation at a time in a scratch
   copy, run the module's tests, record which fail; Sarvam's version is in the drop repo under
   drive-tool/module-004/d024-sarvam-proof-tests/mutate.py), adversarial probes (symlinked parent
   to outside dir, two concurrent writers, injected failures leave no temp file and no fd).
4. Record a D018 decision + PROGRESS/STATUS, open the PR to main; merge only with Dolesh's approval.

## Pitfalls hit today (avoid them)

- Edit a queued task at least ~2 minutes before it runs; the box pulls the bus periodically. An edit
  40 s before start was not picked up (d017 ran the old text).
- Hold dependent tasks (`hold/`) and release one at a time after checking the previous output. A
  truncated d017 output reached Sarvam (d018) before a hold arrived; it had to be stopped by hand.
- Never merge to browser-tool main while a relay task runs: the deploy restarts hostd, the relay
  re-runs the task once, and Sarvam (which runs on its own servers) keeps going in the old chat.
  Today this created two Sarvam threads for d024; the old one was stopped by hand before it pushed.
- Write task files with a QUOTED heredoc (`<<'EOF'`) or a file writer: backticks in an unquoted
  heredoc are executed by the shell and vanish from the text.
- DeepSeek stops at an output limit and shows Continue; the relay now presses it with a real click
  (browser-tool PRs #10, #11; verified live: 2 presses, 87.5k chars). Its reasoning can also be cut.
- In LocalAdapter IDs, `p/` is a prefix, not a folder: `p/sub/leaf` is `<root>/sub/leaf` (DeepSeek's
  first proof test got this wrong; Sarvam fixed the fixture).
- Keep session files' bytes: some have mixed CRLF/LF. Append with a byte-preserving writer and
  write UTF-8 properly (a "\xe2\x80\x94" string once became mojibake in STATUS; repaired).

## Open items outside MODULE-005

- Design gap (D017): find() in a folder larger than its entry budget returns no items, because
  listing is all-or-nothing. Needs a partial/streamed listing contract (own module) before agents
  rely on search in large folders.
- Then MODULE-006 service (5 tools, per-agent tokens and caps, deadlines, audit, health), then the
  6-agent soak test, then Google Drive.
- Relay improvement offered to Dolesh, not built: after a host restart, stop the old chat before the
  automatic re-run; and a send-and-check mode for pushing agents so modules can overlap.
- Security housekeeping: BH_TOKEN, viewer password and the Sarvam API key were pasted in chat:
  rotate them when the host moves to Dolesh's PC. Google Cloud shows an overdue balance; the VM may
  be suspended without warning.
