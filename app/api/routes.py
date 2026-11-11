from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.agent.planner import ImplementationPlan
from app.api.dependencies import get_services
from app.api.schemas import (
    EngineeringTaskRequest,
    HealthResponse,
    IssueSubmissionRequest,
    IssueSubmissionResponse,
    PatchGenerationResponse,
    PlanningRequest,
    PlanningResponse,
    PublicTask,
    PublicWorktree,
    RepositoryInspectionRequest,
    RepositoryInspectionResponse,
    TaskEvidenceResponse,
    TaskId,
    VerificationRequest,
)
from app.models.task import (
    EngineeringTask,
    LifecycleStatus,
    ReportStatus,
    VerificationReport,
)
from app.services.container import AppServices

router = APIRouter()
logger = logging.getLogger(__name__)


def _public_task(task: EngineeringTask) -> PublicTask:
    return PublicTask(
        task_id=task.task_id,
        title=task.title,
        description=task.description,
        repository_name=Path(task.repository_path).name,
        status=task.status,
        base_commit_sha=task.base_commit_sha,
        created_at=task.created_at,
        metadata={
            key: value
            for key, value in task.metadata.items()
            if key in {"worktree_created", "patch_artifact_id"}
        },
    )


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="ASEP")


@router.post("/api/v1/issues", response_model=IssueSubmissionResponse)
def submit_issue(
    payload: IssueSubmissionRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> IssueSubmissionResponse:
    return IssueSubmissionResponse(issue=services.issue_service.submit(payload.to_issue()))


@router.post("/api/v1/repositories/inspect", response_model=RepositoryInspectionResponse)
def inspect_repository(
    payload: RepositoryInspectionRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> RepositoryInspectionResponse:
    try:
        repository = services.repository_inspector.inspect(payload.repository_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository was not found or is inaccessible.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository path is invalid or outside the configured allowed directories.",
        ) from exc
    return RepositoryInspectionResponse(repository=repository)


@router.post("/api/v1/agent/plan", response_model=PlanningResponse)
def generate_plan(
    payload: PlanningRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> PlanningResponse:
    issue = payload.to_issue()
    try:
        repository = services.repository_inspector.inspect(payload.repository_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository was not found or is inaccessible.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository path is invalid or outside the configured allowed directories.",
        ) from exc
    plan = ImplementationPlan.model_validate(
        services.provider.generate_plan(issue, repository)
    )
    return PlanningResponse(issue=issue.model_dump(), repository=repository, plan=plan.model_dump())


@router.post("/api/v1/agent/patch", response_model=PatchGenerationResponse)
def generate_patch(
    payload: EngineeringTaskRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> PatchGenerationResponse:
    issue = payload.to_issue()
    repository_path = payload.repository_path
    try:
        services.repository_inspector.inspect(repository_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository was not found or is inaccessible.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository path is invalid or outside the configured allowed directories.",
        ) from exc

    task = services.task_service.create_task(
        issue.title, issue.description, repository_path
    )
    try:
        worktree = services.worktree_manager.create_worktree(repository_path, task.task_id)
        services.task_service.store_worktree(task.task_id, worktree)
        task = services.task_service.update_task(
            task.task_id,
            worktree_path=worktree.worktree_path,
            base_commit_sha=worktree.base_commit_sha,
            status=LifecycleStatus.WORKTREE_READY,
            metadata={"worktree_created": True},
        )
        patch = services.patch_service.generate_patch(issue, task)
        services.task_service.store_patch(task.task_id, patch)
        task = services.task_service.update_task(
            task.task_id, status=LifecycleStatus.PATCH_GENERATED
        )
        task = services.task_service.update_task(
            task.task_id,
            status=LifecycleStatus.PATCH_APPLIED,
            metadata={"patch_artifact_id": patch.patch_id, "worktree_created": True},
        )
        return PatchGenerationResponse(
            task_id=task.task_id,
            status=task.status,
            task=_public_task(task),
            patch=patch,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        logger.exception("Patch generation failed for task %s", task.task_id)
        current_task = services.task_service.get_task(task.task_id)
        failed_status = (
            LifecycleStatus.REJECTED
            if "rejected" in str(exc).casefold()
            else LifecycleStatus.FAILED
        )
        if current_task.status not in {
            LifecycleStatus.FAILED,
            LifecycleStatus.REJECTED,
        }:
            services.task_service.update_task(
                task.task_id,
                status=failed_status,
                metadata={"failure": str(exc), "worktree_created": current_task.worktree_path is not None},
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "task_id": task.task_id,
                "error": "Patch generation failed. The task status records the outcome.",
            },
        ) from exc


@router.post(
    "/api/v1/verification/run",
    response_model=VerificationReport,
)
def run_verification(
    payload: VerificationRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> VerificationReport:
    try:
        task = services.task_service.get_task(payload.task_id)
        worktree = services.task_service.get_worktree(payload.task_id)
        patch = services.task_service.get_patch(payload.task_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if task.status != LifecycleStatus.PATCH_APPLIED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task must have an applied patch before verification.",
        )

    services.task_service.update_task(task.task_id, status=LifecycleStatus.VERIFYING)
    try:
        report = services.verification_service.run_verification(
            task.task_id, worktree.worktree_path, patch
        )
    except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError):
        logger.exception("Verification failed unexpectedly for task %s", task.task_id)
        report = VerificationReport(
            task_id=task.task_id,
            status=ReportStatus.INCOMPLETE,
            summary="Verification could not be completed due to an internal execution error.",
            docker_available=False,
            image_available=False,
        )
    services.task_service.store_verification_report(task.task_id, report)
    services.task_service.update_task(
        task.task_id, status=LifecycleStatus(report.status.value)
    )
    return report


@router.get("/api/v1/tasks/{task_id}", response_model=PublicTask)
def get_task(
    task_id: TaskId,
    services: Annotated[AppServices, Depends(get_services)],
) -> PublicTask:
    try:
        task = services.task_service.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _public_task(task)


@router.get(
    "/api/v1/tasks/{task_id}/evidence",
    response_model=TaskEvidenceResponse,
)
def get_task_evidence(
    task_id: TaskId,
    services: Annotated[AppServices, Depends(get_services)],
) -> TaskEvidenceResponse:
    try:
        task = services.task_service.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        worktree = services.task_service.get_worktree(task_id)
    except KeyError:
        worktree = None
    try:
        patch = services.task_service.get_patch(task_id)
    except KeyError:
        patch = None
    try:
        report = services.task_service.get_verification_report(task_id)
    except KeyError:
        report = None
    return TaskEvidenceResponse(
        task=_public_task(task),
        worktree=PublicWorktree.from_record(worktree) if worktree else None,
        patch=patch,
        verification=report,
    )
