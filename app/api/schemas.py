from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.models.issue import IssueCreate, IssueRecord, RepositoryInspectionResult
from app.models.task import (
    LifecycleStatus,
    PatchArtifact,
    VerificationReport,
    WorktreeRecord,
)

TaskId = Annotated[str, Field(pattern=r"^task-[0-9a-f]{32}$")]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "ASEP"


class IssueSubmissionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=10, max_length=12000)
    repository_path: str | None = None
    tags: list[str] = Field(default_factory=list)

    def to_issue(self) -> IssueCreate:
        return IssueCreate(
            title=self.title,
            description=self.description,
            repository_path=self.repository_path,
            tags=self.tags,
        )


class IssueSubmissionResponse(BaseModel):
    issue: IssueRecord


class RepositoryInspectionRequest(BaseModel):
    repository_path: str = Field(..., min_length=1)


class RepositoryInspectionResponse(BaseModel):
    repository: RepositoryInspectionResult


class PlanningRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=10, max_length=12000)
    repository_path: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)

    def to_issue(self) -> IssueCreate:
        return IssueCreate(
            title=self.title,
            description=self.description,
            repository_path=self.repository_path,
            tags=self.tags,
        )


class PlanningResponse(BaseModel):
    issue: dict[str, Any]
    repository: RepositoryInspectionResult
    plan: dict[str, Any]


class EngineeringTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=10, max_length=12000)
    repository_path: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)

    def to_issue(self) -> IssueCreate:
        return IssueCreate(
            title=self.title,
            description=self.description,
            repository_path=self.repository_path,
            tags=self.tags,
        )


class PatchGenerationResponse(BaseModel):
    task_id: TaskId
    status: LifecycleStatus
    task: PublicTask
    patch: PatchArtifact


class VerificationRequest(BaseModel):
    task_id: TaskId


class PublicTask(BaseModel):
    task_id: TaskId
    title: str
    description: str
    repository_name: str
    status: LifecycleStatus
    base_commit_sha: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublicWorktree(BaseModel):
    task_id: TaskId
    repository_name: str
    base_commit_sha: str
    created_at: datetime
    dirty_repository: bool
    cleanup_completed: bool

    @classmethod
    def from_record(cls, record: WorktreeRecord) -> PublicWorktree:
        return cls(
            task_id=record.task_id,
            repository_name=Path(record.original_repository_path).name,
            base_commit_sha=record.base_commit_sha,
            created_at=record.created_at,
            dirty_repository=record.dirty_repository,
            cleanup_completed=record.cleanup_completed,
        )


class TaskEvidenceResponse(BaseModel):
    task: PublicTask
    worktree: PublicWorktree | None = None
    patch: PatchArtifact | None = None
    verification: VerificationReport | None = None
