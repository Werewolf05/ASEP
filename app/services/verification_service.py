from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import time
from pathlib import Path

from app.models.task import (
    CheckStatus,
    PatchArtifact,
    ReportStatus,
    VerificationCheck,
    VerificationReport,
)
from app.repository.git_evidence import complete_git_diff
from app.repository.git_utils import run_git
from app.services.docker_service import DockerExecutionResult, DockerSandboxService


class VerificationService:
    """Collect actual Git evidence and run all repository checks only in Docker."""

    def __init__(self, docker_service: DockerSandboxService) -> None:
        self.docker_service = docker_service

    def run_verification(
        self,
        task_id: str,
        worktree_path: str,
        patch: PatchArtifact | None,
    ) -> VerificationReport:
        checks: list[VerificationCheck] = []
        worktree = Path(worktree_path).resolve(strict=True)
        docker_available = self.docker_service.is_available()
        image_available = docker_available and self.docker_service.image_available()

        paths_check = self._check_changed_paths(task_id, worktree, patch)
        checks.append(paths_check)
        if paths_check.status == CheckStatus.PASS:
            checks.append(self._check_patch_integrity(task_id, worktree, patch))
        else:
            checks.append(
                VerificationCheck(
                    check_id="patch-integrity",
                    check_name="Patch integrity",
                    command=[],
                    status=CheckStatus.SKIPPED,
                    mandatory=True,
                    stderr="Patch diff was not read because changed-path validation failed.",
                )
            )

        commands = {
            "pytest": ["pytest", "-q", "-p", "no:cacheprovider"],
            "ruff": ["ruff", "check", "."],
            "mypy": ["mypy", "--cache-dir", "/tmp/mypy-cache", "app"],
        }
        prerequisite_message = (
            "Docker daemon is unavailable."
            if not docker_available
            else f"Verification image '{self.docker_service.image}' is unavailable."
        )
        for check_name, command in commands.items():
            if not docker_available or not image_available:
                checks.append(
                    VerificationCheck(
                        check_id=f"docker-{check_name}",
                        check_name=f"{check_name} verification",
                        command=command,
                        status=CheckStatus.SKIPPED,
                        mandatory=True,
                        exit_code=None,
                        duration_ms=0,
                        stderr=prerequisite_message,
                    )
                )
                continue
            started = time.perf_counter()
            try:
                result: DockerExecutionResult = self.docker_service.run(
                    str(worktree), command, timeout_seconds=120
                )
                checks.append(
                    VerificationCheck(
                        check_id=f"docker-{check_name}",
                        check_name=f"{check_name} verification",
                        command=command,
                        status=result.status,
                        mandatory=True,
                        exit_code=result.exit_code,
                        duration_ms=result.duration_ms,
                        stdout=result.stdout,
                        stderr=result.stderr,
                    )
                )
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                subprocess.SubprocessError,
            ) as exc:
                checks.append(
                    VerificationCheck(
                        check_id=f"docker-{check_name}",
                        check_name=f"{check_name} verification",
                        command=command,
                        status=CheckStatus.ERROR,
                        mandatory=True,
                        exit_code=None,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        stderr=f"Sandbox execution failed: {exc}",
                    )
                )

        mandatory = [check for check in checks if check.mandatory]
        if any(check.status == CheckStatus.FAIL for check in mandatory):
            overall = ReportStatus.FAILED
            summary = "At least one executed mandatory check failed."
        elif not docker_available or not image_available:
            overall = ReportStatus.BLOCKED
            summary = prerequisite_message + " Repository execution checks did not run."
        elif any(
            check.status in {CheckStatus.ERROR, CheckStatus.SKIPPED}
            for check in mandatory
        ):
            overall = ReportStatus.INCOMPLETE
            summary = "Required verification evidence is incomplete."
        else:
            overall = ReportStatus.VERIFIED
            summary = "Every mandatory verification check executed and passed."

        return VerificationReport(
            task_id=task_id,
            status=overall,
            summary=summary,
            checks=checks,
            docker_available=docker_available,
            image_available=image_available,
        )

    def _check_patch_integrity(
        self,
        task_id: str,
        worktree: Path,
        patch: PatchArtifact | None,
    ) -> VerificationCheck:
        started = time.perf_counter()
        commands: list[list[str]] = []
        try:
            actual_diff = complete_git_diff(worktree, commands)
            duration = int((time.perf_counter() - started) * 1000)
            expected_hash = (
                hashlib.sha256(patch.diff.encode("utf-8")).hexdigest()
                if patch is not None and patch.task_id == task_id
                else ""
            )
            actual_hash = hashlib.sha256(actual_diff).hexdigest()
            passed = (
                patch is not None
                and patch.task_id == task_id
                and expected_hash == patch.patch_sha256
                and actual_hash == patch.patch_sha256
                and actual_diff.decode("utf-8", errors="strict") == patch.diff
            )
            return self._local_check(
                "patch-integrity",
                "Patch integrity",
                [
                    "git",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--binary",
                    "--find-renames",
                    "HEAD",
                ],
                CheckStatus.PASS if passed else CheckStatus.FAIL,
                0 if passed else 1,
                f"Recorded SHA-256: {expected_hash or 'missing'}\nActual SHA-256: {actual_hash}",
                duration,
                commands=commands,
            )
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            subprocess.SubprocessError,
        ) as exc:
            return self._local_check(
                "patch-integrity",
                "Patch integrity",
                [
                    "git",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--binary",
                    "--find-renames",
                    "HEAD",
                ],
                CheckStatus.ERROR,
                None,
                "",
                int((time.perf_counter() - started) * 1000),
                commands=commands,
                stderr=str(exc),
            )

    def _check_changed_paths(
        self,
        task_id: str,
        worktree: Path,
        patch: PatchArtifact | None,
    ) -> VerificationCheck:
        started = time.perf_counter()
        command = [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "-z",
        ]
        try:
            result = run_git(
                worktree,
                command[1:],
                text=False,
                timeout=15,
            )
            duration = int((time.perf_counter() - started) * 1000)
            if result.returncode != 0:
                return self._local_check(
                    "changed-paths",
                    "Changed-path validation",
                    command,
                    CheckStatus.ERROR,
                    result.returncode,
                    "",
                    duration,
                    stderr=self._as_text(result.stderr),
                )
            if not isinstance(result.stdout, bytes):
                    raise TypeError("Git status output was not returned as bytes.")
            actual_paths = self._parse_status_paths(result.stdout)
            unsafe = [path for path in actual_paths if not self._path_is_safe(worktree, path)]
            expected_paths = (
                set(patch.changed_files)
                if patch is not None and patch.task_id == task_id
                else set()
            )
            actual_hashes: dict[str, str] = {}
            hash_errors: list[str] = []
            if patch is not None and patch.task_id == task_id:
                for path in expected_paths:
                    try:
                        digest = self._safe_file_hash(worktree, path)
                    except (OSError, ValueError) as exc:
                        hash_errors.append(f"{path}: {exc}")
                    else:
                        if digest is not None:
                            actual_hashes[path] = digest
            hashes_match = (
                patch is not None
                and actual_hashes == patch.changed_file_hashes
            )
            passed = (
                not unsafe
                and not hash_errors
                and set(actual_paths) == expected_paths
                and hashes_match
            )
            details = (
                f"Actual paths: {sorted(actual_paths)}\n"
                f"Recorded paths: {sorted(expected_paths)}\n"
                f"Actual file hashes match recorded hashes: {hashes_match}"
            )
            if unsafe:
                details += f"\nUnsafe paths: {unsafe}"
            if hash_errors:
                details += f"\nFile hash errors: {hash_errors}"
            return self._local_check(
                "changed-paths",
                "Changed-path validation",
                command,
                CheckStatus.PASS if passed else CheckStatus.FAIL,
                0 if passed else 1,
                details,
                duration,
            )
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            subprocess.SubprocessError,
        ) as exc:
            return self._local_check(
                "changed-paths",
                "Changed-path validation",
                command,
                CheckStatus.ERROR,
                None,
                "",
                int((time.perf_counter() - started) * 1000),
                stderr=str(exc),
            )

    @staticmethod
    def _parse_status_paths(raw: bytes) -> list[str]:
        tokens = [token for token in raw.split(b"\0") if token]
        paths: list[str] = []
        index = 0
        while index < len(tokens):
            entry = tokens[index]
            index += 1
            if len(entry) < 4:
                continue
            status = entry[:2].decode(errors="replace")
            paths.append(entry[3:].decode(errors="surrogateescape"))
            if ("R" in status or "C" in status) and index < len(tokens):
                paths.append(tokens[index].decode(errors="surrogateescape"))
                index += 1
        return paths

    @staticmethod
    def _path_is_safe(root: Path, relative_path: str) -> bool:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            return False
        current = root
        for part in candidate.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                return False
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                return False
        try:
            current.resolve(strict=False).relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return False
        return True

    @staticmethod
    def _safe_file_hash(root: Path, relative_path: str) -> str | None:
        parts = Path(relative_path).parts
        if not parts or Path(relative_path).is_absolute() or ".." in parts:
            raise ValueError("Invalid changed-file path.")
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                try:
                    next_directory = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory,
                    )
                except FileNotFoundError:
                    return None
                os.close(directory)
                directory = next_directory
            try:
                descriptor = os.open(
                    parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
                )
            except FileNotFoundError:
                return None
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ValueError("Changed path is not a private regular file.")
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        finally:
            os.close(directory)

    @staticmethod
    def _as_text(value: str | bytes) -> str:
        return value if isinstance(value, str) else value.decode(errors="replace")

    @staticmethod
    def _local_check(
        check_id: str,
        name: str,
        command: list[str],
        status: CheckStatus,
        exit_code: int | None,
        output: str,
        duration_ms: int,
        *,
        commands: list[list[str]] | None = None,
        stderr: str = "",
    ) -> VerificationCheck:
        return VerificationCheck(
            check_id=check_id,
            check_name=name,
            command=command,
            status=status,
            commands=commands if commands is not None else ([command] if command else []),
            mandatory=True,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout=output,
            stderr=stderr,
        )
