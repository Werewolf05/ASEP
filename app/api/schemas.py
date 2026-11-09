from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.issue import IssueCreate, IssueRecord, RepositoryInspectionResult
from app.models.task import EngineeringTask, PatchArtifact, VerificationReport


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
    task_id: str
    status: str
    task: EngineeringTask
    patch: PatchArtifact


class VerificationRequest(BaseModel):
    task_id: str = Field(..., min_length=1)


class TaskEvidenceResponse(BaseModel):
    task: EngineeringTask
    patch: PatchArtifact | None = None
    verification: VerificationReport | None = None
