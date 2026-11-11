from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.models.task import CheckStatus


@dataclass(frozen=True)
class DockerExecutionResult:
    status: CheckStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int


class DockerSandboxService:
    """Runs verification commands only in the prebuilt, restricted Docker image."""

    def __init__(self, image: str = "asep-verification:0.1.0") -> None:
        self.image = image

    def is_available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def image_available(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", self.image],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def run(
        self,
        worktree_path: str,
        command: list[str],
        timeout_seconds: int = 120,
    ) -> DockerExecutionResult:
        if not self.is_available():
            return DockerExecutionResult(
                CheckStatus.SKIPPED, None, "", "Docker daemon is unavailable.", 0
            )
        if not self.image_available():
            return DockerExecutionResult(
                CheckStatus.SKIPPED,
                None,
                "",
                f"Required verification image '{self.image}' is not available locally.",
                0,
            )

        worktree = Path(worktree_path).resolve(strict=True)
        if not worktree.is_dir():
            return DockerExecutionResult(
                CheckStatus.ERROR, None, "", "Task worktree is not a directory.", 0
            )
        if os.getuid() == 0:
            return DockerExecutionResult(
                CheckStatus.ERROR,
                None,
                "",
                "ASEP must be started by a non-root host user to preserve sandbox ownership.",
                0,
            )

        container_name = f"asep-verify-{uuid.uuid4().hex}"
        create_command = [
            "docker", "create",
            "--name", container_name,
            "--network=none",
            "--cpus=1.0",
            "--memory=512m",
            "--pids-limit=128",
            "--ulimit", "nofile=1024:1024",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--volume", f"{worktree}:/workspace:Z",
            "--workdir", "/workspace",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--env", "HOME=/tmp",
            "--env", "TMPDIR=/tmp",
            "--env", "RUFF_CACHE_DIR=/tmp/ruff-cache",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            self.image,
            *command,
        ]
        started = time.perf_counter()
        stdout = ""
        stderr = ""
        exit_code: int | None = None
        status = CheckStatus.ERROR
        try:
            created = subprocess.run(
                create_command,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if created.returncode != 0:
                stderr = created.stderr.strip() or created.stdout.strip()
                if (
                    "executable file not found" in stderr.lower()
                    or "no such image" in stderr.lower()
                    or "unable to find image" in stderr.lower()
                ):
                    status = CheckStatus.SKIPPED
                else:
                    status = CheckStatus.ERROR
                return DockerExecutionResult(
                    status, created.returncode, "", stderr, int((time.perf_counter() - started) * 1000)
                )
            result = subprocess.run(
                ["docker", "start", "--attach", container_name],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
            status = CheckStatus.PASS if exit_code == 0 else CheckStatus.FAIL
        except subprocess.TimeoutExpired as exc:
            stdout = self._decode_output(exc.stdout)
            stderr = self._decode_output(exc.stderr)
            stderr = f"{stderr}\nVerification command timed out after {timeout_seconds} seconds.".strip()
            status = CheckStatus.ERROR
        except OSError as exc:
            stderr = f"Unable to execute Docker CLI: {exc}"
            status = CheckStatus.ERROR
        finally:
            try:
                cleanup = subprocess.run(
                    ["docker", "rm", "--force", container_name],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                cleanup_error = cleanup.stderr.strip() or cleanup.stdout.strip()
                if cleanup.returncode != 0 and "no such container" not in cleanup_error.lower():
                    status = CheckStatus.ERROR
                    stderr = f"{stderr}\nContainer cleanup failed: {cleanup_error}".strip()
            except (OSError, subprocess.SubprocessError) as exc:
                status = CheckStatus.ERROR
                stderr = f"{stderr}\nContainer cleanup failed: {exc}".strip()

        return DockerExecutionResult(
            status,
            exit_code,
            stdout[:1_000_000],
            stderr[:1_000_000],
            int((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def _decode_output(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode(errors="replace") if isinstance(value, bytes) else value
