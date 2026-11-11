# ASEP security model

## Trust boundaries

- The original repository working tree is never used as a patch destination. Dirty repositories are refused; clean repositories get a detached worktree at the recorded base commit.
- Worktrees are registered with checkout disabled, then populated from Git objects without running repository hooks, smudge/clean filters, or checkout commands. Host Git is invoked with global/system configuration isolated, local filter drivers neutralized, and filesystem-monitor hooks disabled.
- The task worktree is the only writable host path mounted into verification. The host executes Git metadata operations only; pytest, Ruff, and mypy run inside Docker.
- The Docker CLI/daemon and host kernel are trusted infrastructure. Container controls reduce exposure but do not protect against a compromised daemon or kernel.

## Docker-enforced verification

Build the required local image from the project root:

```bash
docker build --file Dockerfile.verification --tag asep-verification:0.1.0 .
```

Verification checks for a responding Docker daemon and the exact local image before running. It never pulls a replacement image or falls back to host execution. A missing daemon/image produces `BLOCKED`; a missing tool in the container is `SKIPPED`; timeouts and execution exceptions are `ERROR`. Every container is named uniquely and removed with `docker rm --force` in a `finally` path.

The container uses a digest-pinned Python base image and pinned Python dependencies, a date-pinned Debian snapshot and Git package, no network, a read-only root filesystem, a size-limited temporary filesystem, and a single read/write task-worktree bind mount. The bind mount uses Docker's private SELinux `:Z` relabel option for Fedora; this changes the isolated worktree's SELinux label, not the original repository. The container runs with the invoking non-root UID/GID, dropped Linux capabilities, `no-new-privileges`, CPU/memory/PID/file-descriptor caps, and a 120-second per-command timeout. No host home directory, Docker socket, host environment, or credentials are mounted or passed.

## Patch path controls

- Proposals are fully validated before any write.
- Absolute paths, drive paths, backslashes, traversal, sensitive/case-variant names, binary content, and unsupported extensions are rejected.
- Existing symlinks and symlinked parent components are rejected.
- Repository symlinks that escape the worktree and hard-linked patch targets are rejected.
- Writes use directory file descriptors, `O_NOFOLLOW`, exclusive temporary files, and atomic replacement.
- File and complete patch size caps are enforced; failed applications restore captured file contents.
- New, modified, deleted, and renamed files are represented in the patch evidence.

## Evidence and status semantics

Patch SHA-256 is checked against the actual worktree diff. Changed paths are independently read from Git status and checked for path escape/symlink conditions; existing changed files are hashed. Mandatory checks have `PASS`, `FAIL`, `ERROR`, or `SKIPPED` outcomes. Overall `VERIFIED` is possible only when every mandatory check actually executed and passed. Missing prerequisites produce `BLOCKED`; missing/inconclusive evidence produces `INCOMPLETE`; an executed failed mandatory check produces `FAILED`.

Task, worktree, patch, and verification evidence are retained in a task-ID-scoped in-memory store. API responses omit absolute host paths. This is not durable storage: restarting the service loses the in-memory evidence.

## Running tests

Unit/security tests (including mocked Docker behavior):

```bash
pytest -q
```

Actual Docker integration tests (not mocked; require the Docker daemon and image):

```bash
docker build --file Dockerfile.verification --tag asep-verification:0.1.0 .
pytest -q tests/integration/test_docker_sandbox.py
```

The integration test skips if Docker or the image is unavailable. A skipped integration test is not evidence that Docker verification passed.
