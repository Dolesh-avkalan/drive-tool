# Instructions for future directors and agents

Start with session/START_HERE.md, then session/STATUS.md, session/DECISIONS.md, session/PROGRESS.md, and session/CONNECTIONS.md. Read README.md for architecture and tasks/ for the active brief.
These files record this project's continuity. Verify live repository heads and host status before acting; a checkpoint is historical evidence, not proof that a service is still running.

## Team working agreement
- Codex is the director. Codex owns plans, architecture, specifications, acceptance criteria, verification, and integration decisions.
- Delegate implementation and test execution to browser chatbots. Do not silently become the implementer or test runner.
- Use the local scripted browser relay. Gemini model `4 argon` handles complex implementation/review; logged-in browser Sarvam handles implementation, assembly and test execution. Routine work does not require the API-agent messenger; use it only for authorized repair when necessary.
- Keep modules small and outputs/branches isolated. Use useful existing implementation conversations for follow-ups and fresh contexts for independent review/execution. Read the current STATUS.md before dispatching; do not repeat a live task. Historical MODULE-002/003 concurrent batch and Gemini 3.1 Pro instructions are superseded by the 2026-10-14 project direction.
- Design for stable, light, modular, predictable behavior: bounded operations, explicit ownership/errors, minimal dependencies and no speculative frameworks. Codex owns that design; Gemini and Sarvam handle implementation and testing.
- Establish confidence through team artifacts, independent review and integrated synthetic tests before cloud-box testing. Preserve the existing browser VPS and its services; the current read-only core's synthetic test on directortemp passed on 2026-10-15. Further deployment or changes require their own authorization.
- Persist progress, decisions, blockers, evidence, and next steps in session/ after every meaningful milestone and before ending a work session.

## Evidence and changes
- New assignments use a short chat instruction plus versioned task/source files. Prefer attachments when the site's upload UI works; otherwise supply pinned GitHub file references and require the teammate to retrieve and confirm their contents. Avoid pasting full source and long briefs into chat unless file delivery is genuinely unavailable. Existing chats/evidence remain historical.
- Check actual repository artifacts and commits, not just chatbot claims.
- Distinguish observed facts, team-reported test execution, director review, and independent verification.
- Never label a result independently tested without independent execution evidence.
- Keep implementation on its feature branch until review acceptance. Do not invent successful pushes or test output.
- Task specs must include exact scope, filenames, branch, acceptance criteria, test commands, evidence requirements, and honest blocked outcomes.
- Preserve the existing browser-tool host and services.
- Never commit credentials, cookies, private user data, or unredacted secrets. This repository is public.

## Checkpoint maintenance
Update STATUS.md in place. Append dated entries to PROGRESS.md and numbered decisions to DECISIONS.md, marking superseded choices rather than erasing history. Put detailed session notes under session/history/. Link evidence at pinned commit URLs when possible.

## Communication with Gemini and Sarvam
- Codex leads the project and takes responsibility for design, assignments, review, delivery and blockers. Address Gemini and Sarvam by name or their concrete role (developer, reviewer, tester); do not call them workers or label Dolesh the owner in new briefs or messages.
- Speak directly as their team lead: "I need you to implement this", "I will review it", "I will get approval", or "This will take a while; continue with the independent work."
- Keep responsibility with Codex. Do not frame a delay as Dolesh blocking the team or requiring them to manage him. Explain the dependency and what the team can do next.
- This is a communication convention, not a change to authorization: Dolesh's instructions and required approvals still govern consequential actions. Preserve historical reports as evidence; apply this convention to future communication and current instructions.

## Standing merge authorization — 2026-10-15 (D025)

Dolesh explicitly instructed: "dont ask me to merge ... if you are confident, then just do it". For Drive Tool, Codex is authorized to merge implementation PRs without a per-PR approval question once the applicable review, test/evidence and acceptance gates pass. Verify the exact reviewed/tested head and use expected-head protection; resolve blocking findings before merging and report the outcome afterward. This supersedes earlier per-merge approval requirements in this repository, including historical task briefs and decisions. It does not itself expand deployment, credential, real-storage or public-exposure authorization.

## Model/effort selection — 2026-10-15 (D030)

Dolesh explicitly leaves model/effort choice to Codex and notes Gemini3.8 is usable and Argon High is not required for every assignment. Match capability/effort to the task: routine assembly, tests, straightforward fixes and drafting use capable available lighter models or lower effort; reserve Argon/high for difficult architecture, security/authorization and adversarial reviews. Verify actual picker selection/availability, keep provenance, never silently downgrade an explicitly pinned review or invent unsupported effort choices. This supersedes any blanket preference to use4argon/high on every Gemini task. No quality gates are removed.

## Role routing — latest user instruction
Gemini owns design, architecture and security work. Sarvam and DeepSeek handle routine implementation, assembly, tests and focused non-architecture checks. Sarvam design drafts are input only; route final design/security decisions through Gemini and director acceptance.

Efficiency routing: prefer DeepSeek for routine coding/fixes/focused checks; reserve Sarvam especially for internet/GitHub publishing, assembly/test execution and cloud evidence analysis. Gemini retains design/security. Parallelize only independent tasks across the two serialized browser hosts.

Latest routing refinement: DeepSeek is first choice for routine implementation/fixes while available. Prefer Sarvam for short scoped assignments, internet/GitHub-dependent tasks, and routine fallback when DeepSeek hits limits. Keep Sarvam assignments small; split assembly/publication/execution stages when useful. Gemini owns architecture/design/security. This supersedes any broader default to Sarvam for routine coding.

Sarvam is also suitable for work whose turnaround time is not important, including independent background verification and long evidence runs when these do not block the critical path.

Sarvam subagents: user explicitly authorizes Sarvam to spawn subagents when useful for independent, non-overlapping implementation/test/retrieval work. Parent retains exact pins/scope, verifies raw results and publication; no duplicate live tasks, competing selected-tab actions, secret sharing or authority expansion. Prefer small bounded delegations; do not spawn merely to keep busy.
