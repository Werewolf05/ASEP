# ASEP architecture

## Milestone 2.1 scope

The current implementation supports issue intake, repository inspection, isolated patch creation, and evidence-backed verification. It does not implement VGRO decisions, repair retries, persistence across process restarts, or deployment.

## Components

- **API:** Pydantic request/response models and thin FastAPI routes. An `AppServices` container is created at application startup and supplied through request dependencies.
- **Task/evidence store:** Thread-safe in-memory storage keyed by UUID-based task IDs. Tasks, worktree records, patch artifacts, and verification reports cannot be stored under a different task ID.
- **Worktree manager:** Rejects dirty source repositories, records the main repository and base SHA, and creates a detached per-task worktree under a private managed root. It registers worktrees without checkout and materializes Git objects without hooks or filters. Cleanup only removes a registered, clean worktree identified by its stored record; it never uses force removal.
- **Patch engine:** Obtains a structured proposal from the mock provider; validates the entire proposal; uses directory file descriptors and no-follow opens for patch writes; and records the complete Git diff, changed paths, file hashes, and patch hash.
- **Verification engine:** Collects Git integrity and path evidence, then invokes pytest, Ruff, and mypy only through the configured Docker sandbox service. Git metadata commands run on the host; repository test/lint/type-check code does not.
- **Docker sandbox:** Uses a locally built, digest-pinned verification image, network isolation, a read-only container root, a read/write mount of only the task worktree, Fedora SELinux relabeling, non-root UID, resource caps, timeouts, output capture, and forced container cleanup.

## Verification state

Individual mandatory checks report `PASS`, `FAIL`, `ERROR`, or `SKIPPED`. The overall report is:

- `VERIFIED`: every mandatory check ran and passed.
- `FAILED`: an executed mandatory check failed.
- `INCOMPLETE`: mandatory evidence had an execution error or was skipped.
- `BLOCKED`: Docker or the required local verification image was unavailable.

No skipped or inconclusive check can make a task verified.

## API task lifecycle

`CREATED -> WORKTREE_READY -> PATCH_GENERATED -> PATCH_APPLIED -> VERIFYING -> VERIFIED|FAILED|INCOMPLETE|BLOCKED`

Patch proposal errors produce `REJECTED` or `FAILED`, never a successful task status. Responses omit absolute repository/worktree paths. Task evidence includes the worktree base SHA and repository name without exposing the host directory.

## Known limitations

- Task and evidence storage is in memory and is lost on process restart.
- The mock provider emits deterministic demonstration proposals; it is not repository-aware code synthesis.
- Verification results depend on the exact locally built image tag. ASEP will not pull or substitute an image when it is missing.
- Docker daemon access remains a host trust boundary. The platform controls container restrictions but cannot contain a compromised Docker daemon or host kernel.
- No patch commits, merges, push, automatic deployment, VGRO decisions, or repair retries are implemented.
