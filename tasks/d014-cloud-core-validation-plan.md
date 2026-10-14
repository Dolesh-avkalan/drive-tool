# d014 — existing cloud-box core validation plan

PREPARED ONLY. The owner tentatively prefers an existing box after local worker confidence. No test candidate is accepted or cloud operation authorized by this file alone. Director must supply the exact accepted integrated commit and actual connection method before dispatch.

Known historical candidate host: old browser VPS at 34.89.54.103. Its public /api/health returned ok during this session. Browser-bus report at eab1d40 (updated 2026-10-14T17:38:35Z) reports nine active browser services, about 8 GiB RAM and 726 MiB free disk. This is a periodic report, not direct live disk inspection. The host API is not a shell; no SSH credential or connected remote device is available in this local session. Do not infer access from reachability.

Worker plan once explicitly dispatched with a connection:

1. Read-only preflight: identify host, OS/kernel, Python versions, filesystem, free disk/RAM and existing browser service health. Confirm it is the intended box. Stop and report if a tiny isolated test does not fit. Do not alter services, updater, profiles, credentials or private storage.
2. Use an operator-approved disposable path, a pinned source archive and synthetic fixtures only. Budget <=50 MiB total new files and <=1 MiB per read unless a specifically bounded fixture checks the limit. No dependencies/package installation, daemon/API deployment, mount changes or production paths. Capture baseline and final space/health.
3. Run the integrated committed suite on available supported Python versions; distinguish missing interpreters, explicit unsupported-platform rejection and skipped tests. Record commit/tree/blob hashes and actual commands/stdout/stderr/exits/counts. Test filesystem/kernels on this real target, not the browser worker's sandbox.
4. Execute tiny timeout-bounded real no-follow/dir-swap/root-rename/FIFO/bounded-read/descriptor-lifetime checks supplied with the accepted candidate. Do not require old insecure behavior on a conformant kernel. Distinguish mechanism proof from stress tests and record actual kernel semantics.
5. Keep artifacts for director inspection; clean only the explicitly created disposable directory after resolving/validating its full path and the approved workspace boundary. No broad recursive deletes. Recheck browser health and resource delta.

This validates only the read-only core on a conformant cloud target. It does not establish full Google Drive/OAuth/write/job/API behavior or a production release. A later service task must specify authentication, listing/search budgets, transfer recovery and startup behavior before a service restart test is meaningful.
