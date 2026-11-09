from __future__ import annotations

from typing import Any

from app.agent.base import AIProvider
from app.models.issue import IssueCreate
from app.models.task import EngineeringTask


class MockAIProvider(AIProvider):
    """Deterministic mock provider used for local development and automated tests."""

    def generate_plan(
        self,
        issue: IssueCreate,
        repository: Any,
    ) -> dict[str, Any]:
        repository_lanes = [
            "review repository structure",
            "inspect relevant source files",
            "patch the root cause",
            "add or update regression tests",
            "verify with the project test suite",
        ]

        task_list = [
            {
                "step": 1,
                "title": "Assess the issue and repository context",
                "details": f"Review the issue '{issue.title}' and the repository '{repository.repository_name}' to confirm the likely failure mode and impacted modules.",
            },
            {
                "step": 2,
                "title": "Identify the likely edit locations",
                "details": "Map the issue to file paths, tests, and the minimal implementation surface before proposing changes.",
            },
            {
                "step": 3,
                "title": "Implement the fix",
                "details": "Update the root cause and keep the patch limited to the affected code path and associated tests.",
            },
            {
                "step": 4,
                "title": "Validate the change",
                "details": "Run the relevant test commands and confirm the result with explicit evidence rather than assumptions.",
            },
        ]

        return {
            "provider": "mock-ai-v1",
            "status": "ready",
            "confidence": "high",
            "summary": f"Implement a focused fix for '{issue.title}' in repository '{repository.repository_name}'. The plan assumes the issue is localized to the affected module and requires validation through existing tests.",
            "repository_focus": repository.test_files[:5] or repository.tracked_files[:5],
            "steps": repository_lanes,
            "tasks": task_list,
        }

    def generate_patch_proposal(self, issue: IssueCreate, task: EngineeringTask) -> dict[str, Any]:
        title = issue.title.lower()
        if "reject" in title or "fail" in title or "invalid" in title and "patch" in title:
            return {
                "status": "rejected",
                "reason": "The mock provider intentionally rejects this request for validation testing.",
                "changes": [],
            }

        file_name = "service.py"
        content = (
            "def sanitize_value(value):\n"
            "    if value is None:\n"
            "        return 'missing'\n"
            "    return str(value).strip()\n"
            "\n"
            "def compute_total(items):\n"
            "    return sum(int(item) for item in items if item is not None)\n"
        )
        return {
            "status": "accepted",
            "summary": f"Add a safe value sanitization helper for '{issue.title}'.",
            "changes": [
                {
                    "path": file_name,
                    "action": "create",
                    "content": content,
                }
            ],
        }
