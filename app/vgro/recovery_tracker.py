from __future__ import annotations

import threading

from app.vgro.models import (
    RecoveryAttempt,
    RecoveryAttemptRequest,
    VGROAttemptType,
    VGROPolicy,
)


class RecoveryAttemptTracker:
    """Thread-safe in-memory record of explicitly started recovery attempts."""

    def __init__(self) -> None:
        self._attempts: dict[str, list[RecoveryAttempt]] = {}
        self._lock = threading.RLock()

    def list_attempts(self, task_id: str) -> list[RecoveryAttempt]:
        with self._lock:
            return [
                attempt.model_copy(deep=True)
                for attempt in self._attempts.get(task_id, [])
            ]

    def record_attempt(
        self,
        request: RecoveryAttemptRequest,
        policy: VGROPolicy,
    ) -> RecoveryAttempt:
        if request.previous_decision.value != request.attempt_type.value:
            raise ValueError("Attempt type must match the preceding VGRO decision.")
        if not request.triggering_reason_codes:
            raise ValueError("A recovery attempt requires at least one triggering reason.")

        with self._lock:
            attempts = self._attempts.setdefault(request.task_id, [])
            type_count = sum(
                attempt.attempt_type == request.attempt_type for attempt in attempts
            )
            budget = (
                policy.max_retry_attempts
                if request.attempt_type == VGROAttemptType.RETRY
                else policy.max_repair_attempts
            )
            if type_count >= budget:
                raise ValueError(
                    f"The {request.attempt_type.value.lower()} attempt budget is exhausted."
                )

            next_attempt_number = len(attempts) + 1
            retry_count = sum(
                attempt.attempt_type == VGROAttemptType.RETRY for attempt in attempts
            ) + (request.attempt_type == VGROAttemptType.RETRY)
            repair_count = sum(
                attempt.attempt_type == VGROAttemptType.REPAIR for attempt in attempts
            ) + (request.attempt_type == VGROAttemptType.REPAIR)

            attempt = RecoveryAttempt(
                task_id=request.task_id,
                attempt_number=next_attempt_number,
                attempt_type=request.attempt_type,
                previous_decision=request.previous_decision,
                triggering_reason_codes=request.triggering_reason_codes,
                result=request.result,
                remaining_retry_budget=max(0, policy.max_retry_attempts - retry_count),
                remaining_repair_budget=max(0, policy.max_repair_attempts - repair_count),
            )
            attempts.append(attempt)
            return attempt.model_copy(deep=True)
