from __future__ import annotations

import subprocess
from pathlib import Path


class GitRepositoryManager:
    """Thin wrapper around local Git operations used in the initial milestone."""

    def __init__(self, allowed_roots: list[str] | None = None) -> None:
        self.allowed_roots = [Path(root).expanduser().resolve(strict=False) for root in (allowed_roots or ["/tmp", "/home"])]

    def resolve_repository_path(self, candidate: str) -> Path:
        requested = Path(candidate).expanduser()
        if not requested.is_absolute():
            raise ValueError("Repository path must be an absolute path.")

        resolved = requested.resolve(strict=False)
        if not any(
            resolved == root or root in resolved.parents
            for root in self.allowed_roots
        ):
            raise ValueError(
                "Repository path is outside the configured allowed directories. "
                "Use a repository path inside one of the configured roots."
            )

        return resolved

    def git(self, repo_path: Path, args: list[str]) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(
                f"Git command failed for '{repo_path}': {' '.join(args)}\n"
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed.stdout.strip()
