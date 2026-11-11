from __future__ import annotations

import subprocess
from typing import Any

import pytest

from app.models.task import CheckStatus
from app.services.docker_service import DockerSandboxService


def _completed(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_missing_image_is_skipped_without_running_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    service = DockerSandboxService("missing:tag")
    monkeypatch.setattr(service, "is_available", lambda: True)
    monkeypatch.setattr(service, "image_available", lambda: False)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        "app.services.docker_service.subprocess.run",
        lambda args, **kwargs: commands.append(args),
    )

    result = service.run(str(tmp_path), ["pytest", "-q"])
    assert result.status == CheckStatus.SKIPPED
    assert not commands


def test_docker_timeout_removes_container(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    service = DockerSandboxService()
    monkeypatch.setattr(service, "is_available", lambda: True)
    monkeypatch.setattr(service, "image_available", lambda: True)
    commands: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        commands.append(args)
        if args[1] == "create":
            return _completed(args)
        if args[1] == "start":
            raise subprocess.TimeoutExpired(args, 1)
        return _completed(args)

    monkeypatch.setattr("app.services.docker_service.subprocess.run", fake_run)
    result = service.run(str(tmp_path), ["pytest", "-q"], timeout_seconds=1)

    assert result.status == CheckStatus.ERROR
    assert "timed out" in result.stderr
    assert any(command[:3] == ["docker", "rm", "--force"] for command in commands)


def test_container_is_removed_after_failed_command(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    service = DockerSandboxService()
    monkeypatch.setattr(service, "is_available", lambda: True)
    monkeypatch.setattr(service, "image_available", lambda: True)
    commands: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        commands.append(args)
        if args[1] == "start":
            return _completed(args, returncode=1, stderr="test failed")
        return _completed(args)

    monkeypatch.setattr("app.services.docker_service.subprocess.run", fake_run)
    result = service.run(str(tmp_path), ["pytest", "-q"])

    assert result.status == CheckStatus.FAIL
    assert result.exit_code == 1
    assert any(command[:3] == ["docker", "rm", "--force"] for command in commands)


def test_missing_tool_inside_image_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    service = DockerSandboxService()
    monkeypatch.setattr(service, "is_available", lambda: True)
    monkeypatch.setattr(service, "image_available", lambda: True)
    commands: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        commands.append(args)
        if args[1] == "create":
            return _completed(args, returncode=1, stderr='exec: "pytest": executable file not found')
        return _completed(args, returncode=1, stderr=f"Error: No such container: {args[-1]}")

    monkeypatch.setattr("app.services.docker_service.subprocess.run", fake_run)
    result = service.run(str(tmp_path), ["pytest", "-q"])

    assert result.status == CheckStatus.SKIPPED
    assert any(command[:3] == ["docker", "rm", "--force"] for command in commands)


def test_container_command_has_required_restrictions(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    service = DockerSandboxService()
    monkeypatch.setattr(service, "is_available", lambda: True)
    monkeypatch.setattr(service, "image_available", lambda: True)
    commands: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        commands.append(args)
        return _completed(args)

    monkeypatch.setattr("app.services.docker_service.subprocess.run", fake_run)
    service.run(str(tmp_path), ["pytest", "-q"])

    create = next(command for command in commands if command[1] == "create")
    assert "--network=none" in create
    assert "--read-only" in create
    assert "--cap-drop=ALL" in create
    assert "--pids-limit=128" in create
    assert "--cpus=1.0" in create
    assert "--memory=512m" in create
    assert "nofile=1024:1024" in create
    assert "--user" in create and create[create.index("--user") + 1] != "0:0"
    assert not any("docker.sock" in argument or "/home" in argument for argument in create)
    mount = create[create.index("--volume") + 1]
    assert mount.endswith(":/workspace:Z")
