from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable

from app.models.task import (
    EngineeringTask,
    LifecycleStatus,
    PatchArtifact,
    VerificationReport,
    WorktreeRecord,
)

_TRANSITIONS: dict[LifecycleStatus, set[LifecycleStatus]] = {
    LifecycleStatus.CREATED: {
        LifecycleStatus.WORKTREE_READY,
        LifecycleStatus.FAILED,
        LifecycleStatus.REJECTED,
    },
    LifecycleStatus.WORKTREE_READY: {
        LifecycleStatus.PATCH_GENERATED,
        LifecycleStatus.FAILED,
        LifecycleStatus.REJECTED,
    },
    LifecycleStatus.PATCH_GENERATED: {
        LifecycleStatus.PATCH_APPLIED,
        LifecycleStatus.FAILED,
    },
    LifecycleStatus.PATCH_APPLIED: {
        LifecycleStatus.VERIFYING,
        LifecycleStatus.FAILED,
    },
    LifecycleStatus.VERIFYING: {
        LifecycleStatus.VERIFIED,
        LifecycleStatus.FAILED,
        LifecycleStatus.INCOMPLETE,
        LifecycleStatus.BLOCKED,
    },
    LifecycleStatus.VERIFIED: set(),
    LifecycleStatus.FAILED: set(),
    LifecycleStatus.INCOMPLETE: set(),
    LifecycleStatus.BLOCKED: set(),
    LifecycleStatus.REJECTED: set(),
    LifecycleStatus.HUMAN_REVIEW_REQUIRED: set(),
}


class TaskService:
    """Thread-safe in-memory task and evidence store for the initial milestone."""

    def __init__(self) -> None:
        self._tasks: dict[str, EngineeringTask] = {}
        self._worktrees: dict[str, WorktreeRecord] = {}
        self._patches: dict[str, PatchArtifact] = {}
        self._verification_reports: dict[str, VerificationReport] = {}
        self._lock = threading.RLock()

    def create_task(self, title: str, description: str, repository_path: str) -> EngineeringTask:
        with self._lock:
            while True:
                task_id = f"task-{uuid.uuid4().hex}"
                if task_id not in self._tasks:
                    break
            task = EngineeringTask(
                task_id=task_id,
                title=title,
                description=description,
                repository_path=repository_path,
            )
            self._tasks[task_id] = task
            return task.model_copy(deep=True)

    def get_task(self, task_id: str) -> EngineeringTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' was not found.")
            return task.model_copy(deep=True)

    def update_task(self, task_id: str, **updates: object) -> EngineeringTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task '{task_id}' was not found.")
            updated = task.model_copy(update=updates, deep=True)
            if updated.status != task.status and updated.status not in _TRANSITIONS[task.status]:
                raise ValueError(f"Invalid task transition: {task.status} -> {updated.status}.")
            self._tasks[task_id] = updated
            return updated.model_copy(deep=True)

    def store_worktree(self, task_id: str, record: WorktreeRecord) -> None:
        with self._lock:
            self._require_task(task_id)
            if record.task_id != task_id:
                raise ValueError("Worktree record belongs to a different task.")
            self._worktrees[task_id] = record.model_copy(deep=True)

    def get_worktree(self, task_id: str) -> WorktreeRecord:
        with self._lock:
            self._require_task(task_id)
            record = self._worktrees.get(task_id)
            if record is None:
                raise KeyError(f"Worktree for task '{task_id}' was not found.")
            return record.model_copy(deep=True)

    def store_patch(self, task_id: str, patch: PatchArtifact) -> None:
        with self._lock:
            self._require_task(task_id)
            if patch.task_id != task_id:
                raise ValueError("Patch artifact belongs to a different task.")
            self._patches[task_id] = patch.model_copy(deep=True)

    def get_patch(self, task_id: str) -> PatchArtifact:
        with self._lock:
            self._require_task(task_id)
            patch = self._patches.get(task_id)
            if patch is None:
                raise KeyError(f"Patch for task '{task_id}' was not found.")
            return patch.model_copy(deep=True)

    def store_verification_report(self, task_id: str, report: VerificationReport) -> None:
        with self._lock:
            self._require_task(task_id)
            if report.task_id != task_id:
                raise ValueError("Verification report belongs to a different task.")
            self._verification_reports[task_id] = report.model_copy(deep=True)

    def get_verification_report(self, task_id: str) -> VerificationReport:
        with self._lock:
            self._require_task(task_id)
            report = self._verification_reports.get(task_id)
            if report is None:
                raise KeyError(f"Verification report for task '{task_id}' was not found.")
            return report.model_copy(deep=True)

    def all_tasks(self) -> Iterable[EngineeringTask]:
        with self._lock:
            return [task.model_copy(deep=True) for task in self._tasks.values()]

    def _require_task(self, task_id: str) -> None:
        if task_id not in self._tasks:
            raise KeyError(f"Task '{task_id}' was not found.")
