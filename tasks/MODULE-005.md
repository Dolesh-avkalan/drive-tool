# MODULE-005 — safe local writes: atomic, no-overwrite save and mkdir

Repository: https://github.com/Dolesh-avkalan/drive-tool (public)
Base: main at af42edb (MODULE-004 merged).
Branch: feature/module-005-local-write (created from main by Sarvam)
Design owner and reviewer: Claude (director). Team:
- Gemini 4 Argon: design review, edge cases, test plan (d025); code review (d028).
- DeepSeek: implementation and tests (d026).
- Sarvam: assembly, tests, mutation check, push, evidence (d027).

## Why

Agents must be able to save files (session/AGENT-INTERFACE.md, D016). A save must never leave a
half-written file under the final name, never silently replace an existing file, never write
outside the configured root, and two agents saving the same name at the same moment must get one
clear winner and one clear "exists" error. Writes are the first mutation in this project: they are
security-sensitive and get the same containment rigour as MODULE-003.

## Scope (exact)

Add: drive_tool/local_write.py, tests/test_local_write.py.
Change: drive_tool/storage.py (two new error classes only), drive_tool/local.py (ONE line, below).
Add: docs/evidence/MODULE-005.md (Sarvam's execution evidence).
Do not change any other file. Existing 175 tests must pass unchanged.
Python 3.11+, standard library only, POSIX only (same platform gate as LocalAdapter). No network,
no OAuth, no service code, no threads in the implementation.

## Contract

1. storage.py: add `AlreadyExistsError(StorageError)` and `ReadOnlyError(StorageError)` and add both
   to `__all__`. Nothing else in storage.py changes.
2. local.py: the only change is in `LocalAdapter.list_children`'s odd-name filter: also skip names
   starting with `TEMP_PREFIX` (imported from drive_tool.local_write would be circular: define
   `TEMP_PREFIX = ".drive-tool-tmp-"` in local.py next to PATH_PREFIX and import it in
   local_write.py). Deliver this as an exact two-part edit: the constant line to add after the
   `PATH_PREFIX = "p/"` line, and the filter condition with `or name.startswith(TEMP_PREFIX)` added.
   Temp files still count toward the MODULE-004 entry limit (counting happens before the filter).
3. local_write.py: `class LocalWriter` wrapping an open `LocalAdapter` (writes are only possible for
   code that builds a writer; a plain adapter stays read-only).
   `LocalWriter(adapter, *, max_write_bytes=10_485_760)`; int >= 1, bool rejected, ValueError.
   It may use the adapter's internal helpers (`_check_source_and_state`, `_traverse_parent_dir`,
   `_classify_component_open_error`, `_build_metadata`, `_release_owned`, `_os_*` seams); it must
   not duplicate the containment logic. Add `_os_*` seams of its own for write/fsync/link/rename/
   unlink/mkdir so tests can inject failures.
   Methods (each checks source mismatch first, then closed adapter, then arguments):
   - `mkdir(parent: FileRef, name: str, *, exist_ok: bool = False) -> FileMetadata`
   - `write_bytes(parent: FileRef, name: str, data: bytes, *, overwrite: bool = False) -> FileMetadata`
   `parent` is the directory to create in ("root" or a "p/..." directory). `name` is a single
   component: non-empty str, not "." or "..", no "/", "\\" or NUL, at most 255 bytes UTF-8, and not
   starting with TEMP_PREFIX; else UnsafePathError. `data` must be bytes (ValueError otherwise);
   longer than max_write_bytes -> WriteLimitExceededError(LocalStorageError) before touching disk.
4. Opening the parent: traverse with the adapter's anchored no-follow traversal, then open the parent
   directory itself the same way list_children does (no-follow open, fstat must be S_ISDIR). A parent
   that is a symlink anywhere -> UnsafePathError; missing -> NotFoundError; a file -> NotDirectoryError.
