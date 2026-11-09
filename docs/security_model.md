# ASEP security model

## Core principles

- The host never executes untrusted repository code.
- Repository inspection is limited to Git metadata and file discovery.
- Tasks run in isolated Git worktrees rather than in the original repository.
- Patch application is validated before writing to disk.
- Verification runs independently and records actual command results.

## Path safety

Patch targets must be relative paths, must not contain `..`, must stay within the worktree root, and must not traverse symlinks. Sensitive file names and directories such as `.git`, `.env`, or SSH keys are rejected before writing.

## Verification safety

The verification engine records command output, exit codes, status, and duration. Missing tools are reported as `SKIPPED` instead of being treated as success. Docker execution is preferred for sandboxed validation and is required when available. If Docker is unavailable, the system reports that verification could not be completed without falling back to host execution.

## Human-gated release

This milestone intentionally does not deploy or merge changes automatically. The generated patch and evidence remain reviewable artifacts for later human approval.
