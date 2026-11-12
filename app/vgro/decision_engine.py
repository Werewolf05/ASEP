from __future__ import annotations

import fnmatch
import hashlib

from app.models.task import (
    CheckStatus,
    FailureClassification,
    LifecycleStatus,
    ReportStatus,
    VerificationCheck,
)
from app.vgro.models import (
    VGRODecision,
    VGRODecisionResult,
    VGROEvidence,
    VGROPolicy,
    VGROReason,
    VGROReasonCode,
)


class VGRODecisionEngine:
    """Evaluate structured evidence without depending on API or execution services."""

    def evaluate(self, evidence: VGROEvidence, policy: VGROPolicy) -> VGRODecisionResult:
        retry_used = 0
        repair_used = 0
        for attempt in evidence.recovery_attempts:
            if attempt.task_id != evidence.task_id:
                return self._result(
                    VGRODecision.HUMAN_REVIEW,
                    policy,
                    evidence,
                    self._reason(
                        VGROReasonCode.EVIDENCE_TASK_MISMATCH,
                        "A recovery attempt is associated with a different task.",
                        self._task_ref(evidence.task_id),
                    ),
                    retry_used,
                    repair_used,
                )
            if attempt.attempt_type.value == "RETRY":
                retry_used += 1
            else:
                repair_used += 1

        if evidence.task is not None and evidence.task.task_id != evidence.task_id:
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.EVIDENCE_TASK_MISMATCH,
                    "Task evidence does not belong to the requested task.",
                    self._task_ref(evidence.task_id),
                ),
                retry_used,
                repair_used,
            )

        if evidence.policy_violations:
            violation = evidence.policy_violations[0]
            code = (
                VGROReasonCode.SECURITY_POLICY_VIOLATION
                if violation.security_related
                else VGROReasonCode.POLICY_VIOLATION
            )
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(code, violation.explanation, violation.evidence_reference),
                retry_used,
                repair_used,
            )

        if evidence.task is None:
            return self._missing(
                policy, evidence, retry_used, repair_used, "Task evidence is missing."
            )
        if evidence.worktree is None or evidence.patch is None or evidence.verification is None:
            missing = [
                name
                for name, value in (
                    ("worktree", evidence.worktree),
                    ("patch", evidence.patch),
                    ("verification report", evidence.verification),
                )
                if value is None
            ]
            return self._missing(
                policy,
                evidence,
                retry_used,
                repair_used,
                "Required evidence is missing: " + ", ".join(missing) + ".",
            )
        if not evidence.task.base_commit_sha or not evidence.worktree.base_commit_sha:
            return self._missing(
                policy,
                evidence,
                retry_used,
                repair_used,
                "The task base commit reference is missing.",
            )

        task = evidence.task
        worktree = evidence.worktree
        patch = evidence.patch
        report = evidence.verification
        if (
            worktree.task_id != evidence.task_id
            or patch.task_id != evidence.task_id
            or report.task_id != evidence.task_id
            or worktree.base_commit_sha != task.base_commit_sha
            or patch.task_id != task.task_id
        ):
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.EVIDENCE_TASK_MISMATCH,
                    "Task, worktree, patch, or verification references do not match.",
                    self._task_ref(evidence.task_id),
                    self._verification_ref(evidence),
                ),
                retry_used,
                repair_used,
            )

        actual_patch_hash = hashlib.sha256(patch.diff.encode("utf-8")).hexdigest()
        if actual_patch_hash != patch.patch_sha256 or len(patch.diff.encode("utf-8")) != patch.patch_size:
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.PATCH_INVALID,
                    "Patch content does not match its recorded size and SHA-256.",
                    self._patch_ref(patch.patch_id),
                ),
                retry_used,
                repair_used,
            )

        prohibited = self._prohibited_paths(evidence, policy)
        if prohibited:
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.PROHIBITED_PATH_MODIFIED,
                    "The patch modifies a path prohibited by policy: "
                    + ", ".join(sorted(prohibited)),
                    self._patch_ref(patch.patch_id),
                ),
                retry_used,
                repair_used,
            )

        checks_by_name = self._checks_by_name(report.checks)
        security_failures = [
            check
            for check in report.checks
            if check.mandatory
            and check.status == CheckStatus.FAIL
            and self._is_security_check(check)
        ]
        if security_failures:
            check_refs = [
                self._check_ref(evidence, check.check_id) for check in security_failures
            ]
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.SECURITY_CHECK_FAILED,
                    "A mandatory security verification check failed.",
                    *check_refs,
                ),
                retry_used,
                repair_used,
            )

        integrity_failures = [
            check
            for check in report.checks
            if self._normalize_name(check.check_id) == "patch-integrity"
            and check.status == CheckStatus.FAIL
        ]
        if integrity_failures:
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.PATCH_INTEGRITY_FAILED,
                    "Independent patch-integrity verification failed.",
                    *(
                        self._check_ref(evidence, check.check_id)
                        for check in integrity_failures
                    ),
                ),
                retry_used,
                repair_used,
            )

        if not report.docker_available:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.DOCKER_UNAVAILABLE,
                    "Docker availability was not established for this verification report.",
                    self._verification_ref(evidence),
                ),
                retry_used,
                repair_used,
            )
        if not report.image_available:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.IMAGE_UNAVAILABLE,
                    "The required verification image was unavailable.",
                    self._verification_ref(evidence),
                ),
                retry_used,
                repair_used,
            )
        if report.status == ReportStatus.BLOCKED:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.VERIFICATION_BLOCKED,
                    "Verification was blocked and cannot establish correctness.",
                    self._verification_ref(evidence),
                ),
                retry_used,
                repair_used,
            )
        required_checks: list[VerificationCheck] = []
        missing_checks: list[str] = []
        for required_name in policy.mandatory_check_names:
            matches = checks_by_name.get(self._normalize_name(required_name), [])
            if not matches:
                missing_checks.append(required_name)
            else:
                required_checks.extend(matches)
        if missing_checks:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.MANDATORY_CHECK_MISSING,
                    "Mandatory verification evidence is missing: "
                    + ", ".join(missing_checks)
                    + ".",
                    self._verification_ref(evidence),
                ),
                retry_used,
                repair_used,
            )

        all_mandatory_checks = [
            check
            for check in report.checks
            if check.mandatory
            or self._normalize_name(check.check_id)
            in {self._normalize_name(name) for name in policy.mandatory_check_names}
            or self._normalize_name(check.check_name)
            in {self._normalize_name(name) for name in policy.mandatory_check_names}
        ]
        skipped = [
            check
            for check in all_mandatory_checks
            if check.status == CheckStatus.SKIPPED
        ]
        if skipped:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.MANDATORY_CHECK_SKIPPED,
                    "A mandatory verification check was skipped.",
                    *(self._check_ref(evidence, check.check_id) for check in skipped),
                ),
                retry_used,
                repair_used,
            )

        if report.status == ReportStatus.INCOMPLETE:
            if task.status != LifecycleStatus.INCOMPLETE:
                return self._result(
                    VGRODecision.HUMAN_REVIEW,
                    policy,
                    evidence,
                    self._reason(
                        VGROReasonCode.TASK_STATE_MISMATCH,
                        "Task lifecycle state does not agree with the verification report.",
                        self._task_ref(evidence.task_id),
                        self._verification_ref(evidence),
                    ),
                    retry_used,
                    repair_used,
                )
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.VERIFICATION_INCOMPLETE,
                    "Verification evidence is incomplete.",
                    self._verification_ref(evidence),
                ),
                retry_used,
                repair_used,
            )

        if report.status == ReportStatus.VERIFIED:
            expected_task_status = LifecycleStatus.VERIFIED
        else:
            expected_task_status = LifecycleStatus.FAILED
        if task.status != expected_task_status:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.TASK_STATE_MISMATCH,
                    "Task lifecycle state does not agree with the verification report.",
                    self._task_ref(evidence.task_id),
                    self._verification_ref(evidence),
                ),
                retry_used,
                repair_used,
            )

        invalid_execution_evidence = [
            check
            for check in all_mandatory_checks
            if (
                check.status == CheckStatus.PASS
                and (
                    check.exit_code != 0
                    or not (check.command or check.commands)
                )
            )
            or (
                check.status == CheckStatus.FAIL
                and (
                    check.exit_code is None
                    or check.exit_code == 0
                    or not (check.command or check.commands)
                )
            )
        ]
        if invalid_execution_evidence:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.CHECK_EXECUTION_EVIDENCE_INVALID,
                    "A mandatory check status is not backed by a matching command and exit code.",
                    *(
                        self._check_ref(evidence, check.check_id)
                        for check in invalid_execution_evidence
                    ),
                ),
                retry_used,
                repair_used,
            )

        errors = [
            check
            for check in all_mandatory_checks
            if check.status == CheckStatus.ERROR
        ]
        if errors:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.VERIFICATION_INCOMPLETE,
                    "A mandatory verification check ended with an execution error.",
                    *(self._check_ref(evidence, check.check_id) for check in errors),
                ),
                retry_used,
                repair_used,
            )

        failed = [
            check for check in all_mandatory_checks if check.status == CheckStatus.FAIL
        ]
        if failed:
            if report.status != ReportStatus.FAILED:
                return self._result(
                    VGRODecision.HUMAN_REVIEW,
                    policy,
                    evidence,
                    self._reason(
                        VGROReasonCode.VERIFICATION_INCOMPLETE,
                        "Failed check evidence conflicts with the overall verification status.",
                        self._verification_ref(evidence),
                        *(self._check_ref(evidence, check.check_id) for check in failed),
                    ),
                    retry_used,
                    repair_used,
                )
            return self._decide_failure(
                failed, evidence, policy, retry_used, repair_used
            )

        if report.status != ReportStatus.VERIFIED or any(
            check.status != CheckStatus.PASS for check in all_mandatory_checks
        ):
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.VERIFICATION_INCOMPLETE,
                    "Verification did not prove that every mandatory check passed.",
                    self._verification_ref(evidence),
                ),
                retry_used,
                repair_used,
            )

        integrity_checks = checks_by_name.get(self._normalize_name("patch-integrity"), [])
        if not integrity_checks or any(
            check.status != CheckStatus.PASS for check in integrity_checks
        ):
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.PATCH_INTEGRITY_FAILED,
                    "Independent patch-integrity verification did not pass.",
                    self._verification_ref(evidence),
                    *(self._check_ref(evidence, check.check_id) for check in integrity_checks),
                ),
                retry_used,
                repair_used,
            )

        gate_reason = self._reason(
            VGROReasonCode.CORRECTNESS_GATE_PASSED,
            "All required evidence and mandatory correctness checks passed.",
            self._task_ref(evidence.task_id),
            self._patch_ref(patch.patch_id),
            self._verification_ref(evidence),
        )
        if policy.optimization_enabled:
            if "optimization_requires_human_approval" in policy.human_review_conditions:
                return self._policy_review(
                    evidence,
                    policy,
                    retry_used,
                    repair_used,
                    [self._verification_ref(evidence)],
                )
            return self._result(
                VGRODecision.OPTIMIZE,
                policy,
                evidence,
                gate_reason,
                retry_used,
                repair_used,
                extra_reasons=[
                    self._reason(
                        VGROReasonCode.OPTIMIZATION_ENABLED,
                        "Optimization is eligible only after the correctness gate.",
                        self._verification_ref(evidence),
                    )
                ],
            )
        return self._result(
            VGRODecision.ACCEPT,
            policy,
            evidence,
            gate_reason,
            retry_used,
            repair_used,
        )

    def _policy_review(
        self,
        evidence: VGROEvidence,
        policy: VGROPolicy,
        retry_used: int,
        repair_used: int,
        references: list[str],
    ) -> VGRODecisionResult:
        return self._result(
            VGRODecision.HUMAN_REVIEW,
            policy,
            evidence,
            self._reason(
                VGROReasonCode.HUMAN_REVIEW_POLICY,
                "The active policy requires human review for this condition.",
                *references,
            ),
            retry_used,
            repair_used,
        )

    def _decide_failure(
        self,
        failed: list[VerificationCheck],
        evidence: VGROEvidence,
        policy: VGROPolicy,
        retry_used: int,
        repair_used: int,
    ) -> VGRODecisionResult:
        classifications = {check.failure_classification for check in failed}
        references = [
            self._check_ref(evidence, check.check_id) for check in failed
        ]
        if FailureClassification.SECURITY in classifications:
            code = (
                VGROReasonCode.SECURITY_POLICY_VIOLATION
                if policy.security_failure_behavior.value == "REJECT"
                else VGROReasonCode.SECURITY_CHECK_FAILED
            )
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    code,
                    "A mandatory check classified its failure as security-related.",
                    *references,
                ),
                retry_used,
                repair_used,
            )
        if FailureClassification.NON_RECOVERABLE in classifications:
            return self._result(
                VGRODecision.REJECT,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.NON_RECOVERABLE_FAILURE,
                    "A mandatory check classified its failure as non-recoverable.",
                    *references,
                ),
                retry_used,
                repair_used,
            )
        if FailureClassification.UNKNOWN in classifications:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.FAILURE_CLASSIFICATION_UNKNOWN,
                    "Failure evidence is not explicitly classified as recoverable or transient.",
                    *references,
                ),
                retry_used,
                repair_used,
            )
        if len(classifications) != 1:
            return self._result(
                VGRODecision.HUMAN_REVIEW,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.FAILURE_CLASSIFICATION_CONFLICT,
                    "Mandatory failures have mixed classifications; automated recovery is unsafe.",
                    *references,
                ),
                retry_used,
                repair_used,
            )

        classification = next(iter(classifications))
        if classification == FailureClassification.TRANSIENT_INFRASTRUCTURE:
            if "transient_failures_require_review" in policy.human_review_conditions:
                return self._policy_review(
                    evidence, policy, retry_used, repair_used, references
                )
            remaining = max(0, policy.max_retry_attempts - retry_used)
            if remaining == 0:
                return self._result(
                    VGRODecision.HUMAN_REVIEW,
                    policy,
                    evidence,
                    self._reason(
                        VGROReasonCode.RETRY_BUDGET_EXHAUSTED,
                        "The retry budget is exhausted.",
                        *references,
                    ),
                    retry_used,
                    repair_used,
                )
            return self._result(
                VGRODecision.RETRY,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.TRANSIENT_INFRASTRUCTURE_FAILURE,
                    "The mandatory failure is explicitly classified as transient infrastructure.",
                    *references,
                ),
                retry_used,
                repair_used,
                another_attempt_permitted=True,
            )

        if classification == FailureClassification.RECOVERABLE_CORRECTNESS:
            if "recoverable_failures_require_review" in policy.human_review_conditions:
                return self._policy_review(
                    evidence, policy, retry_used, repair_used, references
                )
            remaining = max(0, policy.max_repair_attempts - repair_used)
            if remaining == 0:
                return self._result(
                    VGRODecision.HUMAN_REVIEW,
                    policy,
                    evidence,
                    self._reason(
                        VGROReasonCode.REPAIR_BUDGET_EXHAUSTED,
                        "The repair budget is exhausted.",
                        *references,
                    ),
                    retry_used,
                    repair_used,
                )
            return self._result(
                VGRODecision.REPAIR,
                policy,
                evidence,
                self._reason(
                    VGROReasonCode.RECOVERABLE_FAILURE,
                    "The mandatory failure is explicitly classified as recoverable correctness work.",
                    *references,
                ),
                retry_used,
                repair_used,
                another_attempt_permitted=True,
            )

        return self._result(
            VGRODecision.HUMAN_REVIEW,
            policy,
            evidence,
            self._reason(
                VGROReasonCode.FAILURE_CLASSIFICATION_UNKNOWN,
                "Failure evidence does not match a supported recovery classification.",
                *references,
            ),
            retry_used,
            repair_used,
        )

    def _missing(
        self,
        policy: VGROPolicy,
        evidence: VGROEvidence,
        retry_used: int,
        repair_used: int,
        explanation: str,
    ) -> VGRODecisionResult:
        return self._result(
            VGRODecision.HUMAN_REVIEW,
            policy,
            evidence,
            self._reason(
                VGROReasonCode.EVIDENCE_MISSING,
                explanation,
                self._task_ref(evidence.task_id),
            ),
            retry_used,
            repair_used,
        )

    def _result(
        self,
        decision: VGRODecision,
        policy: VGROPolicy,
        evidence: VGROEvidence,
        reason: VGROReason,
        retry_used: int,
        repair_used: int,
        *,
        another_attempt_permitted: bool = False,
        extra_reasons: list[VGROReason] | None = None,
    ) -> VGRODecisionResult:
        reasons = [reason, *(extra_reasons or [])]
        reason_codes = list(dict.fromkeys(item.code for item in reasons))
        references = list(
            dict.fromkeys(
                [
                    self._task_ref(evidence.task_id),
                    *(
                        [self._patch_ref(evidence.patch.patch_id)]
                        if evidence.patch is not None
                        and evidence.patch.task_id == evidence.task_id
                        else []
                    ),
                    *(
                        [self._verification_ref(evidence)]
                        if evidence.verification is not None
                        else []
                    ),
                    *(
                        [self._worktree_ref(evidence.task_id)]
                        if evidence.worktree is not None
                        else []
                    ),
                    *(
                        self._attempt_ref(attempt.task_id, attempt.attempt_number)
                        for attempt in evidence.recovery_attempts
                        if attempt.task_id == evidence.task_id
                    ),
                    *(ref for item in reasons for ref in item.evidence_references),
                ]
            )
        )
        return VGRODecisionResult(
            task_id=evidence.task_id,
            decision=decision,
            explanation=" ".join(item.explanation for item in reasons),
            reason_codes=reason_codes,
            reasons=reasons,
            evidence_references=references,
            policy_version=policy.version,
            another_attempt_permitted=another_attempt_permitted,
            remaining_retry_budget=max(0, policy.max_retry_attempts - retry_used),
            remaining_repair_budget=max(0, policy.max_repair_attempts - repair_used),
            human_approval_required=decision == VGRODecision.HUMAN_REVIEW,
        )

    @staticmethod
    def _checks_by_name(
        checks: list[VerificationCheck],
    ) -> dict[str, list[VerificationCheck]]:
        indexed: dict[str, list[VerificationCheck]] = {}
        for check in checks:
            for name in (check.check_id, check.check_name):
                indexed.setdefault(VGRODecisionEngine._normalize_name(name), []).append(check)
        return indexed

    @staticmethod
    def _prohibited_paths(evidence: VGROEvidence, policy: VGROPolicy) -> set[str]:
        paths = set(evidence.patch.changed_files if evidence.patch else [])
        if evidence.patch:
            for change in evidence.patch.file_changes:
                paths.add(change.path)
                if change.previous_path:
                    paths.add(change.previous_path)
        matches: set[str] = set()
        for path in paths:
            normalized = path.replace("\\", "/").casefold()
            while normalized.startswith("./"):
                normalized = normalized[2:]
            for pattern in policy.prohibited_paths:
                normalized_pattern = pattern.replace("\\", "/").casefold()
                if fnmatch.fnmatchcase(normalized, normalized_pattern):
                    matches.add(path)
                    break
        return matches

    @staticmethod
    def _is_security_check(check: VerificationCheck) -> bool:
        return (
            check.failure_classification == FailureClassification.SECURITY
            or "security" in check.check_id.casefold()
            or "security" in check.check_name.casefold()
        )

    @staticmethod
    def _normalize_name(name: str) -> str:
        return name.strip().casefold().replace("_", "-")

    @staticmethod
    def _task_ref(task_id: str) -> str:
        return f"task:{task_id}"

    @staticmethod
    def _patch_ref(patch_id: str) -> str:
        return f"patch:{patch_id}"

    @staticmethod
    def _worktree_ref(task_id: str) -> str:
        return f"worktree:{task_id}"

    @staticmethod
    def _attempt_ref(task_id: str, attempt_number: int) -> str:
        return f"attempt:{task_id}:{attempt_number}"

    @staticmethod
    def _verification_ref(evidence: VGROEvidence) -> str:
        if evidence.verification is None:
            return f"verification:{evidence.task_id}:missing"
        timestamp = evidence.verification.generated_at.isoformat()
        return f"verification:{evidence.task_id}:{timestamp}"

    @classmethod
    def _check_ref(cls, evidence: VGROEvidence, check_id: str) -> str:
        return f"{cls._verification_ref(evidence)}:check:{check_id}"

    @staticmethod
    def _reason(
        code: VGROReasonCode,
        explanation: str,
        *evidence_references: str,
    ) -> VGROReason:
        return VGROReason(
            code=code,
            explanation=explanation,
            evidence_references=list(evidence_references),
        )


def evaluate(evidence: VGROEvidence, policy: VGROPolicy) -> VGRODecisionResult:
    """Evaluate one evidence snapshot with an explicit policy."""
    return VGRODecisionEngine().evaluate(evidence, policy)
