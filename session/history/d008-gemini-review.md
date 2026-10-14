Verdict: CHANGES REQUIRED

The removal of O_DIRECTORY from _dir_open_flags() paired with immediate fstat verification (S_ISDIR) and O_NOFOLLOW | O_NONBLOCK resolves the gVisor O_DIRECTORY | O_NOFOLLOW symlink-traversal bypass. However, two blocking issues remain:

1. Concrete descriptor leak (scan_fd / file_fd) when closing parent_fd fails in list_children() and read_bytes() (drive_tool/local.py). The correction fixed child-descriptor cleanup on parent-close failure inside _open_anchored_root() and _traverse_parent_dir(), and tested only the first close of a 3-component path (p/a/b/file.txt), missing the final handoff from parent_fd to scan_fd in list_children() and from parent_fd to file_fd in read_bytes().

2. Self-fulfilling test skip gate in _is_supported_posix() and uncoupled probe flags in _no_follow_semantics_available() (tests/test_local.py, drive_tool/local.py). Because _is_supported_posix() calls _no_follow_semantics_available() inside _PosixTempRootTestCase.setUp(), any POSIX kernel where the runtime probe returns False silently skips the entire TestPlatformNoFollowSemantics suite (making test_runtime_probe_reports_nofollow_enforced impossible to fail), and _no_follow_semantics_available() reconstructs its own flag bitmask instead of calling _dir_open_flags().

## Files Read & Execution Status

- Files Read (authoritative inline copies from commit c2b324dab4cf720178a36a58b5f1cf1339730151):

- drive_tool/storage.py (base contract, MODULE-001)

- drive_tool/local.py (MODULE-003 correction)

- tests/test_local.py (MODULE-003 test suite)

- Not Included Inline: docs/evidence/MODULE-003-GEMINI.md (required by the MODULE-003 contract; must be verified present on branch feature/module-003-review-fixes).

- Test Execution Status: TESTS_NOT_RUN. All test pass counts (82 tests on Python 3.11/3.12) and race/mutation results are worker-reported only and have not been independently executed in this review environment.

## Priority-by-Priority Audit
### 1. Atomic Containment, Root Anchoring, and Platform Gate

- Root establishment (_open_anchored_root) and intermediate traversal (_traverse_parent_dir):

- Opens / and each single path component step-by-step via os.open(part, _dir_open_flags(), dir_fd=current_fd) using O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC (without O_DIRECTORY), followed immediately by os.fstat(next_fd) and stat.S_ISDIR(st.st_mode).

- Because O_NOFOLLOW is enforced atomically at the kernel openat boundary when O_DIRECTORY is omitted, a symlink at any ancestor, root, or intermediate component fails at openat with ELOOP (or EMLINK), mapped to UnsafePathError. There is no separate lstat-then-open window.

- stat() containment:

- Traverses parts[:-1] via _traverse_parent_dir to obtain an anchored parent_fd, then calls os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False) (fstatat with AT_SYMLINK_NOFOLLOW). Because target_name is validated as a single slash-free component, fstatat inspects the directory entry itself without following symlinks and raises UnsafePathError if stat.S_ISLNK(st.st_mode) is true.

- list_children() containment:

- Opens the target directory via anchored no-follow openat ("." from root_fd for "root", or target_name from parent_fd), verifies S_ISDIR via fstat(scan_fd), and iterates os.scandir(scan_fd).

- Calls entry.stat(follow_symlinks=False) on each entry (which in CPython issues fstatat with AT_SYMLINK_NOFOLLOW against the scandir descriptor rather than trusting cached d_type), skipping symlinks (S_ISLNK), disappearing entries (ENOENT), and special files (FIFOs, sockets, devices).

- read_bytes() containment:

- Opens target_name from parent_fd with _file_open_flags() (O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC), then verifies S_ISREG via fstat(file_fd) (raising IsDirectoryError on directories, UnsafePathError on symlinks, and UnsupportedEntryError on special entries).

- Root anchoring after rename:

- Retained self._root_fd keeps operations anchored to the original directory inode even if the root path on disk is renamed and replaced by a symlink to an outside directory.

- Remaining routes under documented threat model:

