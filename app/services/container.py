from __future__ import annotations

from dataclasses import dataclass

from app.agent.mock_provider import MockAIProvider
from app.config import Settings, load_settings
from app.repository.inspector import RepositoryInspector
from app.services.docker_service import DockerSandboxService
from app.services.issue_service import IssueService
from app.services.patch_service import PatchGenerationService
from app.services.task_service import TaskService
from app.services.verification_service import VerificationService
from app.services.worktree_manager import WorktreeManager


@dataclass
class AppServices:
    provider: MockAIProvider
    issue_service: IssueService
    repository_inspector: RepositoryInspector
    worktree_manager: WorktreeManager
    task_service: TaskService
    patch_service: PatchGenerationService
    verification_service: VerificationService

    @classmethod
    def create(cls, settings: Settings | None = None) -> AppServices:
        config = settings or load_settings()
        worktrees = WorktreeManager()
        docker = DockerSandboxService()
        provider = MockAIProvider()
        return cls(
            provider=provider,
            issue_service=IssueService(),
            repository_inspector=RepositoryInspector(
                allowed_roots=config.allowed_repository_roots
            ),
            worktree_manager=worktrees,
            task_service=TaskService(),
            patch_service=PatchGenerationService(
                provider=provider,
                worktree_manager=worktrees,
            ),
            verification_service=VerificationService(docker),
        )
