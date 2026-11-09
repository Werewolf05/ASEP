from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


class DockerSandboxService:
    """Executes verification commands in an isolated Docker sandbox when available."""

    def __init__(self, image: str = "python:3.11-slim") -> None:
        self.image = image

    def is_available(self) -> bool:
        docker_path = shutil.which("docker")
        if not docker_path:
            return False
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
        return result.returncode == 0

    def build_run_command(self, worktree_path: str, command: list[str], timeout_seconds: int = 120) -> list[str]:
        if not self.is_available():
            raise RuntimeError("Docker sandbox is unavailable in the current environment.")

        docker_root = Path(worktree_path).resolve(strict=True)
        return [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--cpus=1.0",
            "--memory=512m",
            "--pids-limit=128",
            "--ulimit",
            "nofile=1024:1024",
            "--read-only",
            "-v",
            f"{docker_root}:/workspace:rw",
            "--workdir",
            "/workspace",
            "--user",
            "1000:1000",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            self.image,
            *command,
        ]

    def run(self, worktree_path: str, command: list[str], timeout_seconds: int = 120) -> tuple[int, str, str, float]:
        if not self.is_available():
            return 127, "", "Docker sandbox unavailable. Verification did not run.", 0.0

        started = time.perf_counter()
        result = subprocess.run(
            self.build_run_command(worktree_path, command, timeout_seconds=timeout_seconds),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration = time.perf_counter() - started
        return result.returncode, result.stdout, result.stderr, duration
