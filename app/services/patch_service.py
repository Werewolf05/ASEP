from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import ClassVar

from app.agent.base import AIProvider
from app.models.issue import IssueCreate
from app.models.task import EngineeringTask, PatchArtifact
from app.services.worktree_manager import WorktreeManager


class PatchGenerationService:
    """Validates provider proposals and applies them exclusively in the task worktree."""

    forbidden_basenames: ClassVar[set[str]] = {".git", ".env", "id_rsa", "id_dsa", "authorized_keys", "secrets.yml"}
    allowed_suffixes: ClassVar[set[str]] = {
        ".py",
        ".txt",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
    }
    max_file_size_bytes: ClassVar[int] = 256 * 1024
    max_patch_size_bytes: ClassVar[int] = 1 * 1024 * 1024

    def __init__(self, provider: AIProvider | None = None, worktree_manager: WorktreeManager | None = None) -> None:
        self.provider = provider
        self.worktree_manager = worktree_manager or WorktreeManager()

    def generate_patch(self, issue: IssueCreate, task: EngineeringTask) -> PatchArtifact:
        if self.provider is None:
            raise ValueError("Patch generation requires an AI provider implementation.")
        if task.worktree_path is None:
            raise ValueError(f"Task '{task.task_id}' does not have a valid worktree.")

        proposal = self.provider.generate_patch_proposal(issue, task)
        if proposal.get("status") == "rejected":
            raise ValueError(f"Patch proposal rejected: {proposal.get('reason', 'Unknown reason')}")

        result = proposal.get("changes")
        if not isinstance(result, list) or not result:
            raise ValueError("Patch proposal does not contain any valid file changes.")

        worktree_root = Path(task.worktree_path).resolve(strict=True)
        patch_size = 0
        changed_files: list[str] = []
        file_hashes: dict[str, str] = {}

        for item in result:
            if not isinstance(item, dict):
                raise TypeError("Each change entry must be a dictionary.")
            file_path = str(item.get("path", "")).strip()
            if not file_path:
                raise ValueError("Patch proposal includes an empty change path.")
            target = self._validate_target_path(file_path, worktree_root)
            action = str(item.get("action", "modify")).lower()
            content = item.get("content")
            if action not in {"create", "modify", "replace"}:
                raise ValueError(f"Unsupported patch action '{action}' for '{file_path}'.")
            if not isinstance(content, str):
                raise TypeError(f"Patch content for '{file_path}' must be a string.")
            if len(content.encode("utf-8")) > self.max_file_size_bytes:
                raise ValueError(f"Patch content for '{file_path}' exceeds the configurable file-size limit.")

            destination = worktree_root / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            changed_files.append(target)
            file_hashes[target] = hashlib.sha256(destination.read_bytes()).hexdigest()
            patch_size += len(content.encode("utf-8"))

        if patch_size > self.max_patch_size_bytes:
            raise ValueError("Patch exceeds the configured total patch-size limit.")

        diff = self._git_diff(worktree_root)
        diff_bytes = diff.encode("utf-8")
        patch_sha256 = hashlib.sha256(diff_bytes).hexdigest()

        artifact = PatchArtifact(
            task_id=task.task_id,
            patch_id=f"patch-{task.task_id}",
            patch_sha256=patch_sha256,
            patch_size=len(diff_bytes),
            diff=diff,
            changed_files=changed_files,
            changed_file_hashes=file_hashes,
            summary=str(proposal.get("summary", "Patch generated successfully.")),
        )
        return artifact

    def _validate_target_path(self, requested: str, worktree_root: Path) -> str:
        if not requested or requested.startswith("/"):
            raise ValueError(f"Absolute patch paths are rejected: '{requested}'")

        candidate = Path(requested)
        if ".." in candidate.parts:
            raise ValueError(f"Path traversal is rejected: '{requested}'")

        normalized = candidate.as_posix()
        if any(segment in {".git", ".env", ".ssh", ".aws"} for segment in candidate.parts):
            raise ValueError(f"Sensitive path is rejected: '{requested}'")

        suffix = candidate.suffix.lower()
        if suffix not in self.allowed_suffixes:
            raise ValueError(f"Unsupported file type for patch target '{requested}'.")

        absolute_target = (worktree_root / candidate).resolve(strict=False)
        if not str(absolute_target).startswith(str(worktree_root.resolve(strict=True))):
            raise ValueError(f"Patch path escapes the worktree: '{requested}'")

        if candidate.name.lower() in self.forbidden_basenames:
            raise ValueError(f"Sensitive file names are rejected: '{requested}'")

        if absolute_target.exists() and absolute_target.is_symlink():
            raise ValueError(f"Symlink escape is rejected: '{requested}'")

        return normalized

    def _git_diff(self, worktree_root: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(worktree_root), "diff", "--no-ext-diff", "--binary"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(f"Unable to generate patch diff: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout
