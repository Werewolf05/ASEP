from __future__ import annotations

import subprocess
from pathlib import Path

from app.agent.mock_provider import MockAIProvider
from app.models.issue import IssueCreate
from app.models.task import EngineeringTask
from app.services.patch_service import PatchGenerationService
from app.services.verification_service import VerificationService
from app.services.worktree_manager import WorktreeManager


def _create_git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "user.email", "test@example.com"], check=True)
    (repo_path / "service.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-m", "initial commit"], check=True, capture_output=True)
    return repo_path


def test_worktree_manager_creates_and_cleans_up(tmp_path: Path) -> None:
    repo_path = _create_git_repo(tmp_path)
    manager = WorktreeManager(worktree_root=str(tmp_path / "worktrees"))
    record = manager.create_worktree(str(repo_path), "task-001")

    assert record.task_id == "task-001"
    assert record.base_commit_sha
    assert Path(record.worktree_path).exists()

    removed = manager.cleanup_worktree("task-001", str(repo_path))
    assert removed is True
    assert not Path(record.worktree_path).exists()


def test_original_repository_remains_unchanged_after_patch(tmp_path: Path) -> None:
    repo_path = _create_git_repo(tmp_path)
    manager = WorktreeManager(worktree_root=str(tmp_path / "worktrees"))
    record = manager.create_worktree(str(repo_path), "task-002")
    task = EngineeringTask(
        task_id="task-002",
        title="Fix logic",
        description="Harden input handling.",
        repository_path=str(repo_path),
        worktree_path=record.worktree_path,
        base_commit_sha=record.base_commit_sha,
    )

    issue = IssueCreate(title="Fix logic", description="Harden input handling.")
    service = PatchGenerationService(provider=MockAIProvider(), worktree_manager=manager)
    artifact = service.generate_patch(issue, task)

    assert artifact.patch_sha256
    assert artifact.changed_files
    status_before = subprocess.run(["git", "-C", str(repo_path), "status", "--porcelain"], capture_output=True, text=True, check=False)
    assert status_before.stdout.strip() == ""

    manager.cleanup_worktree("task-002", str(repo_path))


def test_patch_generation_rejects_invalid_patch_request(tmp_path: Path) -> None:
    repo_path = _create_git_repo(tmp_path)
    manager = WorktreeManager(worktree_root=str(tmp_path / "worktrees"))
    record = manager.create_worktree(str(repo_path), "task-003")
    task = EngineeringTask(
        task_id="task-003",
        title="Invalid patch request",
        description="This patch should be rejected intentionally.",
        repository_path=str(repo_path),
        worktree_path=record.worktree_path,
        base_commit_sha=record.base_commit_sha,
    )
    issue = IssueCreate(title="Invalid patch request", description="This patch should be rejected intentionally.")

    service = PatchGenerationService(provider=MockAIProvider(), worktree_manager=manager)
    try:
        service.generate_patch(issue, task)
        raise AssertionError("Expected invalid patch proposal to be rejected.")
    except ValueError:
        pass

    manager.cleanup_worktree("task-003", str(repo_path))


def test_verification_service_marks_docker_unavailable(tmp_path: Path) -> None:
    class StubDocker:
        def is_available(self) -> bool:
            return False

    class StubVerificationService(VerificationService):
        pass

    service = StubVerificationService(docker_service=StubDocker())
    report = service.run_verification("task-004", str(tmp_path), None)

    assert report.status == "FAILED"
    assert report.docker_available is False
    assert any(check.status == "SKIPPED" for check in report.checks)


def test_task_evidence_and_cleanup_flow(tmp_path: Path) -> None:
    repo_path = _create_git_repo(tmp_path)
    manager = WorktreeManager(worktree_root=str(tmp_path / "worktrees"))
    record = manager.create_worktree(str(repo_path), "task-005")
    task = EngineeringTask(
        task_id="task-005",
        title="Fix bug",
        description="Add safer handling for invalid input.",
        repository_path=str(repo_path),
        worktree_path=record.worktree_path,
        base_commit_sha=record.base_commit_sha,
    )

    service = PatchGenerationService(provider=MockAIProvider(), worktree_manager=manager)
    artifact = service.generate_patch(IssueCreate(title="Fix bug", description="Add safer handling for invalid input."), task)

    assert artifact.patch_size > 0
    assert artifact.patch_sha256

    manager.cleanup_worktree("task-005", str(repo_path))
