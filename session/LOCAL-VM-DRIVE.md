# Local VM Drive integration — 2026-10-16

Installed on `brave-host` using browser-tool `b50a2f150639e52c2c48d784b4196b26bd93cdf4` host modules and drive-tool `722c94f9b084255e17c95be1d124614caa5d6da8`.

- `bh-drive` enabled at boot, listening only on `127.0.0.1:8940`.
- Local data: `/var/lib/agent-drive/projects` in the VM disk on Windows E:.
- Host API exposes `drive_list`, `drive_find`, `drive_read`, `drive_save`, `drive_append`; credentials remain in the VM's root-only environment file.
- Separate local hub identity and local data; no cloud-to-local replication or Google Drive backup configured.
- Updated installed `agent.py`, `hostd.py`, `relay.py`, `scripted.py`, `drive.py`, `chat.html`. Existing browser profile, viewer gateway and tunnel services retained.
- Four local lanes are now enabled: 0=Sarvam/general, 1=Gemini, 2=DeepSeek, 3=Grok. Separate MCP services listen on loopback ports 8931–8934, sharing the existing Brave profile.
- The VM has its own local-only Git bus at `/var/lib/browserhost/local-bus`, branch `local-bus`, with a local bare origin `/var/lib/browserhost/local-bus-origin.git`. It does not consume the cloud host's GitHub queue. Queue task files there and commit/push as `bh`; results return to `tasks/<id>.result.md`. `project:` headers use the higher-level relay and automatic local Drive records/history.
- The existing `/opt/browser-tool/local-relay.py` remains the low-level direct-chat launcher. It does not parse `project:` headers; automatic task records/history require the higher-level `Relay` task path, whose updated implementation is installed. Do not claim that the low-level launcher automatically saves every chat to the drive.

Verification performed on the VM: 31 existing browser drive tests and 22 existing serve-launcher tests passed. The wrapper suite emitted an unclosed test-server socket ResourceWarning. Live authenticated host API save/read/list/find/append succeeded, traversal failed, unauthenticated access returned 401, and installed `relay.project_memory()` loaded the saved memory. `bh-drive`, `bh-hostd`, `bh-brave`, `bh-mcp`, viewer gateway and tunnel were active; local viewer returned HTTP 200. No live chatbot relay task was needed or claimed for these checks.

Recovery backup: `/var/backups/browser-drive-20261016T143523Z`. Existing test logs: `/var/tmp/local-drive-install-Ir27jG/`. Synthetic verification project: `local-integration-check`.

Four-lane deployment check: hostd logged all four routed lanes, and four tasks opened their respective sites in separate windows. DeepSeek and Grok reported `blocked` because those sites are not signed in locally; their task records and project history were saved to the local Drive. No login bypass was attempted. Lane service processes plus the browser used about 1.0 GiB at idle and 1.4–1.7 GiB during these short checks, within the unchanged 4 GiB VM allocation. End-to-end task results remain in the local bus.

After the user signed into DeepSeek and Grok, a second four-lane exercise completed **4/4 done** on 2026-10-16, 14:49–14:51 UTC. Each agent contributed a distinct short operating-guide section: Gemini design rules (3.5 Flash-Lite/low, routine draft only), DeepSeek task template, Sarvam evidence checklist, Grok practical review (Fast). All used `project: four-lane-demo`, loaded its MEMORY.md and saved a separate bare answer to `notes/<agent>.md`. The local Drive directly contained all four notes, four full task records and exactly four history lines. Start windows opened within 13 seconds; final completion was about 2 minutes 13 seconds after queue submission. Gemini and Grok needed model-picker retries, so this is a successful short functional exercise, not a sustained reliability or security-isolation claim. About 1.7 GiB RAM used afterward; roughly 2.2 GiB available.
