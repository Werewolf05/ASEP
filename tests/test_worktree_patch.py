from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agent.base import AIProvider
from app.models.issue import IssueCreate
from app.models.task import (
    CheckStatus,
    EngineeringTask,
    LifecycleStatus,
    PatchArtifact,
    ReportStatus,
    VerificationReport,
)
from app.services.docker_service import DockerExecutionResult
from app.services.patch_service import PatchGenerationService
from app.services.task_service import TaskService
from app.services.verification_service import VerificationService
from app.services.worktree_manager import WorktreeManager


def _task_id() -> str:
    return f"task-{uuid.uuid4().hex}"


def _create_git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "user.email", "test@example.com"], check=True)
    (repo_path / "service.py").write_text("value = 1\n", encoding="utf-8")
    (repo_path / "delete_me.py").write_text("old = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "initial commit"],
        check=True,
        capture_output=True,
    )
    return repo_path


def _make_task(repo: Path, manager: WorktreeManager) -> EngineeringTask:
    task_id = _task_id()
    record = manager.create_worktree(str(repo), task_id)
    return EngineeringTask(
        task_id=task_id,
        title="Fix logic",
        description="Harden input handling.",
        repository_path=record.original_repository_path,
        worktree_path=record.worktree_path,
        base_commit_sha=record.base_commit_sha,
        status=LifecycleStatus.WORKTREE_READY,
    )


class StaticProvider(AIProvider):
    def __init__(self, changes: list[dict[str, Any]]) -> None:
        self.changes = changes

    def generate_plan(self, issue: IssueCreate, repository: Any) -> dict[str, Any]:
        return {}

    def generate_patch_proposal(
        self, issue: IssueCreate, task: EngineeringTask
    ) -> dict[str, Any]:
        return {"status": "accepted", "changes": self.changes, "summary": "test patch"}


class StubDocker:
    def __init__(
        self,
        *,
        available: bool = True,
        image_available: bool = True,
        results: dict[str, DockerExecutionResult] | None = None,
    ) -> None:
        self.available = available
        self.has_image = image_available
        self.results = results or {}
        self.commands: list[list[str]] = []

    @property
    def image(self) -> str:
        return "mock-verification-image"

    def is_available(self) -> bool:
        return self.available

    def image_available(self) -> bool:
        return self.has_image

    def run(
        self, worktree_path: str, command: list[str], timeout_seconds: int = 120
    ) -> DockerExecutionResult:
        self.commands.append(command)
        name = command[0]
        return self.results.get(
            name,
            DockerExecutionResult(CheckStatus.PASS, 0, "ok", "", 4),
        )


def _patch(
    repo: Path,
    tmp_path: Path,
    changes: list[dict[str, Any]],
) -> tuple[WorktreeManager, EngineeringTask, PatchArtifact]:
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task = _make_task(repo, manager)
    service = PatchGenerationService(
        provider=StaticProvider(changes),
        worktree_manager=manager,
    )
    artifact = service.generate_patch(
        IssueCreate(title="Fix logic", description="Harden input handling."),
        task,
    )
    return manager, task, artifact


