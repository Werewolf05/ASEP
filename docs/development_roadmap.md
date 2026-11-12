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

## Milestone 3.0: deterministic VGRO decision engine

- independent decision engine over structured task-scoped evidence
- hard security/integrity rejection and fail-closed missing evidence
- bounded retry/repair recommendations with in-memory attempt tracking
- policy-versioned reasons and evidence references
- API evaluation and latest-decision retrieval

Use [roadmap.md](./roadmap.md) for implemented scope and readiness details.

## Future work

- recovery execution behind an additional safety gate
- durable SQLAlchemy persistence for tasks and evidence
- benchmark-based optimization acceptance
- human-gated release candidates
