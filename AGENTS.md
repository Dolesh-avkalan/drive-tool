# Instructions for future directors and agents

Start with session/START_HERE.md, then session/STATUS.md, session/DECISIONS.md, session/PROGRESS.md, and session/CONNECTIONS.md. Read README.md for architecture and tasks/ for the active brief.
These files record this project's continuity. Verify live repository heads and host status before acting; a checkpoint is historical evidence, not proof that a service is still running.

## Owner's working agreement
- Codex is the director. Codex owns plans, architecture, specifications, acceptance criteria, verification, and integration decisions.
- Delegate implementation and test execution to browser chatbots. Do not silently become the implementer or test runner.
- Use the local scripted browser relay. Gemini model `4 argon` handles complex implementation/review; logged-in browser Sarvam handles implementation, assembly and test execution. Routine work does not require the API-agent messenger; use it only for authorized repair when necessary.
- Keep modules small and outputs/branches isolated. Use useful existing implementation conversations for follow-ups and fresh contexts for independent review/execution. Read the current STATUS.md before dispatching; do not repeat a live task. Historical MODULE-002/003 concurrent batch and Gemini 3.1 Pro instructions are superseded by the 2026-10-14 owner steering.
- Design for stable, light, modular, predictable behavior: bounded operations, explicit ownership/errors, minimal dependencies and no speculative frameworks. Codex owns that design; workers perform the labor.
- Establish confidence through worker artifacts, independent review and integrated synthetic tests before the owner's later cloud-box test. Do not assume the old browser VPS is the test target or deploy to it. The browser workload now runs locally; preserve the existing VPS until separately authorized.
- Persist progress, decisions, blockers, evidence, and next steps in session/ after every meaningful milestone and before ending a work session.

## Evidence and changes
- Check actual repository artifacts and commits, not just chatbot claims.
- Distinguish observed facts, worker-reported test execution, director review, and independent verification.
- Never label a result independently tested without independent execution evidence.
- Keep implementation on its feature branch until review acceptance. Do not invent successful pushes or test output.
- Task specs must include exact scope, filenames, branch, acceptance criteria, test commands, evidence requirements, and honest blocked outcomes.
- Preserve the existing browser-tool host and services.
- Never commit credentials, cookies, private user data, or unredacted secrets. This repository is public.

## Checkpoint maintenance
Update STATUS.md in place. Append dated entries to PROGRESS.md and numbered decisions to DECISIONS.md, marking superseded choices rather than erasing history. Put detailed session notes under session/history/. Link evidence at pinned commit URLs when possible.