def test_worktree_manager_creates_and_cleans_up_safely(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task_id = _task_id()
    record = manager.create_worktree(str(repo), task_id)
    assert Path(record.worktree_path).exists()
    assert Path(record.original_repository_path) == repo.resolve()
    assert record.base_commit_sha
    assert manager.cleanup_worktree(record) is True
    assert not Path(record.worktree_path).exists()


def test_worktree_creation_does_not_run_repository_hooks_or_filters(
    tmp_path: Path,
) -> None:
    repo = _create_git_repo(tmp_path)
    hook_marker = tmp_path / "post-checkout-ran"
    filter_marker = tmp_path / "smudge-filter-ran"
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch '{hook_marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    (repo / ".gitattributes").write_text("*.py filter=unsafe\n", encoding="utf-8")
    (repo / "filter.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "config", "filter.unsafe.smudge", f"touch '{filter_marker}'"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "configure fixture filter"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git", "-C", str(repo), "config", "filter.unsafe.clean",
            f"touch '{filter_marker}'; cat",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git", "-C", str(repo), "config", "diff.external",
            f"touch '{filter_marker}'",
        ],
        check=True,
    )
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    record = manager.create_worktree(str(repo), _task_id())
    task = EngineeringTask(
        task_id=record.task_id,
        title="Add a safe marker",
        description="Ensure host Git does not execute repository hooks or filters.",
        repository_path=record.original_repository_path,
        worktree_path=record.worktree_path,
        base_commit_sha=record.base_commit_sha,
        status=LifecycleStatus.WORKTREE_READY,
    )
    patch = PatchGenerationService(
        StaticProvider(
            [{"path": "new_module.py", "action": "create", "content": "value = 2\n"}]
        ),
        manager,
    ).generate_patch(
        IssueCreate(title=task.title, description=task.description),
        task,
    )
    VerificationService(StubDocker(available=False)).run_verification(
        task.task_id, record.worktree_path, patch
    )

    assert not hook_marker.exists()
    assert not filter_marker.exists()
    assert (Path(record.worktree_path) / "service.py").read_text(encoding="utf-8") == "value = 1\n"
    assert manager.cleanup_worktree(record) is False


def test_worktree_rejects_dirty_original_repository_without_modifying_it(
    tmp_path: Path,
) -> None:
    repo = _create_git_repo(tmp_path)
    (repo / "uncommitted.py").write_text("dirty = True\n", encoding="utf-8")
    before = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    with pytest.raises(ValueError, match="uncommitted changes"):
        manager.create_worktree(str(repo))
    after = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert after == before


def test_worktree_rejects_repository_symlink_escape_and_cleans_partial_worktree(
    tmp_path: Path,
) -> None:
    repo = _create_git_repo(tmp_path)
    (repo / "escape.py").symlink_to("../../outside.py")
    subprocess.run(["git", "-C", str(repo), "add", "escape.py"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "add escaping symlink"],
        check=True,
        capture_output=True,
    )
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task_id = _task_id()

    with pytest.raises(ValueError, match="symlink"):
        manager.create_worktree(str(repo), task_id)
    assert not (tmp_path / "outside.py").exists()
    assert not (manager.worktree_root / task_id).exists()


def test_worktree_rejects_invalid_cleanup_ids_and_arbitrary_paths(
    tmp_path: Path,
) -> None:
    repo = _create_git_repo(tmp_path)
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    arbitrary = tmp_path / "worktrees" / "important"
    arbitrary.mkdir()
    (arbitrary / "keep.txt").write_text("keep", encoding="utf-8")
    invalid = {
        "task_id": "../important",
        "original_repository_path": str(repo),
        "worktree_path": str(arbitrary),
        "base_commit_sha": "0" * 40,
    }
    from app.models.task import WorktreeRecord

    assert manager.cleanup_worktree(WorktreeRecord(**invalid)) is False
    assert (arbitrary / "keep.txt").exists()


