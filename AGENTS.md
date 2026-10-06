# Instructions for future directors and agents

Start with session/START_HERE.md, then session/STATUS.md, session/DECISIONS.md, session/PROGRESS.md, and session/CONNECTIONS.md. Read README.md for architecture and tasks/ for the active brief.
These files record this project's continuity. Verify live repository heads and host status before acting; a checkpoint is historical evidence, not proof that a service is still running.

## Owner's working agreement
- Codex is the director. Codex owns plans, architecture, specifications, acceptance criteria, verification, and integration decisions.
- Delegate implementation and test execution to browser chatbots. Do not silently become the implementer or test runner.
- API Sarvam is the browser messenger; logged-in browser Sarvam is the initial implementer.
- Keep modules small. The owner authorizes a two-module batch: easy MODULE-002 on the existing Sarvam thread and harder MODULE-003 on Gemini 3.1 Pro in a separate tab. Keep outputs and branches isolated.
- Stabilize on the VPS before deploying a tagged stable version to the PC.
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
