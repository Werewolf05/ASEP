from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LifecycleStatus(str, Enum):
    CREATED = "CREATED"
    WORKTREE_READY = "WORKTREE_READY"
    PATCH_GENERATED = "PATCH_GENERATED"
    PATCH_APPLIED = "PATCH_APPLIED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class EngineeringTask(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    title: str
    description: str
    repository_path: str
    status: LifecycleStatus = LifecycleStatus.CREATED
    worktree_path: str | None = None
    base_commit_sha: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorktreeRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    original_repository_path: str
    worktree_path: str
    base_commit_sha: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    dirty_repository: bool = False
    cleanup_completed: bool = False


class PatchArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    patch_id: str
    patch_sha256: str
    patch_size: int
    diff: str
    changed_files: list[str] = Field(default_factory=list)
    changed_file_hashes: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = ""


class VerificationCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    check_id: str
    check_name: str
    command: list[str]
    status: str
    exit_code: int | None = None
    duration_ms: int | None = None
    stdout: str = ""
    stderr: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    status: str
    summary: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checks: list[VerificationCheck] = Field(default_factory=list)
    docker_available: bool = False


class TaskEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task: EngineeringTask
    patch: PatchArtifact | None = None
    verification: VerificationReport | None = None
