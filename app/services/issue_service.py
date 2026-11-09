from __future__ import annotations

import uuid
from collections.abc import Iterable

from app.models.issue import IssueCreate, IssueRecord


class IssueService:
    """In-memory issue storage for the initial milestone."""

    def __init__(self) -> None:
        self._issues: dict[str, IssueRecord] = {}

    def submit(self, issue: IssueCreate) -> IssueRecord:
        issue_id = f"issue-{uuid.uuid4().hex[:8]}"
        record = IssueRecord(
            id=issue_id,
            title=issue.title,
            description=issue.description,
            repository_path=issue.repository_path,
            tags=issue.tags,
        )
        self._issues[issue_id] = record
        return record

    def list_issues(self) -> Iterable[IssueRecord]:
        return list(self._issues.values())
