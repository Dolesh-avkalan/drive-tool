# Two-hour usage audit — 2026-10-06

Requested window: 2026-10-06 21:39:29–23:39:29 Asia/Kolkata (16:09:29–18:09:29 UTC), anchored to the user's request timestamp.

Fetched all 524 retained events from epoch 19. Filtering timestamped events to the window yields 139 Thinking model-processing steps, 125 browser tool calls/results, 16 user/instruction messages, and one quota error. Tool breakdown: browser_evaluate 38, browser_tabs 26, browser_click 20, browser_navigate 11, browser_wait_stable 11, browser_type 9, browser_find 4, browser_file_upload 3, browser_press_key 2, browser_take_screenshot 1. Model-processing steps are not a verified billable-call count. No per-call usage entries exist in retained events.

Read browser-tool source at main commit 5905582914d90c437acd48eb3e198fd2e04e8e79, browser_host/agent.py: constructor loads aggregate usage from persisted agent.json; _sarvam increments aggregate calls/prompt_tokens/completion_tokens from provider response; _save overwrites aggregate state; reset clears conversation/events/notes but DOES NOT reset usage. Counters survive conversation resets and service restarts. Thus 34,819,781 input and 589,222 output tokens refer to the persisted host counter lifetime, NOT specifically this session or two-hour window. Counter start date remains unknown.

Exact last-two-hour token usage cannot be reconstructed from these host records: no baseline at window start or timestamped usage ledger is available. Provider dashboard/export with hourly/request usage would be required for an exact figure. Do not use all-time average tokens per call to fabricate a window estimate. Future per-request timestamped token logging is proposed, not implemented. No paid model requests were made for this audit.
