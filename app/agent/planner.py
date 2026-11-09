from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent.base import AIProvider
from app.models.issue import IssueCreate, RepositoryInspectionResult


class ImplementationPlan(BaseModel):
    """Structured implementation plan returned to the API."""

    provider: str
    status: str
    confidence: str
    summary: str
    repository_focus: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    tasks: list[dict[str, object]] = Field(default_factory=list)


class Planner:
    """Builds an issue-to-plan output using an AI provider abstraction."""

    def __init__(self, provider: AIProvider) -> None:
        self.provider = provider

    def build_plan(self, issue: IssueCreate, repository: RepositoryInspectionResult) -> ImplementationPlan:
        raw_plan = self.provider.generate_plan(issue, repository)
        return ImplementationPlan(
            provider=str(raw_plan.get("provider", "unknown-provider")),
            status=str(raw_plan.get("status", "ready")),
            confidence=str(raw_plan.get("confidence", "medium")),
            summary=str(raw_plan.get("summary", "Implementation plan generated.")),
            repository_focus=[str(value) for value in raw_plan.get("repository_focus", [])],
            steps=[str(value) for value in raw_plan.get("steps", [])],
            tasks=[dict(task) for task in raw_plan.get("tasks", [])],
        )
