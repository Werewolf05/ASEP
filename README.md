# ASEP

Autonomous Software Engineer Platform (ASEP) is a Python-based engineering platform that accepts software issues, inspects repositories, proposes implementation plans, and records verification evidence without running untrusted repository code on the host.

## Milestones

The project is currently in Milestone 2: isolated patch generation and independent verification.

### Milestone 1 complete

- FastAPI application startup and health checks
- Issue intake validation with Pydantic
- Local Git repository inspection with path validation
- Deterministic mock AI planning for repository-specific implementation work
- API-oriented foundation for future patch generation and verification

### Milestone 2 includes

- Isolated Git worktrees for engineering tasks
- Deterministic mock patch generation and validation
- Patch artifact hashing and diff capture
- Verification evidence collection with Docker-aware execution
- Evidence retrieval and task lifecycle tracking

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

## API endpoints

- `GET /health`
- `POST /api/v1/issues`
- `POST /api/v1/repositories/inspect`
- `POST /api/v1/agent/plan`

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

- No arbitrary repository code is executed by the host environment.
- Repository discovery is restricted to configured allowed directories.
- The mock provider is intentionally deterministic for development and testing.
