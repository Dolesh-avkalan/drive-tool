# Local VM Drive integration — 2026-10-16

Installed on `brave-host` using browser-tool `b50a2f150639e52c2c48d784b4196b26bd93cdf4` host modules and drive-tool `722c94f9b084255e17c95be1d124614caa5d6da8`.

- `bh-drive` enabled at boot, listening only on `127.0.0.1:8940`.
- Local data: `/var/lib/agent-drive/projects` in the VM disk on Windows E:.
- Host API exposes `drive_list`, `drive_find`, `drive_read`, `drive_save`, `drive_append`; credentials remain in the VM's root-only environment file.
- Separate local hub identity and local data; no cloud-to-local replication or Google Drive backup configured.
- Updated installed `agent.py`, `hostd.py`, `relay.py`, `scripted.py`, `drive.py`, `chat.html`. Existing browser profile, viewer gateway and tunnel services retained.
- Kept `BH_LANES=`: this VM still has one MCP/browser execution lane. No cloud bus attached, preventing duplicate consumption of cloud tasks.
- The existing `/opt/browser-tool/local-relay.py` remains the low-level direct-chat launcher. It does not parse `project:` headers; automatic task records/history require the higher-level `Relay` task path, whose updated implementation is installed. Do not claim that the low-level launcher automatically saves every chat to the drive.

Verification performed on the VM: 31 existing browser drive tests and 22 existing serve-launcher tests passed. The wrapper suite emitted an unclosed test-server socket ResourceWarning. Live authenticated host API save/read/list/find/append succeeded, traversal failed, unauthenticated access returned 401, and installed `relay.project_memory()` loaded the saved memory. `bh-drive`, `bh-hostd`, `bh-brave`, `bh-mcp`, viewer gateway and tunnel were active; local viewer returned HTTP 200. No live chatbot relay task was needed or claimed for these checks.

Recovery backup: `/var/backups/browser-drive-20261016T143523Z`. Existing test logs: `/var/tmp/local-drive-install-Ir27jG/`. Synthetic verification project: `local-integration-check`.