5. write_bytes, in this order, all relative to the parent directory descriptor:
   a. create a temp file `TEMP_PREFIX + <16 random hex>` with
      `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC`, mode 0o644 (umask applies);
   b. write all bytes (loop until done; a short write continues), then `fsync` the file;
   c. publish:
      - overwrite=False: `os.link(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
        follow_symlinks=False)`; EEXIST -> AlreadyExistsError. This is the atomic no-replace step:
        never check-then-rename. If link is not supported by the filesystem (EPERM, ENOTSUP,
        EOPNOTSUPP, EXDEV), fail with UnsupportedPlatformError; do not fall back to a racy method.
      - overwrite=True: the existing target (if any) must be a regular file (fstatat no-follow):
        a directory -> IsDirectoryError, a symlink -> UnsafePathError, special -> UnsupportedEntryError.
        Then `os.rename(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)` (atomic replace).
   d. unlink the temp name (overwrite=False path), then `fsync` the parent directory;
   e. return the new file's FileMetadata (kind "file", size len(data)) built like stat().
   On ANY failure after the temp file exists, unlink the temp file (best effort, never masking the
   primary error) and release every descriptor exactly once. Never leave a temp file behind on an
   error path the code controls. A crash between steps can leave a temp file: document it; cleanup
   of stale temp files is out of scope.
6. mkdir: `os.mkdir(name, 0o755, dir_fd=parent_fd)`; EEXIST -> if exist_ok and the existing entry is a
   real directory (fstatat no-follow) return its metadata, else AlreadyExistsError (a symlink with
   that name is never accepted). fsync the parent directory after creating.
7. ReadOnlyError is for later policy layers; LocalWriter does not raise it. Keep docstrings short.
   Document the threat boundaries: hard links, bind mounts and adversarial root provisioning stay
   operator responsibilities (as in MODULE-003); data is fully buffered (bytes), streaming is later.

## Tests (tests/test_local_write.py; deterministic, temp directories only)

- write then read back via the adapter (bytes identical, metadata right) at root and nested;
  empty file; file exactly max_write_bytes; one byte over -> WriteLimitExceededError and no file and
  no temp left.
- overwrite=False on an existing name -> AlreadyExistsError, original content unchanged.
- overwrite=True replaces content; target is a directory / symlink / FIFO -> the right error,
  nothing changed.
- name validation: every invalid class above -> UnsafePathError, nothing created.
- parent errors: missing, file, symlink parent (final and intermediate) -> right error; a symlink
  parent pointing outside the root never receives a file (check the outside directory stays empty).
- source mismatch precedes everything; closed adapter -> AdapterClosedError; bad data type and bad
  max_write_bytes -> ValueError.
- failure injection through the seams: write fails midway, fsync fails, link fails with EEXIST and
  with EPERM, rename fails -> correct error, no temp file left in the directory, no descriptor leak
  (/proc/self/fd count on Linux; skip with a reason elsewhere).
- race: two threads (in the TEST only) write the same new name with overwrite=False: exactly one
  succeeds, one gets AlreadyExistsError, the file content is exactly one writer's data.
- listing hides temp files: create a file named TEMP_PREFIX + "x" by hand; list_children omits it;
  it still counts toward max_entries.
- mkdir: create, exist_ok on a directory, exist_ok on a file -> AlreadyExistsError, symlink -> error.

Run: `python3 -m unittest discover -s tests -v` from the repository root.

## Delivery

DeepSeek (d026): you cannot run code. Return `FILE: <path>` then a fenced block with the complete
contents for drive_tool/local_write.py, tests/test_local_write.py and drive_tool/storage.py. For
local.py return exactly two `EDIT drive_tool/local.py:` blocks, each with the exact existing lines
and the exact new lines. No ellipses or placeholders. Then one line per test naming the
implementation mistake it would catch. End with STATUS: GENERATED, TESTS_NOT_RUN.

Sarvam (d027): create the branch from main, apply DeepSeek's output exactly, commit, run the full
suite on Python 3.11 and 3.12. Fix only test or code defects the run proves, in separate
"Sarvam fixes: <what>" commits, never weakening or skipping a test. Mutation check in a scratch copy
(never committed): at least (1) publish with rename instead of link when overwrite=False, (2) skip
the temp-file cleanup on error, (3) drop O_NOFOLLOW from the temp-file open, (4) drop the TEMP_PREFIX
filter in list_children, (5) skip the parent-directory fsync; record which tests fail for each. Add
docs/evidence/MODULE-005.md (base, commits, interpreters, command, counts, raw output tail, fixes,
mutation table) and push the branch. Report only pushes you made.

Acceptance (director): Argon's code review (d028), then independent director execution, mutation
check and adversarial probes at the pushed commit. Merge only with Dolesh's approval.
