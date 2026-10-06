# Connections and operating procedure (no secrets)

## Browser host
Related repo: https://github.com/Dolesh-avkalan/browser-tool
Known direct host: https://34-89-54-103.sslip.io
Browser viewer: https://34-89-54-103.sslip.io/view/vnc.html?path=view/websockify&autoconnect=1&resize=scale
These addresses belong to a temporary VPS and must be checked again in later sessions.
Current direct/tunnel endpoints and health report are available on browser-tool's browser-bus branch, in endpoint.json and status/box.md.

REST uses Authorization: Bearer <BH_TOKEN>. The token is not stored in drive-tool.
Credentials live on the host in /etc/browserhost.env; viewer uses BH_VNC_PASSWORD, not BH_TOKEN.
Owner SSHes into the VPS. The previous session used sudo.ws on its Ubuntu image.
Never fetch or publish the whole credential file. Restore access with only the necessary secret via the owner's chosen secure mechanism.

## API Sarvam messenger
GET /api/chat/status -> running, configured, model, events, epoch.
POST /api/chat with JSON {"message": "..."} starts a task when idle or queues steering while running.
GET /api/chat/events?after=N&epoch=E returns next, running, epoch and events; update the cursor from the response.
POST /api/chat/stop requests stop. POST /api/chat/reset resets conversation and can reset browser tabs on the next task; do not reset a logged-in session casually.
Fetch status first. Avoid steering an existing task unless intentionally authorized.
Events/replies persist on the host; no automatic ping to Codex is configured.

For MODULE-001, historical epoch=19; dispatch followed event 56; idle event=102.
Use current status in future sessions, because reset/restarts can change cursors.
The messenger reported conversation URL:
https://indus.sarvam.ai/indus/c/01M4925D25XJX7MQR2ZHGWMKG0
This URL was messenger-reported, not independently revisited by the director.

## Browser task delivery
Sarvam web URL: https://indus.sarvam.ai/
List tabs, use the logged-in Sarvam tab or navigate the available blank tab. Do not log out or reset the profile.
Send the pinned brief and demand a small factual confirmation (task ID, method names, branch) to establish it was read.
If inaccessible, supply exact brief contents. Wait for completion with browser_wait_stable; collect actual results.
The API messenger operates the browser only; do not tell it to implement the module.
Verify returned commit via GitHub. A browser chatbot may have sandbox and GitHub connector capabilities, but confirm each task's evidence rather than assume them.

## Git bus fallback
browser-tool supports request JSON files on browser-bus with matching responses. It was verified for direct browser tool calls in this session.
It does not by itself expose the built-in agent's chat API. Use REST for messenger instructions.
Pushing browser-tool main triggers host self-update; do not modify that repo for drive-tool work.
