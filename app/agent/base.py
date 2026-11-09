from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.issue import IssueCreate
from app.models.task import EngineeringTask


class AIProvider(ABC):
    """Common interface for AI-backed providers."""

    @abstractmethod
    def generate_plan(
        self,
        issue: IssueCreate,
        repository: Any,
    ) -> dict[str, Any]:
        """Return a deterministic structure for the planned work."""

    @abstractmethod
    def generate_patch_proposal(self, issue: IssueCreate, task: EngineeringTask) -> dict[str, Any]:
        """Return a structured patch proposal for the task worktree."""
