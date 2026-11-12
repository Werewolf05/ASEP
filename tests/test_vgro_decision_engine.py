from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from app.models.task import (
    CheckStatus,
    EngineeringTask,
    FailureClassification,
    LifecycleStatus,
    PatchArtifact,
    ReportStatus,
    VerificationCheck,
    VerificationReport,
    WorktreeRecord,
)
from app.services.task_service import TaskService
from app.vgro import decision_engine
from app.vgro.decision_engine import VGRODecisionEngine
from app.vgro.models import (
    RecoveryAttempt,
    RecoveryAttemptRequest,
    RecoveryAttemptStatus,
    VGROAttemptType,
    VGRODecision,
    VGRODecisionResult,
    VGROEvidence,
    VGROPolicy,
    VGROPolicyViolation,
    VGROReasonCode,
)
from app.vgro.recovery_tracker import RecoveryAttemptTracker

TASK_ID = "task-" + "a" * 32
PATCH_ID = "patch-1"
CHECK_NAMES = {
    "changed-paths": "Changed-path validation",
    "patch-integrity": "Patch integrity",
    "docker-pytest": "pytest verification",
    "docker-ruff": "ruff verification",
    "docker-mypy": "mypy verification",
}


def build_evidence(
    *,
    task_id: str = TASK_ID,
    verification_status: ReportStatus = ReportStatus.VERIFIED,
    check_overrides: dict[
        str, tuple[CheckStatus, FailureClassification]
    ] | None = None,
    missing_checks: set[str] | None = None,
    changed_files: list[str] | None = None,
    patch_task_id: str | None = None,
    patch_id: str = PATCH_ID,
    patch_hash: str | None = None,
    docker_available: bool = True,
    image_available: bool = True,
    task_status: LifecycleStatus | None = None,
    recovery_attempts: list[RecoveryAttempt] | None = None,
) -> VGROEvidence:
    diff = "diff --git a/src/module.py b/src/module.py\n"
    patch = PatchArtifact(
        task_id=patch_task_id or task_id,
        patch_id=patch_id,
        patch_sha256=patch_hash or hashlib.sha256(diff.encode()).hexdigest(),
        patch_size=len(diff.encode()),
        diff=diff,
        changed_files=changed_files or ["src/module.py"],
    )
    checks: list[VerificationCheck] = []
    overrides = check_overrides or {}
    omitted = missing_checks or set()
    for check_id, name in CHECK_NAMES.items():
        if check_id in omitted:
            continue
        status_value, classification = overrides.get(
            check_id, (CheckStatus.PASS, FailureClassification.UNKNOWN)
        )
        checks.append(
            VerificationCheck(
                check_id=check_id,
                check_name=name,
                command=[check_id],
                status=status_value,
                failure_classification=classification,
                mandatory=True,
                exit_code=(
                    0
                    if status_value == CheckStatus.PASS
                    else 1
                    if status_value == CheckStatus.FAIL
                    else None
                ),
            )
        )
    report = VerificationReport(
        task_id=task_id,
        status=verification_status,
        summary="structured test evidence",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        checks=checks,
        docker_available=docker_available,
        image_available=image_available,
    )
    base_sha = "b" * 40
    status_value = task_status or (
        LifecycleStatus.VERIFIED
        if verification_status == ReportStatus.VERIFIED
        else LifecycleStatus.FAILED
        if verification_status == ReportStatus.FAILED
        else LifecycleStatus.INCOMPLETE
        if verification_status == ReportStatus.INCOMPLETE
        else LifecycleStatus.BLOCKED
    )
    task = EngineeringTask(
        task_id=task_id,
        title="Example",
        description="A sufficiently detailed example task.",
        repository_path="/private/path/repository",
        status=status_value,
        base_commit_sha=base_sha,
    )
    worktree = WorktreeRecord(
        task_id=task_id,
        original_repository_path="/private/path/repository",
        worktree_path="/private/path/worktrees/task",
        base_commit_sha=base_sha,
    )
    return VGROEvidence(
        task_id=task_id,
        task=task,
        worktree=worktree,
        patch=patch,
        verification=report,
        recovery_attempts=recovery_attempts or [],
    )


