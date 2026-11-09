from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IssueCreate(BaseModel):
    """Input model for issue intake."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=10, max_length=12000)
    repository_path: str | None = Field(default=None)
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        normalized = []
        for tag in value:
            cleaned = tag.strip().lower()
            if cleaned:
                normalized.append(cleaned)
        return normalized


class IssueRecord(BaseModel):
    """Persisted issue record."""

    id: str
    title: str
    description: str
    repository_path: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RepositoryInspectionResult(BaseModel):
    """Summary of a repository inspection result."""

    repository_name: str
    display_path: str
    git_initialized: bool
    branch_name: str | None = None
    status_summary: str
    tracked_files: list[str]
    language_summary: dict[str, int]
    test_files: list[str]
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)
