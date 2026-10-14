I'll start with reconnaissance: check sandbox capabilities, find the GitHub/fetch tools, and set up a plan.

Now let me read the test file and the evidence doc, then run the suite.

Now let me run the committed suite on the actual interpreter available (3.12.13), capturing everything.

python3.11 exists. Let me run on both interpreters and capture skip detail.

Both interpreters available — 89 tests, OK, exit 0, zero skips on each. Now let me establish the platform probe state and per-file test counts.

Now let me inspect the base implementation's seams and the two target methods, to design a check that runs against both trees.

Base bug confirmed. Now I'll write the primary fresh ownership check with a virtual-fd lifetime ledger (acquisition-tracked, not fd integers).

Core handoff scenarios behave exactly as required (PASS on candidate, FAIL on base). Two harness artifacts to fix: (a) the "earlier traversal" scenario legitimately never acquires the child, and (b) my root-usability probe still had injection active. Let me correct these.

Ownership check complete: 11/11 pass on candidate, 9 fail on base. Now the probe-coupling check — let me read the relevant test harness first.

Now the probe-coupling / platform-denial check:

Two "failures" are artifacts of simulating an unsupported kernel on a kernel that actually enforces: I patched only the test module's probe, not the production one, and the raw-kernel cross-check test legitimately detects that mismatch. Let me make the simulation consistent and assert the correct property (denial tests execute, not skip).

Candidate probe check PASSES; base Part A shows the probe is decoupled (the d010 bug) and base Part B crashes because the old test module lacks the static/runtime split. Let me make the base path report cleanly.

Probe coupling verified: candidate PASS, base FAIL (decoupled). Now the operational containment checks with fresh fixtures.

All 14 operational checks passed; the fd-count loop just caught the wrong base exception (NotFoundError is a StorageError, not LocalStorageError). Fixing.

Operational checks: 15/15 pass on both trees. Now the d009 cross-check — let me locate the d009 scripts.

The d009 scripts live in a separate drop repo. Let me fetch it to see what's actually available for the secondary cross-check.

The drop repo has the d009 scripts and the d010 delivery. Let me verify the d010 drop files match the candidate blobs, then run the d009 scripts as a secondary cross-check.

All three d010 drop files match the candidate blobs exactly. Now run the d009 scripts as the secondary cross-check against the candidate tree.

Secondary cross-check complete: all six d009 scripts pass on the candidate on both interpreters, and d009's fd_cleanup independently fails on base (child leaked) — matching my fresh check. Now a targeted mutation test to prove my checks have teeth beyond "base is old".

M1 caught (6 ownership failures). But M2 slipped through — my coupling check only tested "weakening the helper ⇒ probe False", which a decoupled probe that is always False would coincidentally satisfy. That's a genuine weakness. Let me add a strong, direct coupling assertion: the probe must open with the helper's actual flags.

The strong check works (M2 and base now caught), but candidate shows stray non-probe os.open calls. Let me identify them before finalizing the filter.

The strays are tempfile/shutil internals, not the probe. Filtering on the probe's own opens (those carrying O_NOFOLLOW) makes the check exact.

Both checks now have teeth (base and M2 caught; candidate passes). Let me record the environment and assemble the deliverable. First, the environment and final consolidated runs.

Now the consolidated battery, including candidate tests against the base implementation (to reproduce the d010 "5 failures" claim independently).

All consistent. Let me capture the new-tests-on-base runs with correct exit codes, then assemble the deliverable.

Let me check for a GitHub write connector to surface (push is blocked), then write the report.

Now the final report.

Now assemble the manifest and zip bundle.

Done. Here's the outcome of the d012 independent execution.

PUSH_BLOCKED. No GitHub write connector is bound to this worker (only the read-only public repo fetcher; gh unauthenticated), so nothing was written to Dolesh-avkalan/drop or any branch. I've surfaced a GitHub connector you can enable on a later turn. Everything is delivered as files instead.

Candidate verified from scratch. 80bf5488… on feature/module-003-resource-fixes, base c2b324da…, brief from main 440d0f69…. The two source blob identities match the brief exactly (local.py 5da22dd4…, test_local.py b3319752…), and a recursive tree diff confirms only those two files plus the new docs/evidence/MODULE-003-RESOURCE-FIXES.md differ from base — storage.py and __init__.py are byte-identical. The d010 delivery in drop carries the same blobs, so it corresponds to this commit.

Committed suite, both interpreters. Python 3.11.2 and 3.12.13 are both present and both used: Ran 89 tests … OK, exit 0, zero skips on each. The prior "89 passing" is independently confirmed. Running the candidate's tests against the base implementation gives FAILED (failures=5) on both — exactly the four handoff regressions plus the probe test, reproducing the d010 claim.

Fresh ownership check (my own code, outside the checkout). It tracks descriptors by acquisition (unique virtual ids), not fd integers, and injects real-close-then-error and ambiguous-close failures. Candidate: 11/11 pass. Base: 9 fail (child orphaned on every handoff scenario). A mutant that drops the new child cleanup: 6 fail. It exercises shallow/deep parent-handoff close failure for read and scan, multiple close failures, an earlier traversal close failure, primary read/scan failure combined with close errors, and ambiguous closes (no retry, no claimed release). So the child-handoff check fails on c2b324d and passes on the candidate.

Probe coupling + denial gating. The probe now opens with the production helper's actual flags (marker-spy proves it): candidate PASS, base FAIL (decoupled — it rebuilds flags inline), and a decoupled mutant is caught. With static facilities present and the runtime probe forced false, the platform-denial tests still run (skipped 0) while operational tests skip with a precise reason; the one suite failure under forced-false is the raw-kernel cross-check test, expected because the simulation lies about a kernel that actually enforces.

Operational containment, 15/15 (symlink final/intermediate, listing omission, root rename anchoring, bounded read below/exact/above, growth-after-fstat bounded to max+1, FIFO no-hang, no fd leak over 200 cycles) — identical on base, since these were already correct. d009 secondary cross-check: all six scripts pass on the candidate on both interpreters; d009's fd_cleanup independently fails on base, matching my finding.

Two files are in your downloads: d012-sarvam-independent-tests.md (full report) and d012-independent-tests.zip (the three check scripts, all raw logs, MANIFEST.md with sizes and sha256, laid out for the intended drive-tool/module-003/d012-independent-tests/ path). Limits are stated honestly in the report: gVisor/9p environment, conformant-kernel behaviour inferred not executed, ambiguous close documented rather than claimed, and conformant cloud-kernel / MODULE-002 integration remain later gates.