- Within the single-threaded lifecycle and excluding out-of-scope threats (hard links, bind mounts/submounts, procfs/sysfs, and adversarial root provisioning), there are no remaining symlink-swap routes to outside bytes, metadata, or directory listings once O_NOFOLLOW without O_DIRECTORY is enforced by the kernel.

- See Blocking Finding 2 below regarding _no_follow_semantics_available() not calling _dir_open_flags().

### 2. Descriptor Ownership and Cleanup on Exception Paths

- _open_anchored_root() and _traverse_parent_dir():

- Both methods transfer ownership (prev_fd = current_fd; current_fd = next_fd) before calling _close_owned_fd(prev_fd). If closing prev_fd raises OSError, the outer except BaseException: closes the newly opened current_fd (next_fd) via _close_quietly and does not close prev_fd a second time.

- close() idempotency:

- Sets self._closed = True and self._root_fd = None before calling self._os_close(fd), preventing double-close if os.close raises OSError and __exit__ or __del__ runs later.

- os.scandir(scan_fd) duplicate vs scan_fd:

- CPython's os.scandir(fd) duplicates fd via _Py_dup before calling fdopendir. The with scandir_it as entries: block closes the duplicated descriptor on exit, while finally: self._close_traversal_fd(scan_fd, ...) closes the caller-owned scan_fd.

- Concrete leak found (list_children and read_bytes):

- In both list_children() (lines 643–661) and read_bytes() (lines 749–767), scan_fd / file_fd is opened inside the try block that owns parent_fd, while the try ... finally block that owns scan_fd / file_fd starts after the parent_fd finally block completes. If _close_traversal_fd(parent_fd) fails with OSError, it raises LocalStorageError before the second try ... finally is entered, leaking scan_fd or file_fd. See Blocking Finding 1 for the exact execution trace and fix.

### 3. Bounded Reads, FIFO Nonblocking Behavior, Precedence, Errors, and MODULE-001 Compatibility

- Bounded allocation and read enforcement:

- _read_open_file_bounded() checks st.st_size against self._max_read_bytes before reading and reads in chunks of at most _READ_CHUNK_SIZE (65,536 bytes) up to max_read_bytes + 1 total bytes. It never allocates buffers proportional to untrusted st.st_size, retries on EINTR, and raises ReadLimitExceededError if a concurrently growing file exceeds max_read_bytes.

- FIFO and special-entry nonblocking behavior:

- Opening a FIFO with O_RDONLY | O_NONBLOCK returns immediately on POSIX without waiting for a writer; fstat then detects S_ISFIFO (not S_ISDIR and not S_ISREG), closes the descriptor, and raises UnsupportedEntryError.

- Opening a Unix domain socket (S_IFSOCK) fails in os.open with ENXIO/EOPNOTSUPP; _classify_component_open_error falls back to os.stat(..., dir_fd=parent_fd, follow_symlinks=False), sees a non-regular/non-directory mode, and raises UnsupportedEntryError.

- Source-mismatch precedence:

- _check_source_and_state() runs first in stat(), list_children(), and read_bytes(). It validates isinstance(ref, FileRef) (TypeError), then checks ref.source_id != self._source_id (SourceMismatchError) before checking self._closed (AdapterClosedError) or parsing ref.file_id (UnsafePathError).

- Error hierarchy and preserved causes:

- LocalStorageError subclasses StorageError; all five specific local errors subclass LocalStorageError (UnsafePathError also subclasses ValueError, satisfying constructor ValueError tests while preserving UnsafePathError specificity). All OS error translations use raise ... from exc.

- MODULE-001 compatibility:

- drive_tool/storage.py is unmodified, and LocalAdapter conforms to StorageAdapter.

### 4. Regression Test Quality vs. Self-Fulfilling Assumptions

- Real-kernel vs. mocked mechanism tests:

- TestPlatformNoFollowSemantics.test_real_kernel_rejects_symlink_with_component_flags exercises the actual kernel's os.open with _dir_open_flags() on a real symlink.

- The symlink-swap tests (test_dir_swap_before_component_open_* and test_adversarial_symlink_swap_between_stat_and_read_and_mid_traversal) are deterministic mechanism tests that hook _os_open to perform an atomic filesystem rename/symlink replacement immediately before the real kernel os.open call, verifying that the kernel rejects the swapped symlink with ELOOP -> UnsafePathError and never returns SENTINEL_BYTES.

- Defects in test assumptions:

