from __future__ import annotations

from pathlib import Path

from app.config import normalize_repo_roots
from app.models.issue import RepositoryInspectionResult
from app.repository.git_manager import GitRepositoryManager


class RepositoryInspector:
    """Static repository inspection for issue-aware planning."""

    def __init__(self, git_manager: GitRepositoryManager | None = None, allowed_roots: list[str] | None = None) -> None:
        self.git_manager = git_manager or GitRepositoryManager(allowed_roots or [str(path) for path in normalize_repo_roots()])

    def inspect(self, repository_path: str) -> RepositoryInspectionResult:
        resolved_path = self.git_manager.resolve_repository_path(repository_path)
        if not resolved_path.exists():
            raise FileNotFoundError(f"Repository path '{resolved_path}' does not exist.")

        try:
            git_root = self.git_manager.git(resolved_path, ["rev-parse", "--show-toplevel"])
        except ValueError as exc:
            raise ValueError(f"'{resolved_path}' is not a valid Git repository.") from exc

        branch_name = self.git_manager.git(resolved_path, ["branch", "--show-current"])
        status_summary = self.git_manager.git(resolved_path, ["status", "--short"])

        repo_root = Path(git_root)
        tracked_files = [
            str(path.relative_to(repo_root)).replace("\\", "/")
            for path in sorted(repo_root.rglob("*"))
            if path.is_file()
        ]

        language_summary: dict[str, int] = {}
        for file_path in tracked_files:
            suffix = Path(file_path).suffix.lower()
            if not suffix:
                continue
            language = {
                ".py": "python",
                ".js": "javascript",
                ".ts": "typescript",
                ".tsx": "typescript",
                ".go": "golang",
                ".rs": "rust",
                ".java": "java",
                ".cs": "csharp",
                ".md": "markdown",
            }.get(suffix, suffix.lstrip("."))
            language_summary[language] = language_summary.get(language, 0) + 1

        test_files = [
            file_path
            for file_path in tracked_files
            if "test" in Path(file_path).name.lower() or "/test" in file_path.lower()
        ]

        display_path = str(resolved_path.relative_to(self.git_manager.allowed_roots[0])) if resolved_path.is_relative_to(self.git_manager.allowed_roots[0]) else resolved_path.name

        return RepositoryInspectionResult(
            repository_name=repo_root.name,
            display_path=display_path,
            git_initialized=True,
            branch_name=branch_name or None,
            status_summary=status_summary,
            tracked_files=tracked_files[:200],
            language_summary=language_summary,
            test_files=test_files[:50],
            summary=(
                f"Repository '{repo_root.name}' has {len(tracked_files)} tracked files and "
                f"{len(test_files)} likely test files."
            ),
            metadata={
                "repo_root": repo_root.name,
                "path_depth": len(repo_root.parts),
                "is_clean": not bool(status_summary),
            },
        )
