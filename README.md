# ASEP

Autonomous Software Engineer Platform (ASEP) is a Python-based engineering platform that accepts software issues, inspects repositories, proposes implementation plans, and records verification evidence without running untrusted repository code on the host.

## Milestones

The project is currently in the Milestone 2.1 security correction phase. VGRO is not implemented.

### Milestone 1 complete

- FastAPI application startup and health checks
- Issue intake validation with Pydantic
- Local Git repository inspection with path validation
- Deterministic mock AI planning for repository-specific implementation work
- API-oriented foundation for future patch generation and verification

### Milestone 2 foundation

- Isolated Git worktrees for engineering tasks
- Deterministic mock patch proposals
- Task-scoped in-memory evidence storage
- Docker-only repository verification (no host fallback)
- Structured check/report statuses and evidence retrieval

## Architecture overview

The project is organized into five conceptual layers:

- API layer: request validation, serialization, and routing
- Service layer: issue management and orchestration logic
- Repository layer: Git repository discovery and static analysis
- Agent layer: provider abstraction and implementation planning
- Model layer: typed request/response contracts

## Local setup

1. Create a virtual environment.
2. Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

3. Copy the example environment file and adjust paths if needed:

```bash
cp .env.example .env
```

## Running the service

```bash
uvicorn app.main:app --reload
```

The service uses the deterministic mock provider and stores tasks/evidence in memory.
Set `ASEP_ALLOWED_REPO_ROOTS` to comma-separated repository roots before startup if
the default roots do not include your repositories.

## Docker verification image

Verification will not run pytest, Ruff, or mypy on the host. Build the required image
before requesting verification:

```bash
docker build --file Dockerfile.verification --tag asep-verification:0.1.0 .
```

The image uses a digest-pinned Python release, pinned Python requirements, and a
pinned Debian package snapshot for Git. The task-worktree bind mount is privately
SELinux-relabelled (`:Z`) for Fedora. If Docker or this exact image is unavailable, the
report is `BLOCKED`; ASEP does not pull another image or fall back to host execution.

## Validation

Unit tests (Docker interactions are mocked except for explicit integration tests):

```bash
pytest -q
ruff check .
mypy app
```

Actual Docker integration tests require the built image and a working daemon:

```bash
pytest -q tests/integration/test_docker_sandbox.py
```

These are real Docker calls, not mocks. They skip if the daemon or image is missing.

## API endpoints

- `GET /health`
- `POST /api/v1/issues`
- `POST /api/v1/repositories/inspect`
- `POST /api/v1/agent/plan`
- `POST /api/v1/agent/patch`
- `POST /api/v1/verification/run`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/{task_id}/evidence`

## Example issue request

```json
{
  "title": "Fix login validation",
  "description": "The login form accepts empty usernames. Add a guard for blank input and a regression test.",
  "tags": ["bug", "auth"]
}
```

## Example repository inspection request

```json
{
  "repository_path": "/tmp/example-project"
}
```

## Example planning request

```json
{
  "title": "Fix login validation",
  "description": "The login form accepts empty usernames. Add a guard for blank input and a regression test.",
  "repository_path": "/tmp/example-project"
}
```

## Notes

- Repository test/lint/type-check code runs only inside the restricted Docker sandbox.
- Git metadata commands run on the host; arbitrary repository code does not.
- Worktree creation rejects dirty repositories. The original working tree is not patched.
- Worktree population avoids repository checkout hooks and Git filters on the host.
- Verification results use `PASS`, `FAIL`, `ERROR`, and `SKIPPED` check statuses, with
  `VERIFIED`, `FAILED`, `INCOMPLETE`, or `BLOCKED` report statuses. Skipped checks
  cannot produce `VERIFIED`.
- Task and evidence storage is in-memory and is lost when the service restarts.
- The mock provider is intentionally deterministic for development and testing.
- No commit, merge, push, deployment, VGRO decision, or autonomous repair is performed.