def evaluate(
    evidence: VGROEvidence,
    policy: VGROPolicy | None = None,
) -> VGRODecisionResult:
    return decision_engine.evaluate(evidence, policy or VGROPolicy())


def test_fully_verified_patch_is_accepted_with_evidence_references() -> None:
    evidence = build_evidence()

    result = evaluate(evidence)

    assert result.decision == VGRODecision.ACCEPT
    assert VGROReasonCode.CORRECTNESS_GATE_PASSED in result.reason_codes
    assert f"task:{TASK_ID}" in result.evidence_references
    assert f"patch:{PATCH_ID}" in result.evidence_references
    assert any(reference.startswith(f"verification:{TASK_ID}:") for reference in result.evidence_references)
    assert result.policy_version == "vgro-1.0"
    assert not result.human_approval_required


def test_verified_patch_can_be_eligible_for_optimization() -> None:
    result = evaluate(build_evidence(), VGROPolicy(optimization_enabled=True))

    assert result.decision == VGRODecision.OPTIMIZE
    assert result.reason_codes == [
        VGROReasonCode.CORRECTNESS_GATE_PASSED,
        VGROReasonCode.OPTIMIZATION_ENABLED,
    ]


def test_missing_verification_evidence_requires_human_review() -> None:
    evidence = build_evidence()
    evidence.verification = None

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.EVIDENCE_MISSING in result.reason_codes


def test_docker_unavailable_requires_human_review() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.BLOCKED,
        docker_available=False,
        image_available=False,
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.DOCKER_UNAVAILABLE in result.reason_codes
    assert f"verification:{TASK_ID}:2026-01-01T00:00:00+00:00" in result.evidence_references


def test_missing_verification_image_requires_human_review() -> None:
    evidence = build_evidence(image_available=False)

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.IMAGE_UNAVAILABLE in result.reason_codes


def test_missing_mandatory_check_requires_human_review() -> None:
    result = evaluate(build_evidence(missing_checks={"docker-mypy"}))

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.MANDATORY_CHECK_MISSING in result.reason_codes
    assert "docker-mypy" in result.explanation


def test_pass_status_without_command_exit_evidence_requires_review() -> None:
    evidence = build_evidence()
    check = next(
        check
        for check in evidence.verification.checks
        if check.check_id == "docker-pytest"
    )
    check.exit_code = None

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.CHECK_EXECUTION_EVIDENCE_INVALID in result.reason_codes
    assert any(":check:docker-pytest" in ref for ref in result.evidence_references)


def test_skipped_mandatory_check_requires_human_review() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.INCOMPLETE,
        check_overrides={"docker-ruff": (CheckStatus.SKIPPED, FailureClassification.UNKNOWN)},
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.MANDATORY_CHECK_SKIPPED in result.reason_codes


