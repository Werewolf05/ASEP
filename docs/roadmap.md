# ASEP roadmap

## Milestone 1: foundation

- issue intake and Pydantic validation
- repository inspection with configured path restrictions
- deterministic mock planning

## Milestone 2.1: security corrections and verification foundation

Implemented, then independently validated before being considered complete:

- isolated worktrees with dirty-repository refusal and safe cleanup
- proposal-wide path/size validation and safe, atomic patch writes
- complete patch/evidence for created, modified, deleted, and renamed files
- task-scoped in-memory evidence storage
- Docker-only repository test, lint, and type-check execution
- explicit verification statuses and actual Git-state checks

## Milestone 2 readiness gate

Do not begin VGRO until:

- `pytest -q`, `ruff check .`, and `mypy app` pass
- the verification Docker image builds successfully
- the actual Docker integration tests pass (not only mocked Docker tests)
- task evidence and worktree integrity tests pass

## Milestone 3: VGRO

- deterministic decision engine with ACCEPT, RETRY, REPAIR, OPTIMIZE, REJECT, and HUMAN_REVIEW
- bounded repair attempts and mandatory-check policy
- audit trail and experiments driven by structured evidence

## Later milestones

- database-backed durable evidence
- benchmarks and optimization evidence
- release-candidate review and explicit human approval
