from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.agent.mock_provider import MockAIProvider
from app.api.schemas import (
    EngineeringTaskRequest,
    HealthResponse,
    IssueSubmissionRequest,
    IssueSubmissionResponse,
    PatchGenerationResponse,
    PlanningRequest,
    PlanningResponse,
    RepositoryInspectionRequest,
    RepositoryInspectionResponse,
    VerificationRequest,
)
from app.config import load_settings
from app.models.task import LifecycleStatus
from app.repository.inspector import RepositoryInspector
from app.services.docker_service import DockerSandboxService
from app.services.issue_service import IssueService
from app.services.patch_service import PatchGenerationService
from app.services.task_service import TaskService
from app.services.verification_service import VerificationService
from app.services.worktree_manager import WorktreeManager

router = APIRouter()
settings = load_settings()
issue_service = IssueService()
repository_inspector = RepositoryInspector(allowed_roots=settings.allowed_repository_roots)
worktree_manager = WorktreeManager()
task_service = TaskService()
patch_service = PatchGenerationService(provider=MockAIProvider(), worktree_manager=worktree_manager)
verification_service = VerificationService(docker_service=DockerSandboxService())


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name.upper())


@router.post("/api/v1/issues", response_model=IssueSubmissionResponse)
def submit_issue(payload: IssueSubmissionRequest) -> IssueSubmissionResponse:
    issue = payload.to_issue()
    record = issue_service.submit(issue)
    return IssueSubmissionResponse(issue=record)


@router.post("/api/v1/repositories/inspect", response_model=RepositoryInspectionResponse)
def inspect_repository(payload: RepositoryInspectionRequest) -> RepositoryInspectionResponse:
    try:
        repository = repository_inspector.inspect(payload.repository_path)
        return RepositoryInspectionResponse(repository=repository)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/v1/agent/plan", response_model=PlanningResponse)
def generate_plan(payload: PlanningRequest) -> PlanningResponse:
    try:
        issue = payload.to_issue()
        repository = repository_inspector.inspect(payload.repository_path)
        plan = MockAIProvider().generate_plan(issue, repository)
        return PlanningResponse(
            issue=issue.model_dump(),
            repository=repository,
            plan=plan,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/v1/agent/patch", response_model=PatchGenerationResponse)
def generate_patch(payload: EngineeringTaskRequest) -> PatchGenerationResponse:
    issue = payload.to_issue()
    if issue.repository_path is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="repository_path is required.")

    try:
        repository_inspector.inspect(issue.repository_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        task = task_service.create_task(issue.title, issue.description, issue.repository_path)
        worktree_record = worktree_manager.create_worktree(issue.repository_path, task.task_id)
        task = task_service.update_task(
            task.task_id,
            worktree_path=worktree_record.worktree_path,
            base_commit_sha=worktree_record.base_commit_sha,
            status=LifecycleStatus.WORKTREE_READY,
            metadata={"worktree_created": True},
        )
        patch_artifact = patch_service.generate_patch(issue, task)
        task_service.store_patch(task.task_id, patch_artifact)
        task_service.update_task(
            task.task_id,
            status=LifecycleStatus.PATCH_APPLIED,
            metadata={"patch_artifact_id": patch_artifact.patch_id, "worktree_created": True},
        )
        return PatchGenerationResponse(task_id=task.task_id, status=LifecycleStatus.PATCH_APPLIED.value, task=task, patch=patch_artifact)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/v1/verification/run")
def run_verification(payload: VerificationRequest) -> dict[str, object]:
    task_id = payload.task_id
    try:
        task = task_service.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if task.worktree_path is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task is missing a valid worktree.")

    try:
        patch_artifact = task_service.get_patch(task_id)
    except KeyError:
        patch_artifact = None

    report = verification_service.run_verification(task_id, task.worktree_path, patch_artifact)
    task_service.store_verification_report(task_id, report)
    task_service.update_task(task_id, status=LifecycleStatus.VERIFIED if report.status == "VERIFIED" else LifecycleStatus.FAILED)
    return {
        "task_id": task_id,
        "status": report.status,
        "summary": report.summary,
        "checks": [check.model_dump() for check in report.checks],
    }


@router.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, object]:
    try:
        task = task_service.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return task.model_dump()


@router.get("/api/v1/tasks/{task_id}/evidence")
def get_task_evidence(task_id: str) -> dict[str, object]:
    try:
        task = task_service.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    patch_artifact = None
    verification_report = None
    try:
        patch_artifact = task_service.get_patch(task_id)
    except KeyError:
        pass

    try:
        verification_report = task_service.get_verification_report(task_id)
    except KeyError:
        pass

    return {
        "task": task.model_dump(),
        "patch": patch_artifact.model_dump() if patch_artifact else None,
        "verification": verification_report.model_dump() if verification_report else None,
    }
