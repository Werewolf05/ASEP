# Development roadmap

## Milestone 1: foundation and issue-to-plan workflow

Completed in this milestone:

- FastAPI application bootstrapping
- Health and issue endpoints
- Repository path validation against configured roots
- Local repository inspection
- Deterministic mock planning flow
- Basic tests, docs, and initial environment configuration

## Milestone 2: verification-gated workflow

Planned work includes:

- Explicit verification evidence models
- Test and lint execution wrappers
- Security scanning hooks
- Structured decision states for ACCEPT, RETRY, REPAIR, REJECT, and HUMAN_REVIEW

## Milestone 3: optimization and auditing

Planned work includes:

- Benchmarking support for original vs. modified code
- Evidence recording and audit trails
- Policy-based gating for optimization and release decisions

## Milestone 4: persistence and product maturity

Planned work includes:

- SQLite persistence for issues, patches, and verification runs
- PostgreSQL migration path
- Release-candidate review flow and human approval gating