def test_mandatory_security_failure_is_rejected() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.FAILED,
        check_overrides={
            "docker-pytest": (CheckStatus.FAIL, FailureClassification.SECURITY),
        },
    )
    evidence.verification.checks.append(
        VerificationCheck(
            check_id="security-scan",
            check_name="Security scan",
            command=["semgrep"],
            status=CheckStatus.FAIL,
            failure_classification=FailureClassification.SECURITY,
            mandatory=True,
            exit_code=1,
        )
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.REJECT
    assert VGROReasonCode.SECURITY_CHECK_FAILED in result.reason_codes
    assert any(":check:security-scan" in ref for ref in result.evidence_references)


def test_mandatory_policy_violation_is_rejected_with_its_evidence_reference() -> None:
    evidence = build_evidence()
    evidence.policy_violations.append(
        VGROPolicyViolation(
            code="secret-disclosure",
            explanation="A configured security policy was violated.",
            evidence_reference="policy-check:secret-disclosure",
        )
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.REJECT
    assert VGROReasonCode.SECURITY_POLICY_VIOLATION in result.reason_codes
    assert "policy-check:secret-disclosure" in result.evidence_references


def test_patch_hash_mismatch_is_rejected() -> None:
    evidence = build_evidence(patch_hash="0" * 64)

    result = evaluate(evidence)

    assert result.decision == VGRODecision.REJECT
    assert VGROReasonCode.PATCH_INVALID in result.reason_codes
    assert f"patch:{PATCH_ID}" in result.evidence_references


def test_failed_patch_integrity_check_is_hard_rejected() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.FAILED,
        check_overrides={
            "patch-integrity": (CheckStatus.FAIL, FailureClassification.UNKNOWN),
        },
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.REJECT
    assert VGROReasonCode.PATCH_INTEGRITY_FAILED in result.reason_codes
    assert any(":check:patch-integrity" in ref for ref in result.evidence_references)


def test_prohibited_path_modification_is_rejected_case_insensitively() -> None:
    result = evaluate(build_evidence(changed_files=["Secrets/PRIVATE.PEM"]))

    assert result.decision == VGRODecision.REJECT
    assert VGROReasonCode.PROHIBITED_PATH_MODIFIED in result.reason_codes


def test_root_environment_file_is_prohibited() -> None:
    result = evaluate(build_evidence(changed_files=[".ENV.local"]))

    assert result.decision == VGRODecision.REJECT
    assert VGROReasonCode.PROHIBITED_PATH_MODIFIED in result.reason_codes


def test_recoverable_failure_with_budget_recommends_repair() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.FAILED,
        check_overrides={
            "docker-pytest": (
                CheckStatus.FAIL,
                FailureClassification.RECOVERABLE_CORRECTNESS,
            ),
        },
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.REPAIR
    assert result.another_attempt_permitted
    assert result.remaining_repair_budget == 1
    assert VGROReasonCode.RECOVERABLE_FAILURE in result.reason_codes
    assert any(":check:docker-pytest" in ref for ref in result.evidence_references)


def test_recoverable_failure_with_exhausted_budget_requires_review() -> None:
    attempts = [
        RecoveryAttempt(
            task_id=TASK_ID,
            attempt_number=1,
            attempt_type=VGROAttemptType.REPAIR,
            previous_decision=VGRODecision.REPAIR,
            triggering_reason_codes=[VGROReasonCode.RECOVERABLE_FAILURE],
            result=RecoveryAttemptStatus.FAILED,
            remaining_retry_budget=1,
            remaining_repair_budget=0,
        )
    ]
    evidence = build_evidence(
        verification_status=ReportStatus.FAILED,
        check_overrides={
            "docker-pytest": (
                CheckStatus.FAIL,
                FailureClassification.RECOVERABLE_CORRECTNESS,
            ),
        },
        recovery_attempts=attempts,
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert result.remaining_repair_budget == 0
    assert VGROReasonCode.REPAIR_BUDGET_EXHAUSTED in result.reason_codes
    assert f"attempt:{TASK_ID}:1" in result.evidence_references


def test_transient_infrastructure_failure_with_budget_recommends_retry() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.FAILED,
        check_overrides={
            "docker-pytest": (
                CheckStatus.FAIL,
                FailureClassification.TRANSIENT_INFRASTRUCTURE,
            ),
        },
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.RETRY
    assert result.another_attempt_permitted
    assert result.remaining_retry_budget == 1
    assert VGROReasonCode.TRANSIENT_INFRASTRUCTURE_FAILURE in result.reason_codes


def test_incomplete_execution_error_requires_human_review_even_if_transient() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.INCOMPLETE,
        check_overrides={
            "docker-pytest": (
                CheckStatus.ERROR,
                FailureClassification.TRANSIENT_INFRASTRUCTURE,
            ),
        },
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.VERIFICATION_INCOMPLETE in result.reason_codes
    assert not result.another_attempt_permitted


def test_exhausted_retry_budget_requires_review() -> None:
    attempts = [
        RecoveryAttempt(
            task_id=TASK_ID,
            attempt_number=1,
            attempt_type=VGROAttemptType.RETRY,
            previous_decision=VGRODecision.RETRY,
            triggering_reason_codes=[VGROReasonCode.TRANSIENT_INFRASTRUCTURE_FAILURE],
            result=RecoveryAttemptStatus.FAILED,
            remaining_retry_budget=0,
            remaining_repair_budget=1,
        )
    ]
    evidence = build_evidence(
        verification_status=ReportStatus.FAILED,
        check_overrides={
            "docker-pytest": (
                CheckStatus.FAIL,
                FailureClassification.TRANSIENT_INFRASTRUCTURE,
            ),
        },
        recovery_attempts=attempts,
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert result.remaining_retry_budget == 0
    assert VGROReasonCode.RETRY_BUDGET_EXHAUSTED in result.reason_codes
    assert f"attempt:{TASK_ID}:1" in result.evidence_references


def test_unknown_failure_classification_requires_human_review() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.FAILED,
        check_overrides={"docker-pytest": (CheckStatus.FAIL, FailureClassification.UNKNOWN)},
    )

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.FAILURE_CLASSIFICATION_UNKNOWN in result.reason_codes


def test_cross_task_patch_evidence_is_rejected() -> None:
    foreign_patch_id = "patch-from-another-task"
    result = evaluate(
        build_evidence(
            patch_task_id="task-" + "c" * 32,
            patch_id=foreign_patch_id,
        )
    )

    assert result.decision == VGRODecision.REJECT
    assert VGROReasonCode.EVIDENCE_TASK_MISMATCH in result.reason_codes
    assert f"patch:{foreign_patch_id}" not in result.evidence_references


def test_task_store_rejects_a_decision_for_another_task() -> None:
    task_service = TaskService()
    stored_task = task_service.create_task(
        "Different task",
        "This task must not receive another task's decision.",
        "/tmp/repository",
    )
    decision = evaluate(build_evidence())

    with pytest.raises(ValueError, match="different task"):
        task_service.store_vgro_decision(stored_task.task_id, decision)


def test_task_and_verification_state_mismatch_requires_human_review() -> None:
    evidence = build_evidence(task_status=LifecycleStatus.FAILED)

    result = evaluate(evidence)

    assert result.decision == VGRODecision.HUMAN_REVIEW
    assert VGROReasonCode.TASK_STATE_MISMATCH in result.reason_codes


def test_optimization_never_bypasses_failed_correctness_checks() -> None:
    evidence = build_evidence(
        verification_status=ReportStatus.FAILED,
        check_overrides={
            "docker-pytest": (
                CheckStatus.FAIL,
                FailureClassification.RECOVERABLE_CORRECTNESS,
            ),
        },
    )

    result = evaluate(
        evidence,
        VGROPolicy(optimization_enabled=True),
    )

    assert result.decision == VGRODecision.REPAIR
    assert result.decision != VGRODecision.OPTIMIZE


def test_decision_is_reproducible_for_identical_evidence() -> None:
    evidence = build_evidence()
    engine = VGRODecisionEngine()
    policy = VGROPolicy()

    assert engine.evaluate(evidence, policy) == engine.evaluate(evidence, policy)


def test_recovery_attempt_tracker_records_and_enforces_budgets() -> None:
    tracker = RecoveryAttemptTracker()
    policy = VGROPolicy(max_repair_attempts=1)
    request = RecoveryAttemptRequest(
        task_id=TASK_ID,
        attempt_type=VGROAttemptType.REPAIR,
        previous_decision=VGRODecision.REPAIR,
        triggering_reason_codes=[VGROReasonCode.RECOVERABLE_FAILURE],
        result=RecoveryAttemptStatus.STARTED,
    )

    recorded = tracker.record_attempt(request, policy)

    assert recorded.attempt_number == 1
    assert recorded.remaining_repair_budget == 0
    assert tracker.list_attempts(TASK_ID) == [recorded]
    with pytest.raises(ValueError, match="budget is exhausted"):
        tracker.record_attempt(request, policy)


def test_recovery_attempt_tracker_rejects_mismatched_attempt_type() -> None:
    tracker = RecoveryAttemptTracker()
    request = RecoveryAttemptRequest(
        task_id=TASK_ID,
        attempt_type=VGROAttemptType.RETRY,
        previous_decision=VGRODecision.REPAIR,
        triggering_reason_codes=[VGROReasonCode.RECOVERABLE_FAILURE],
        result=RecoveryAttemptStatus.STARTED,
    )

    with pytest.raises(ValueError, match="must match"):
        tracker.record_attempt(request, VGROPolicy())
