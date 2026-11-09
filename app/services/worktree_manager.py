from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

from app.models.task import WorktreeRecord


class WorktreeManager:
    """Manages isolated Git worktrees for engineering tasks."""

    def __init__(self, worktree_root: str | None = None) -> None:
        base = Path(worktree_root) if worktree_root else Path("/tmp/asep-worktrees")
        self.worktree_root = base.expanduser().resolve(strict=False)
        self.worktree_root.mkdir(parents=True, exist_ok=True)

    def create_worktree(self, repo_path: str, task_id: str | None = None) -> WorktreeRecord:
        original_repo = Path(repo_path).expanduser().resolve(strict=False)
        if not original_repo.exists():
            raise FileNotFoundError(f"Repository path '{original_repo}' does not exist.")

        git_check = subprocess.run(
            ["git", "-C", str(original_repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if git_check.returncode != 0 or git_check.stdout.strip() != "true":
            raise ValueError(f"'{original_repo}' is not a valid Git repository.")

        dirty_status = subprocess.run(
            ["git", "-C", str(original_repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if dirty_status.returncode != 0:
            raise ValueError(f"Unable to inspect repository state for '{original_repo}'.")
        dirty = bool(dirty_status.stdout.strip())

        base_commit = subprocess.run(
            ["git", "-C", str(original_repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if base_commit.returncode != 0:
            raise ValueError(f"Repository '{original_repo}' has no commits and cannot create a worktree.")

        resolved_task_id = task_id or uuid.uuid4().hex
        target_dir = self.worktree_root / resolved_task_id
        if target_dir.exists():
            raise ValueError(f"Worktree path '{target_dir}' already exists for task '{resolved_task_id}'.")

        result = subprocess.run(
            ["git", "-C", str(original_repo), "worktree", "add", "--detach", str(target_dir), "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise ValueError(
                f"Failed to create worktree for '{original_repo}': {result.stderr.strip() or result.stdout.strip()}"
            )

        return WorktreeRecord(
            task_id=resolved_task_id,
            original_repository_path=str(original_repo),
            worktree_path=str(target_dir),
            base_commit_sha=base_commit.stdout.strip(),
            dirty_repository=dirty,
        )

    def cleanup_worktree(self, task_id: str, original_repo: str | None = None) -> bool:
        worktree_dir = self.worktree_root / task_id
        if not worktree_dir.exists():
            return False

        repo_path = original_repo or self._discover_original_repo(task_id)
        if repo_path:
            subprocess.run(
                ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_dir)],
                capture_output=True,
                text=True,
                check=False,
            )

        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)
        return True

    def _discover_original_repo(self, task_id: str) -> str | None:
        worktree_dir = self.worktree_root / task_id
        if not worktree_dir.exists():
            return None

        list_output = subprocess.run(
            ["git", "-C", str(worktree_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if list_output.returncode == 0:
            return list_output.stdout.strip()
        return None
