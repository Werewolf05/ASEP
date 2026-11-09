# ASEP architecture

## Goal

ASEP is a foundation for an autonomous engineering platform that safely transforms software issues into reviewable implementation plans without mutating the original repository.

## Layer responsibilities

### API layer

The API layer accepts issue submissions and repository inspection requests, validates them with Pydantic models, and keeps route logic thin. Hard business logic is delegated to services and domain-specific components.

### Model layer

Typed models define issue requests, repository summaries, and programmatic planning outputs. The core design makes validation explicit and keeps responses deterministic.

### Repository layer

Repository inspection is intentionally static and bounded. It uses Git commands to detect repository status and file metadata while rejecting paths outside configured directories.

### Agent layer

The agent layer exposes a provider abstraction so future LLM integrations can replace the mock implementation without changing the rest of the application. The initial mock provider returns deterministic, test-friendly plan data.

### Service layer

The issue service stores incoming issue records in memory for the initial milestone. This is intentionally simple and easy to replace with SQLite-backed persistence in a later phase.

## Security and safety constraints

- The application does not execute repository code on the host.
- Repository analysis is limited to static file discovery and Git metadata.
- Repository paths are validated against configured allowed roots.
- The API intentionally avoids returning raw host paths beyond minimal display information.

## Next milestones

- Add explicit verification evidence models
- Introduce a staged repair loop with bounded retries
- Add a database-backed persistence layer
- Add future AI provider adapters and environment-based configuration
