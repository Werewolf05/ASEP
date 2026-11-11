from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from shutil import copytree, ignore_patterns
from typing import Any

import pytest

from app.agent.base import AIProvider
from app.models.issue import IssueCreate
from app.models.task import CheckStatus, EngineeringTask, LifecycleStatus, ReportStatus
from app.services.docker_service import DockerSandboxService
from app.services.patch_service import PatchGenerationService
from app.services.verification_service import VerificationService
from app.services.worktree_manager import WorktreeManager

pytestmark = pytest.mark.docker_integration


def test_actual_docker_sandbox_is_non_root_and_executes_command(tmp_path: Path) -> None:
    docker = DockerSandboxService()
    if not docker.is_available():
        pytest.skip("Docker daemon is unavailable; this is an actual integration test.")
    if not docker.image_available():
        pytest.skip("Verification image is not built; this is an actual integration test.")

    result = docker.run(
        str(tmp_path),
        [
            "python",
            "-c",
            "import os; print(f'uid={os.getuid()}'); print('sandbox-ok')",
        ],
        timeout_seconds=30,
    )
    assert result.status.value == "PASS"
    assert "uid=1000" in result.stdout
    assert "sandbox-ok" in result.stdout


def test_actual_docker_verification_image_has_no_host_repo_code_execution(
    tmp_path: Path,
) -> None:
    docker = DockerSandboxService()
    if not docker.is_available() or not docker.image_available():
        pytest.skip("Docker daemon or verification image is unavailable.")

    result = docker.run(
        str(tmp_path),
        ["pytest", "--version"],
        timeout_seconds=30,
    )
    assert result.status.value == "PASS"
    assert "pytest" in result.stdout.lower()


def test_actual_verification_service_runs_all_checks_in_docker(tmp_path: Path) -> None:
    docker = DockerSandboxService()
    if not docker.is_available():
        pytest.skip("Docker daemon is unavailable; this is an actual integration test.")
    if not docker.image_available():
        pytest.skip("Verification image is not built; this is an actual integration test.")

    repository = tmp_path / "source"
    repository.mkdir()
    (repository / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n.ruff_cache/\n.mypy_cache/\n",
        encoding="utf-8",
    )
    (repository / "app").mkdir()
    (repository / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\n\n[tool.mypy]\npython_version = '3.11'\n",
        encoding="utf-8",
    )
    (repository / "tests").mkdir()
    (repository / "tests" / "test_sample.py").write_text(
        "def test_sandbox_executes_tests():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Integration"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "integration@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )

    class Provider(AIProvider):
        def generate_plan(self, issue: IssueCreate, repo_context: Any) -> dict[str, Any]:
            return {}

        def generate_patch_proposal(
            self, issue: IssueCreate, task: EngineeringTask
        ) -> dict[str, Any]:
            return {
                "status": "accepted",
                "changes": [
                    {
                        "path": "app/generated.py",
                        "action": "create",
                        "content": "VALUE = 2\n",
                    }
                ],
            }

    task_id = f"task-{uuid.uuid4().hex}"
    worktrees = WorktreeManager(str(tmp_path / "worktrees"))
    record = worktrees.create_worktree(str(repository), task_id)
    task = EngineeringTask(
        task_id=task_id,
        title="Add a generated value",
        description="Add a deterministic generated value for integration testing.",
        repository_path=record.original_repository_path,
        worktree_path=record.worktree_path,
        base_commit_sha=record.base_commit_sha,
        status=LifecycleStatus.WORKTREE_READY,
    )
    patch = PatchGenerationService(Provider(), worktrees).generate_patch(
        IssueCreate(
            title=task.title,
            description=task.description,
        ),
        task,
    )

    report = VerificationService(docker).run_verification(
        task_id,
        record.worktree_path,
        patch,
    )
    assert report.status == ReportStatus.VERIFIED, report.model_dump_json(indent=2)
    assert all(check.status == CheckStatus.PASS for check in report.checks)


def test_actual_verification_service_runs_asep_suite_in_docker(tmp_path: Path) -> None:
    docker = DockerSandboxService()
    if not docker.is_available():
        pytest.skip("Docker daemon is unavailable; this is an actual integration test.")
    if not docker.image_available():
        pytest.skip("Verification image is not built; this is an actual integration test.")

    project_root = Path(__file__).resolve().parents[2]
    repository = tmp_path / "asep-snapshot"
    copytree(
        project_root,
        repository,
        ignore=ignore_patterns(
            ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache",
            "__pycache__", "*.egg-info", ".venv", ".env",
        ),
    )
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Integration"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "integration@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "ASEP integration snapshot"],
        check=True,
        capture_output=True,
    )

    class Provider(AIProvider):
        def generate_plan(self, issue: IssueCreate, repo_context: Any) -> dict[str, Any]:
            return {}

        def generate_patch_proposal(
            self, issue: IssueCreate, task: EngineeringTask
        ) -> dict[str, Any]:
            return {
                "status": "accepted",
                "changes": [
                    {
                        "path": "integration_marker.py",
                        "action": "create",
                        "content": "MARKER = 'verified'\n",
                    }
                ],
            }

    task_id = f"task-{uuid.uuid4().hex}"
    worktrees = WorktreeManager(str(tmp_path / "asep-worktrees"))
    record = worktrees.create_worktree(str(repository), task_id)
    task = EngineeringTask(
        task_id=task_id,
        title="Add an integration marker",
        description="Add a deterministic marker for Docker verification.",
        repository_path=record.original_repository_path,
        worktree_path=record.worktree_path,
        base_commit_sha=record.base_commit_sha,
        status=LifecycleStatus.WORKTREE_READY,
    )
    patch = PatchGenerationService(Provider(), worktrees).generate_patch(
        IssueCreate(title=task.title, description=task.description),
        task,
    )
    report = VerificationService(docker).run_verification(
        task_id, record.worktree_path, patch
    )

    assert report.status == ReportStatus.VERIFIED, report.model_dump_json(indent=2)
    assert all(check.status == CheckStatus.PASS for check in report.checks)
