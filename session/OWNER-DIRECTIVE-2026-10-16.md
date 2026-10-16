# OWNER DIRECTIVE — 2026-10-16 ~06:05 UTC — read before any further work

From Dolesh (owner). Dolesh is away from his home PC and cannot reach Codex directly, so he asked
Claude (his cloud Claude Code session) to write this where Codex will certainly read it. It is
Dolesh's instruction and overrides earlier plans, gates and heartbeat steering where they differ.

Dolesh's view: "we just need a file system" for his agents. The core is good; the hardening has
gone beyond what this personal tool needs. Change course:

1. **Stop the harsh-test loop.** Do not rerun the D110/D118 harsh package and do not start more
   harness-correction rounds. Let the currently running D122 task finish or stop it at a safe
   point, save its evidence as-is, and record it as DEFERRED (not PASS, not FAIL of the product).
   The harsh stages (memory caps, ENOSPC, EMFILE, crash recovery, hostile HTTP) are no longer
   release gates; they become optional future hardening.
2. **New release gate for the local agent service (keep it simple):** the existing suite on main
   passes, plus ONE realistic normal-use run: 6 concurrent agent clients doing ordinary
   find / list / read / save calls against the HTTP service (the existing 600-call smoke is
   enough; the 10,000-call soak is optional). If that passes, mark the local agent service
   USABLE and stop hardening it.
3. **Google Drive: do not build a custom connector from scratch.** First write a short design
   note (no code) evaluating rclone (already supports Google Drive: list, copy, move, sync) behind
   the same small set of agent tools, keeping drive-tool's local containment core for local
   roots. Bring that note to Dolesh before implementing.
4. **Less churn.** No checkpoint-only commits; commit when something meaningful changes. Keep a
   plain-English summary of at most 10 lines at the top of session/STATUS.md (whole words, no
   run-together text) so Dolesh can read it at a glance.
5. **PC shutdown:** the earlier authorization stands. Once D122 is finished or stopped and its
   evidence is saved and pushed, shut down the home PC as previously authorized (PC only, never
   the cloud boxes), after posting the final notification.

When you have read this, add one line under it: "Read by Codex at <UTC time>; plan: <one line>".