- TestCloseFailureOwnership.test_traversal_close_failure_cleans_new_fd_without_double_close only injects EIO on len(close_calls) == 1 during a 3-component read (p/a/b/file.txt), which tests _close_owned_fd inside _traverse_parent_dir and misses the parent_fd close in read_bytes (len(close_calls) == 2) and list_children.

- _is_supported_posix() includes and _no_follow_semantics_available(), which causes _PosixTempRootTestCase.setUp() to skip TestPlatformNoFollowSemantics whenever _no_follow_semantics_available() returns False. See Blocking Finding 2.

### 5. Simplicity, Resource Overhead, and Threat Boundaries

- Threat boundaries:

- The module docstring accurately documents that concurrent external writers, concurrent calls racing with close(), hard links, bind mounts/submounts, procfs/sysfs, and adversarial root provisioning are outside the adapter's guarantees.

- Non-blocking resource overhead nit (_no_follow_semantics_available):

- _check_platform_capabilities() executes tempfile.mkdtemp() + os.mkdir() + os.symlink() + os.open() + shutil.rmtree() on every LocalAdapter() construction. While acceptable for long-lived adapter instances, this performs temporary-directory writes on every constructor call and will fail closed with UnsupportedPlatformError if /tmp is read-only even when root is valid. Caching the probe result (or probing once per process while keeping the helper mockable in tests) is an optional cleanup.

## Blocking Findings (CHANGES REQUIRED)
### Blocking Finding 1: Descriptor Leak of scan_fd / file_fd When Closing parent_fd Fails

- File / Functions:

- drive_tool/local.py — LocalAdapter.list_children (lines 643–661)

- drive_tool/local.py — LocalAdapter.read_bytes (lines 749–767)

- Concrete Execution Path / Reproducer Outline:

1. Call adapter.read_bytes(FileRef("src", "p/a/file.txt")) (2-component path, so parts[:-1] == ("a",) and parent_fd != root_fd) or adapter.list_children(FileRef("src", "p/a/b")).

2. _traverse_parent_dir opens "a" and returns parent_fd.

3. Inside the first try block, file_fd = self._os_open("file.txt", _file_open_flags(), dir_fd=parent_fd) (or scan_fd = self._os_open("b", dir_flags, dir_fd=parent_fd) in list_children) succeeds and allocates a new descriptor.

4. The first try block exits normally with sys.exc_info()[0] is None.

5. Its finally block calls self._close_traversal_fd(parent_fd, ...).

6. self._os_close(parent_fd) raises OSError(errno.EIO, "injected close failure"). Because primary = sys.exc_info()[0] is None, _close_traversal_fd raises LocalStorageError.

7. Execution unwinds out of read_bytes() / list_children() before entering the subsequent try ... finally block (line 663 in list_children, line 769 in read_bytes) that closes scan_fd / file_fd.

8. Result: scan_fd / file_fd is leaked open in the process file descriptor table. (For a 3-component path p/a/b/file.txt, this occurs when the 2nd close call fails.)

- Minimal Required Fix:
Initialize scan_fd: int | None = None (in list_children) and file_fd: int | None = None (in read_bytes) before the parent_fd try block, and guard cleanup so that if closing parent_fd raises after scan_fd / file_fd was opened, the child descriptor is closed via self._close_quietly(...) before propagating the LocalStorageError:

```python
        # In list_children (lines 643-662):
        else:
            parent_fd = self._traverse_parent_dir(
                root_fd, parts[:-1], parent.file_id
            )
            scan_fd_ candidate: int | None = None
            try:
                try:
                    target_name = parts[-1]
                    try:
                        scan_fd_candidate = self._os_open(
                            target_name, dir_flags, dir_fd=parent_fd
                        )
                    except OSError as exc:
                        raise self._classify_component_open_error(
                            target_name,
                            parent_fd,
                            exc,
                            parent.file_id,
                            is_intermediate=False,
                            for_directory_op=True,
                        ) from exc
                finally:
                    if parent_fd != root_fd:
                        self._close_traversal_fd(
                            parent_fd,
                            context=f"parent directory of {parent.file_id!r}",
                        )
            except BaseException:
                if scan_fd_candidate is not None:
                    self._close_quietly(scan_fd_candidate)
                raise
            scan_fd = scan_fd_candidate
```

