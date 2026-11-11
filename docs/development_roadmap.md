# Development roadmap

## Milestone 1: foundation

- issue intake and Pydantic validation
- repository inspection with configured path restrictions
- deterministic mock planning

## Milestone 2.1: security corrections and verification foundation

- collision-resistant task IDs and per-task Git worktrees
- dirty source repositories refused; worktree cleanup does not force-remove changes
- prevalidated, path-safe patch writes with file and total patch limits
- complete patch diff and hashes for new, modified, deleted, and renamed files
- task-scoped in-memory evidence retrieval
- Docker-only repository test, lint, and type-check execution
- independent Git state and patch hash validation

VGRO is intentionally deferred until actual Docker-backed suite verification and
the security acceptance checks pass. Use [roadmap.md](./roadmap.md) as the current
readiness gate.

## Future work

- deterministic VGRO decisions and bounded repair attempts
- durable SQLAlchemy persistence for tasks and evidence
- benchmark-based optimization acceptance
- human-gated release candidates
