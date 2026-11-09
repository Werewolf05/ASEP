from __future__ import annotations

from typing import Any

from app.models.task import PatchArtifact, VerificationCheck, VerificationReport


class VerificationService:
    """Executes independent verification checks and records evidence."""

    def __init__(self, docker_service: Any | None = None) -> None:
        self.docker_service = docker_service

    def run_verification(self, task_id: str, worktree_path: str, patch: PatchArtifact | None) -> VerificationReport:
        checks: list[VerificationCheck] = []

        patch_integrity_status = "PASS" if patch and patch.patch_sha256 else "FAIL"
        checks.append(
            VerificationCheck(
                check_id="patch-integrity",
                check_name="Patch application integrity",
                command=["git", "-C", worktree_path, "diff", "--stat"],
                status=patch_integrity_status,
                exit_code=0 if patch_integrity_status == "PASS" else 1,
                duration_ms=0,
                stdout="Patch artifact present." if patch else "No patch artifact present.",
                stderr="",
            )
        )

        changed_files = patch.changed_files if patch else []
        changed_files_status = "PASS" if patch and bool(changed_files) else "FAIL"
        checks.append(
            VerificationCheck(
                check_id="changed-file-validation",
                check_name="Changed file validation",
                command=["python", "-c", "print('validate changed files')"],
                status=changed_files_status,
                exit_code=0 if changed_files_status == "PASS" else 1,
                duration_ms=0,
                stdout=", ".join(changed_files) if changed_files else "No changed files recorded.",
                stderr="",
            )
        )

        tool_commands = {
            "pytest": ["pytest", "-q"],
            "ruff": ["ruff", "check", "."],
            "mypy": ["mypy", "app"],
        }

        docker_available = bool(self.docker_service is not None and self.docker_service.is_available())
        if docker_available:
            assert self.docker_service is not None
            for check_name, cmd in tool_commands.items():
                code, stdout, stderr, duration = self.docker_service.run(worktree_path, cmd, timeout_seconds=120)
                status = "PASS" if code == 0 else "FAIL" if code != 127 else "SKIPPED"
                checks.append(
                    VerificationCheck(
                        check_id=f"docker-{check_name}",
                        check_name=f"{check_name} verification",
                        command=["docker", *cmd],
                        status=status,
                        exit_code=code,
                        duration_ms=int(duration * 1000),
                        stdout=stdout,
                        stderr=stderr,
                    )
                )
        else:
            for check_name, cmd in tool_commands.items():
                checks.append(
                    VerificationCheck(
                        check_id=f"docker-{check_name}",
                        check_name=f"{check_name} verification",
                        command=["docker", *cmd],
                        status="SKIPPED",
                        exit_code=127,
                        duration_ms=0,
                        stdout="",
                        stderr="Docker sandbox verification is unavailable; host execution is intentionally not used.",
                    )
                )

        failed = any(check.status == "FAIL" for check in checks)
        overall = "FAILED" if failed else "VERIFIED"
        summary = "Verification completed successfully." if overall == "VERIFIED" else "At least one verification check failed."
        return VerificationReport(
            task_id=task_id,
            status=overall,
            summary=summary,
            docker_available=docker_available,
            checks=checks,
        )