def test_worktree_cleanup_does_not_force_remove_task_changes(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    record = manager.create_worktree(str(repo), _task_id())
    task_path = Path(record.worktree_path) / "new.py"
    task_path.write_text("changed = True\n", encoding="utf-8")

    assert manager.cleanup_worktree(record) is False
    assert task_path.exists()
    assert repo.joinpath("service.py").read_text(encoding="utf-8") == "value = 1\n"


def test_worktree_task_ids_are_collision_resistant(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    first = manager.create_worktree(str(repo))
    second = manager.create_worktree(str(repo))
    assert first.task_id != second.task_id


def test_original_repository_remains_unchanged_after_patch(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    before = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    _manager, _task, artifact = _patch(
        repo,
        tmp_path,
        [{"path": "new_module.py", "action": "create", "content": "value = 2\n"}],
    )
    assert artifact.patch_sha256
    assert subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout == before


def test_new_untracked_file_is_in_complete_patch_artifact(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, _task, artifact = _patch(
        repo,
        tmp_path,
        [{"path": "new_module.py", "action": "create", "content": "value = 2\n"}],
    )
    assert "new_module.py" in artifact.diff
    assert "value = 2" in artifact.diff
    assert artifact.changed_files == ["new_module.py"]
    assert artifact.patch_sha256 == hashlib.sha256(artifact.diff.encode()).hexdigest()
    assert "new_module.py" in artifact.changed_file_hashes
    applicable = subprocess.run(
        ["git", "-C", str(repo), "apply", "--check", "-"],
        input=artifact.diff,
        capture_output=True,
        text=True,
        check=False,
    )
    assert applicable.returncode == 0, applicable.stderr


def test_modified_tracked_file_is_in_patch_artifact(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, _task, artifact = _patch(
        repo,
        tmp_path,
        [{"path": "service.py", "action": "modify", "content": "value = 2\n"}],
    )
    assert "service.py" in artifact.changed_files
    assert "-value = 1" in artifact.diff
    assert "+value = 2" in artifact.diff


def test_deleted_file_is_recorded(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, _task, artifact = _patch(
        repo, tmp_path, [{"path": "delete_me.py", "action": "delete"}]
    )
    assert "delete_me.py" in artifact.changed_files
    assert any(change.status == "D" for change in artifact.file_changes)
    assert "delete_me.py" not in artifact.changed_file_hashes


def test_renamed_file_is_recorded_with_both_names(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, _task, artifact = _patch(
        repo,
        tmp_path,
        [{"path": "service.py", "new_path": "renamed.py", "action": "rename"}],
    )
    assert "service.py" in artifact.changed_files
    assert "renamed.py" in artifact.changed_files
    assert any(change.status.startswith("R") for change in artifact.file_changes)


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/escape.py",
        "../escape.py",
        "nested/../../escape.py",
        "C:\\escape.py",
        ".ENV",
        "Secrets/key.py",
        "private/id_rsa",
        ".DOCKER/config.json",
        "config/passwords.yaml",
        "unsupported.exe",
    ],
)
def test_malicious_and_unsupported_paths_are_rejected(
    tmp_path: Path, path: str
) -> None:
    repo = _create_git_repo(tmp_path)
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task = _make_task(repo, manager)
    service = PatchGenerationService(
        StaticProvider([{"path": path, "action": "create", "content": "x = 1\n"}]),
        manager,
    )
    with pytest.raises(ValueError):
        service.generate_patch(
            IssueCreate(title="Fix", description="Make a safe patch."),
            task,
        )
    assert not (Path(task.worktree_path or "") / "escape.py").exists()


def test_symlink_escape_and_symlinked_parent_are_rejected(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task = _make_task(repo, manager)
    worktree = Path(task.worktree_path or "")
    (worktree / "linked.py").symlink_to(outside / "victim.py")
    (worktree / "linked_dir").symlink_to(outside, target_is_directory=True)

    for path in ["linked.py", "linked_dir/victim.py"]:
        service = PatchGenerationService(
            StaticProvider([{"path": path, "action": "create", "content": "x = 1\n"}]),
            manager,
        )
        with pytest.raises(ValueError, match="Symlink"):
            service.generate_patch(
                IssueCreate(title="Fix", description="Make a safe patch."),
                task,
            )
    assert not (outside / "victim.py").exists()


def test_entire_proposal_is_validated_before_any_file_is_written(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task = _make_task(repo, manager)
    service = PatchGenerationService(
        StaticProvider(
            [
                {"path": "first.py", "action": "create", "content": "valid = True\n"},
                {"path": "../bad.py", "action": "create", "content": "bad = True\n"},
            ]
        ),
        manager,
    )
    with pytest.raises(ValueError):
        service.generate_patch(
            IssueCreate(title="Fix", description="Make a safe patch."),
            task,
        )
    assert not (Path(task.worktree_path or "") / "first.py").exists()


def test_patch_size_rejection_does_not_partially_write(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task = _make_task(repo, manager)
    service = PatchGenerationService(
        StaticProvider(
            [
                {"path": "first.py", "action": "create", "content": "x" * 200_000},
                {"path": "second.py", "action": "create", "content": "y" * 200_000},
                {"path": "third.py", "action": "create", "content": "z" * 200_000},
                {"path": "fourth.py", "action": "create", "content": "a" * 200_000},
                {"path": "fifth.py", "action": "create", "content": "b" * 200_000},
                {"path": "sixth.py", "action": "create", "content": "c" * 200_000},
            ]
        ),
        manager,
    )
    with pytest.raises(ValueError, match="total patch-size"):
        service.generate_patch(
            IssueCreate(title="Fix", description="Make a safe patch."),
            task,
        )
    worktree = Path(task.worktree_path or "")
    assert not (worktree / "first.py").exists()
    assert not (worktree / "second.py").exists()
    assert not (worktree / "third.py").exists()
    assert not (worktree / "fourth.py").exists()
    assert not (worktree / "fifth.py").exists()
    assert not (worktree / "sixth.py").exists()


def test_complete_diff_size_rejection_restores_deleted_files(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    large_contents = {f"large_{index}.py": "x" * 230_000 + "\n" for index in range(5)}
    for name, content in large_contents.items():
        (repo / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "add large fixtures"],
        check=True,
        capture_output=True,
    )
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task = _make_task(repo, manager)
    service = PatchGenerationService(
        StaticProvider(
            [
                {"path": name, "action": "delete"}
                for name in large_contents
            ]
        ),
        manager,
    )
    with pytest.raises(ValueError, match="Complete patch"):
        service.generate_patch(
            IssueCreate(title="Fix", description="Make a safe patch."),
            task,
        )

    worktree = Path(task.worktree_path or "")
    assert all((worktree / name).read_text(encoding="utf-8") == content for name, content in large_contents.items())
    status = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == ""


def test_single_file_size_rejection_does_not_write(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    manager = WorktreeManager(str(tmp_path / "worktrees"))
    task = _make_task(repo, manager)
    service = PatchGenerationService(
        StaticProvider(
            [{"path": "oversized.py", "action": "create", "content": "x" * 300_000}]
        ),
        manager,
    )
    with pytest.raises(ValueError, match="file-size"):
        service.generate_patch(
            IssueCreate(title="Fix", description="Make a safe patch."),
            task,
        )
    assert not (Path(task.worktree_path or "") / "oversized.py").exists()


def test_task_service_isolates_evidence_and_checks_lifecycle(tmp_path: Path) -> None:
    service = TaskService()
    first = service.create_task("one", "first task description", str(tmp_path / "one"))
    second = service.create_task("two", "second task description", str(tmp_path / "two"))
    assert first.task_id != second.task_id
    assert service.get_task(first.task_id).status == LifecycleStatus.CREATED
    with pytest.raises(ValueError, match="Invalid task transition"):
        service.update_task(first.task_id, status=LifecycleStatus.VERIFIED)

    record = service.update_task(first.task_id, status=LifecycleStatus.FAILED)
    assert record.status == LifecycleStatus.FAILED
    assert service.get_task(second.task_id).status == LifecycleStatus.CREATED


def test_verification_docker_unavailable_blocks_without_host_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, task, patch = _patch(
        repo, tmp_path, [{"path": "new.py", "action": "create", "content": "x = 2\n"}]
    )
    docker = StubDocker(available=False)
    host_commands: list[list[str]] = []
    real_run = subprocess.run

    def checked_run(args: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[Any]:
        command = [str(value) for value in args]
        host_commands.append(command)
        assert not any(Path(value).name in {"pytest", "ruff", "mypy"} for value in command)
        return real_run(args, *a, **kw)

    monkeypatch.setattr("app.services.verification_service.subprocess.run", checked_run)
    report = VerificationService(docker).run_verification(task.task_id, task.worktree_path or "", patch)
    assert report.status == ReportStatus.BLOCKED
    assert all(
        check.status == CheckStatus.SKIPPED
        for check in report.checks
        if check.check_id.startswith("docker-")
    )
    assert docker.commands == []
    assert not any(any(Path(value).name in {"pytest", "ruff", "mypy"} for value in cmd) for cmd in host_commands)


def test_missing_docker_image_blocks_verification(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, task, patch = _patch(
        repo, tmp_path, [{"path": "new.py", "action": "create", "content": "x = 2\n"}]
    )
    report = VerificationService(StubDocker(image_available=False)).run_verification(
        task.task_id, task.worktree_path or "", patch
    )
    assert report.status == ReportStatus.BLOCKED
    assert report.docker_available is True
    assert report.image_available is False


def test_docker_timeout_is_incomplete_not_pass(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, task, patch = _patch(
        repo, tmp_path, [{"path": "new.py", "action": "create", "content": "x = 2\n"}]
    )

    class TimeoutDocker(StubDocker):
        def run(self, worktree_path: str, command: list[str], timeout_seconds: int = 120) -> DockerExecutionResult:
            raise subprocess.TimeoutExpired(command, timeout_seconds)

    report = VerificationService(TimeoutDocker()).run_verification(
        task.task_id, task.worktree_path or "", patch
    )
    assert report.status == ReportStatus.INCOMPLETE
    assert all(
        check.status == CheckStatus.ERROR
        for check in report.checks
        if check.check_id.startswith("docker-")
    )


def test_missing_container_tool_is_skipped_and_report_incomplete(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, task, patch = _patch(
        repo, tmp_path, [{"path": "new.py", "action": "create", "content": "x = 2\n"}]
    )
    docker = StubDocker(
        results={
            "pytest": DockerExecutionResult(CheckStatus.SKIPPED, None, "", "tool missing", 2)
        }
    )
    report = VerificationService(docker).run_verification(
        task.task_id, task.worktree_path or "", patch
    )
    assert report.status == ReportStatus.INCOMPLETE
    assert next(check for check in report.checks if check.check_id == "docker-pytest").status == CheckStatus.SKIPPED


def test_failed_pytest_makes_report_failed(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, task, patch = _patch(
        repo, tmp_path, [{"path": "new.py", "action": "create", "content": "x = 2\n"}]
    )
    docker = StubDocker(
        results={
            "pytest": DockerExecutionResult(CheckStatus.FAIL, 1, "failed", "assertion", 10)
        }
    )
    report = VerificationService(docker).run_verification(
        task.task_id, task.worktree_path or "", patch
    )
    assert report.status == ReportStatus.FAILED


def test_changed_file_hash_mismatch_fails_verification(tmp_path: Path) -> None:
    repo = _create_git_repo(tmp_path)
    _manager, task, patch = _patch(
        repo, tmp_path, [{"path": "new.py", "action": "create", "content": "x = 2\n"}]
    )
    Path(task.worktree_path or "", "new.py").write_text("x = 3\n", encoding="utf-8")
    report = VerificationService(StubDocker()).run_verification(
        task.task_id, task.worktree_path or "", patch
    )
    assert report.status == ReportStatus.FAILED
    changed_paths = next(check for check in report.checks if check.check_id == "changed-paths")
    assert changed_paths.status == CheckStatus.FAIL


def test_task_service_retrieves_patch_and_verification_evidence(tmp_path: Path) -> None:
    service = TaskService()
    task = service.create_task("task", "a sufficiently detailed task", str(tmp_path))
    patch = PatchArtifact(
        task_id=task.task_id,
        patch_id=f"patch-{task.task_id}",
        patch_sha256="a" * 64,
        patch_size=0,
        diff="",
    )
    report = VerificationReport(
        task_id=task.task_id,
        status=ReportStatus.BLOCKED,
        summary="Docker unavailable.",
    )
    service.store_patch(task.task_id, patch)
    service.store_verification_report(task.task_id, report)
    assert service.get_patch(task.task_id).patch_sha256 == "a" * 64
    assert service.get_verification_report(task.task_id).status == ReportStatus.BLOCKED
    with pytest.raises(KeyError):
        service.get_patch(_task_id())
