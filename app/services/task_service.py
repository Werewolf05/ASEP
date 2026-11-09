from __future__ import annotations

from collections.abc import Iterable

from app.models.task import EngineeringTask, LifecycleStatus, PatchArtifact, VerificationReport


class TaskService:
    """Stores task metadata, evidence, and lifecycle state."""

    def __init__(self) -> None:
        self._tasks: dict[str, EngineeringTask] = {}
        self._patches: dict[str, PatchArtifact] = {}
        self._verification_reports: dict[str, VerificationReport] = {}

    def create_task(self, title: str, description: str, repository_path: str) -> EngineeringTask:
        task_id = f"task-{len(self._tasks) + 1:04d}"
        task = EngineeringTask(
            task_id=task_id,
            title=title,
            description=description,
            repository_path=repository_path,
            status=LifecycleStatus.CREATED,
        )
        self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> EngineeringTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' was not found.")
        return task

    def update_task(self, task_id: str, **updates: object) -> EngineeringTask:
        task = self.get_task(task_id)
        for key, value in updates.items():
            if value is not None:
                setattr(task, key, value)
        self._tasks[task_id] = task
        return task

    def store_patch(self, task_id: str, patch: PatchArtifact) -> None:
        self._patches[task_id] = patch

    def get_patch(self, task_id: str) -> PatchArtifact:
        patch = self._patches.get(task_id)
        if patch is None:
            raise KeyError(f"Patch for task '{task_id}' was not found.")
        return patch

    def store_verification_report(self, task_id: str, report: VerificationReport) -> None:
        self._verification_reports[task_id] = report

    def get_verification_report(self, task_id: str) -> VerificationReport:
        report = self._verification_reports.get(task_id)
        if report is None:
            raise KeyError(f"Verification report for task '{task_id}' was not found.")
        return report

    def all_tasks(self) -> Iterable[EngineeringTask]:
        return list(self._tasks.values())
