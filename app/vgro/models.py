from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.task import (
    EngineeringTask,
    PatchArtifact,
    VerificationReport,
    WorktreeRecord,
)


class VGRODecision(str, Enum):
    ACCEPT = "ACCEPT"
    RETRY = "RETRY"
    REPAIR = "REPAIR"
    OPTIMIZE = "OPTIMIZE"
    REJECT = "REJECT"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class VGROReasonCode(str, Enum):
    PATCH_INVALID = "PATCH_INVALID"
    PATCH_INTEGRITY_FAILED = "PATCH_INTEGRITY_FAILED"
    PROHIBITED_PATH_MODIFIED = "PROHIBITED_PATH_MODIFIED"
    SECURITY_POLICY_VIOLATION = "SECURITY_POLICY_VIOLATION"
    SECURITY_CHECK_FAILED = "SECURITY_CHECK_FAILED"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    EVIDENCE_TASK_MISMATCH = "EVIDENCE_TASK_MISMATCH"
    VERIFICATION_BLOCKED = "VERIFICATION_BLOCKED"
    VERIFICATION_INCOMPLETE = "VERIFICATION_INCOMPLETE"
    DOCKER_UNAVAILABLE = "DOCKER_UNAVAILABLE"
    IMAGE_UNAVAILABLE = "IMAGE_UNAVAILABLE"
    MANDATORY_CHECK_MISSING = "MANDATORY_CHECK_MISSING"
    MANDATORY_CHECK_SKIPPED = "MANDATORY_CHECK_SKIPPED"
    MANDATORY_CHECK_FAILED = "MANDATORY_CHECK_FAILED"
    CHECK_EXECUTION_EVIDENCE_INVALID = "CHECK_EXECUTION_EVIDENCE_INVALID"
    TASK_STATE_MISMATCH = "TASK_STATE_MISMATCH"
    FAILURE_CLASSIFICATION_UNKNOWN = "FAILURE_CLASSIFICATION_UNKNOWN"
    FAILURE_CLASSIFICATION_CONFLICT = "FAILURE_CLASSIFICATION_CONFLICT"
    NON_RECOVERABLE_FAILURE = "NON_RECOVERABLE_FAILURE"
    RECOVERABLE_FAILURE = "RECOVERABLE_FAILURE"
    TRANSIENT_INFRASTRUCTURE_FAILURE = "TRANSIENT_INFRASTRUCTURE_FAILURE"
    RETRY_BUDGET_EXHAUSTED = "RETRY_BUDGET_EXHAUSTED"
    REPAIR_BUDGET_EXHAUSTED = "REPAIR_BUDGET_EXHAUSTED"
    CORRECTNESS_GATE_PASSED = "CORRECTNESS_GATE_PASSED"
    OPTIMIZATION_ENABLED = "OPTIMIZATION_ENABLED"
    HUMAN_REVIEW_POLICY = "HUMAN_REVIEW_POLICY"


class VGROAttemptType(str, Enum):
    RETRY = "RETRY"
    REPAIR = "REPAIR"


class RecoveryAttemptStatus(str, Enum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class SecurityFailureBehavior(str, Enum):
    REJECT = "REJECT"


class VGROReason(BaseModel):
    code: VGROReasonCode
    explanation: str
    evidence_references: list[str] = Field(default_factory=list)


class VGROPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = "vgro-1.0"
    max_retry_attempts: int = Field(default=1, ge=0)
    max_repair_attempts: int = Field(default=1, ge=0)
    optimization_enabled: bool = False
    mandatory_check_names: tuple[str, ...] = (
        "changed-paths",
        "patch-integrity",
        "docker-pytest",
        "docker-ruff",
        "docker-mypy",
    )
    prohibited_paths: tuple[str, ...] = (
        ".env",
        ".env.*",
        "**/.env",
        "**/.env.*",
        "secrets/**",
        "**/secrets/**",
        "*.pem",
        "*.key",
        "id_rsa*",
        "id_ed25519*",
        "**/*.pem",
        "**/*.key",
        "**/id_rsa*",
        "**/id_ed25519*",
    )
    security_failure_behavior: SecurityFailureBehavior = SecurityFailureBehavior.REJECT
    human_review_conditions: tuple[str, ...] = (
        "unknown_failure_classification",
        "mixed_failure_classification",
        "task_state_mismatch",
    )


class VGROPolicyViolation(BaseModel):
    code: str
    explanation: str
    evidence_reference: str
    security_related: bool = True


class RecoveryAttempt(BaseModel):
    task_id: str
    attempt_number: int = Field(ge=1)
    attempt_type: VGROAttemptType
    previous_decision: VGRODecision
    triggering_reason_codes: list[VGROReasonCode]
    result: RecoveryAttemptStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    remaining_retry_budget: int = Field(ge=0)
    remaining_repair_budget: int = Field(ge=0)


class VGROEvidence(BaseModel):
    task_id: str
    task: EngineeringTask | None = None
    worktree: WorktreeRecord | None = None
    patch: PatchArtifact | None = None
    verification: VerificationReport | None = None
    recovery_attempts: list[RecoveryAttempt] = Field(default_factory=list)
    policy_violations: list[VGROPolicyViolation] = Field(default_factory=list)


class VGRODecisionResult(BaseModel):
    task_id: str
    decision: VGRODecision
    explanation: str
    reason_codes: list[VGROReasonCode]
    reasons: list[VGROReason]
    evidence_references: list[str]
    policy_version: str
    another_attempt_permitted: bool
    remaining_retry_budget: int
    remaining_repair_budget: int
    human_approval_required: bool


class RecoveryAttemptRequest(BaseModel):
    task_id: str
    attempt_type: VGROAttemptType
    previous_decision: VGRODecision
    triggering_reason_codes: list[VGROReasonCode]
    result: RecoveryAttemptStatus
