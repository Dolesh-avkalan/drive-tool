# MODULE-003 — bounded read-only local adapter (Gemini, harder)

Repository: https://github.com/Dolesh-avkalan/drive-tool
Worker: Gemini web chatbot, model Gemini 3.1 Pro selected by messenger.
Base commit: 194d4ad57de6dd895c6eaaf7faf4d08583eab02b (MODULE-001, not main).
Branch for Sarvam's later submission: feature/module-003-local-readonly
Commit message: Implement MODULE-003 bounded local read-only adapter from Gemini

## Role and output
You implement this module and write tests. You cannot push to GitHub in this workflow. API Sarvam captures your complete output and gives it to browser Sarvam, which assembles the files, executes tests in its sandbox, and pushes the separate branch. Return complete files, not sketches.
Read MODULE-001 source at the pinned base. If you cannot fetch it, request it; do not guess imports.
No real VPS or PC filesystem access, service changes, OAuth, writes/deletes, or network API. Tests use temporary synthetic directories only.

## Exact additions
- drive_tool/local.py
- tests/test_local.py
- docs/evidence/MODULE-003-GEMINI.md
Do not change existing MODULE-001 files.
Sarvam later adds docs/evidence/MODULE-003.md with its own real test run and transport provenance.

## Contract
Python 3.11+, standard library only; Linux/POSIX capability-gated initial implementation.
LocalAdapter(source_id: str, root: str | os.PathLike, *, max_read_bytes: int = 1048576) implements stat, list_children, read_bytes from StorageAdapter; support close() and context manager.
source_id nonempty str; max_read_bytes int >0 (bool invalid); invalid config raises ValueError.
Root must be an absolute existing directory, with no symlink in any component. Establish a directory descriptor through no-follow traversal from /; retain it until close. Missing required descriptor/no-follow platform features raise UnsupportedPlatformError clearly; do not emulate weaker checks silently.
FileRef(source_id, "root") is the synthetic root.
All real entry IDs have prefix "p/" followed by canonical relative POSIX path, e.g. "p/notes/report.txt". This avoids collision with a real filename "root".
Reject malformed IDs, absolute paths, empty/dot/dot-dot components, repeated/trailing slashes, NUL and backslash with UnsafePathError. Top-level real filename root has ID "p/root".
Use FileMetadata from MODULE-001. Root: name="", parent_id=None, kind="directory", size_bytes=None.
Entry: basename name; parent_id="root" for top-level, otherwise "p/"+relative parent; kind="file" or "directory"; size_bytes actual file size or None for directory.
Every public storage operation checks source mismatch FIRST (even a malformed/unknown ID). Wrong source raises existing SourceMismatchError.

## Containment and bounded reads — mandatory
A string resolve()/startswith() check is not sufficient. Traverse relative directory components using anchored descriptors and no-follow opens; do not follow symlinks for intermediate or final paths.
Do not let symlink replacement between checks/opens redirect access outside the root.
Root descriptor anchors the initially opened directory even if its pathname is renamed/replaced later.
Use descriptor lifetime management so repeated operations/failed traversals do not leak descriptors. Define operations after close to raise AdapterClosedError (source mismatch still first). Concurrency with close need not be supported; document this.
Only regular files and directories are supported. Reject reading FIFOs/devices/sockets without blocking: opening must not hang before type checking. Use safe nonblocking/type-check strategy, and explain it.
Directory listing returns supported regular files/directories sorted by name; omit symlinks/special entries. An entry disappearing during listing may be skipped. Do not follow it while collecting metadata.
stat/read on symlink raises UnsafePathError; on special entry raises UnsupportedEntryError.
read_bytes on root/directory raises existing IsDirectoryError; list_children on file raises existing NotDirectoryError; nonexistent canonical path raises existing NotFoundError.
Use LocalStorageError(StorageError) for permission/other OS failures with useful context and preserved cause. Specific local errors (UnsafePathError, UnsupportedEntryError, ReadLimitExceededError, AdapterClosedError, UnsupportedPlatformError) extend LocalStorageError.
ReadLimitExceededError when file exceeds max_read_bytes: check fstat and enforce during reading with at most max_read_bytes+1 bytes read, so a file growing after stat cannot bypass the limit. No unbounded read and no allocating proportional to untrusted file size.
Explicitly document that external writers may change file contents during a read; this module does not promise a coherent snapshot/checksum. Hard links, mount policy, and adversarial root provisioning are outside this module's protection; configured roots must be operator-controlled. Do not claim production security completeness.

## Acceptance/tests
Use unittest, tempfile, tiny fixtures and deterministic tests:
- Nested stat/list/read; exact binary/empty files; metadata/parent IDs; real filename root.
- Sorted listing, empty directory.
- Source mismatch precedence across all three methods.
- Missing entries; directory/file operation errors; all malformed ID classes.
- Symlink to file and to directory outside the configured root cannot be read/traversed; listing omits them. Final and intermediate symlinks tested.
- Symlink root/ancestor rejected at configuration.
- Root renamed and its old pathname replaced by outside symlink: old adapter stays anchored to original directory.
- Read limit below/exact/above threshold and enforcement against simulated growth (deterministic bounded-read unit seam if needed; explain what it proves).
- FIFO/special entry test cannot hang; use timeout-bounded child process if needed.
- Close/context manager cleanup and failure-path descriptor cleanup with a bounded Linux fd-count test or deterministic mock.
- Explicit unsupported-platform gate; existing MODULE-001 tests unaffected.
- Include a deterministic check/symlink-swap adversarial test where feasible; distinguish mechanism review from stress-test proof.
Tests must be skippable with explicit reason where a required platform facility is absent; never silently claim those skipped tests prove containment.

## Deliver and evidence
Return each exact path and full file contents in fenced blocks, or downloadable individual files PLUS accessible full source if download cannot be captured. No ellipses, truncated files, or executable payload hidden in links.
MODULE-003-GEMINI.md: design rationale, threat boundaries, tests written, any actual execution command/log/runtime, and unresolved risks. If you cannot execute code say TESTS_NOT_RUN; Sarvam will execute it separately.
Final status: GENERATED or BLOCKED; include source filenames, exact base, tests-run/not-run, limitations. Do not claim a push.
Sarvam's later testing report must identify your source and any fixes separately. Security review and VPS integration remain director gates after submission.
