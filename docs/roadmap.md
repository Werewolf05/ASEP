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

VGRO development was gated on:

- `pytest -q`, `ruff check .`, and `mypy app` pass
- the verification Docker image builds successfully
- the actual Docker integration tests pass (not only mocked Docker tests)
- task evidence and worktree integrity tests pass

## Milestone 3.0: deterministic VGRO decision engine

Implemented:

- independent Pydantic VGRO domain models and deterministic decision engine
- task/patch/verification ownership checks and independent patch digest validation
- mandatory-check, security, prohibited-path, and missing-evidence decision rules
- bounded RETRY/REPAIR recommendations and an in-memory attempt tracker
- policy version, structured reason codes, and evidence references in results
- task-scoped evaluation and latest-decision API endpoints

Recovery execution is not implemented. Ordinary failures remain unclassified unless
trusted structured evidence explicitly marks them recoverable or transient. Optimization
eligibility is a decision only; no benchmarks or optimization runs occur. `ACCEPT` does
not imply human approval to merge or deploy.

## Later milestones

- database-backed durable evidence
- autonomous recovery execution behind a separate safety gate
- benchmarks and optimization evidence
- release-candidate review and explicit human approval