Apply the identical pattern to file_fd in read_bytes() (lines 749–767).

- Required Acceptance Test (tests/test_local.py):
In TestCloseFailureOwnership, parameterize test_traversal_close_failure_cleans_new_fd_without_double_close across every close index (fail_on_close_index in 1, 2) for both adapter.read_bytes(FileRef("src", "p/a/b/file.txt")) and adapter.list_children(FileRef("src", "p/a/b")) (as well as 2-component paths p/a/file.txt and p/a/b), asserting set(opened) == set(close_calls) and len(close_calls) == len(set(close_calls)) for each index.

### Blocking Finding 2: Self-Fulfilling Skip Condition in _is_supported_posix() and Uncoupled Flags in _no_follow_semantics_available()

- File / Functions:

- drive_tool/local.py — _no_follow_semantics_available() (lines 147–152)

- tests/test_local.py — _is_supported_posix() (lines 42–57) and TestPlatformNoFollowSemantics (lines 125–164)

- Concrete Execution Path / Reproducer Outline:

1. In drive_tool/local.py, _no_follow_semantics_available() manually constructs flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) instead of calling _dir_open_flags(). If _dir_open_flags() is ever altered (e.g., reintroducing O_DIRECTORY), _no_follow_semantics_available() still tests without O_DIRECTORY and returns True on gVisor.

2. In tests/test_local.py, _is_supported_posix() returns ... and _no_follow_semantics_available(), and _PosixTempRootTestCase.setUp() calls if not _is_supported_posix(): self.skipTest(...).

3. If the test suite runs on a POSIX environment where _no_follow_semantics_available() returns False, _PosixTempRootTestCase.setUp() skips all tests in TestPlatformNoFollowSemantics — including test_runtime_probe_reports_nofollow_enforced (self.assertTrue(_no_follow_semantics_available())) and test_real_kernel_rejects_symlink_with_component_flags. Thus, test_runtime_probe_reports_nofollow_enforced can never fail on any machine.

- Minimal Required Fix:

1. In drive_tool/local.py, replace the inline flags construction in _no_follow_semantics_available() with flags = _dir_open_flags() (moving _dir_open_flags() above _no_follow_semantics_available()).

2. In tests/test_local.py:

- Split static POSIX capability checks (_has_static_posix_capabilities()) from _no_follow_semantics_available().

- Gate TestPlatformNoFollowSemantics on _has_static_posix_capabilities() (not _no_follow_semantics_available()), so that on any POSIX target supporting dir_fd and O_NOFOLLOW constants, test_real_kernel_rejects_symlink_with_component_flags and test_runtime_probe_reports_nofollow_enforced execute against the kernel and fail loudly (rather than silently skipping) if O_NOFOLLOW is not enforced.

- Required Acceptance Test (tests/test_local.py):

- Verify that when _no_follow_semantics_available is patched to False, TestPlatformNoFollowSemantics.test_unsupported_platform_when_runtime_probe_fails still executes (is not skipped by setUp) and asserts that LocalAdapter("src", ...) raises UnsupportedPlatformError.

- Verify that _no_follow_semantics_available() calls _dir_open_flags() (e.g., if _dir_open_flags is patched in a unit test to omit O_NOFOLLOW, _no_follow_semantics_available() returns False).

## Residual Uncertainty & Cloud-Box Acceptance Gates

- Residual Uncertainty:

- Because this review is static (TESTS_NOT_RUN), the behavior of os.open with _dir_open_flags() and os.scandir(scan_fd) on the actual target gVisor kernel has not been independently observed in this session.

- docs/evidence/MODULE-003-GEMINI.md was not included in the inline review payload and must be checked in the repository tree.

- Cloud-Box / Director Acceptance Gates Before Merge:

1. Apply the two fixes above and run python3 -m unittest -v tests/test_local.py and existing MODULE-001 tests on both the target gVisor sandbox and a standard Linux kernel (Python 3.11/3.12), confirming zero skipped tests on both environments.

2. Confirm TestPlatformNoFollowSemantics runs unskipped and passes on the gVisor box.

3. Confirm docs/evidence/MODULE-003-GEMINI.md and docs/evidence/MODULE-003.md are present and drive_tool/storage.py has zero diff against base 194d4ad57de6dd895c6eaaf7faf4d08583eab02b.
