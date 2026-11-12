from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    response_payload = response.json()
    assert response_payload["status"] == "ok"
    assert response_payload["service"] == "ASEP"


def test_issue_submission_endpoint() -> None:
    response = client.post(
        "/api/v1/issues",
        json={
            "title": "Fix login validation",
            "description": "The login form accepts empty usernames. Add validation and a regression test.",
            "tags": ["bug", "auth"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["issue"]["title"] == "Fix login validation"
    assert payload["issue"]["description"].startswith("The login form accepts empty usernames")
    assert payload["issue"]["tags"] == ["bug", "auth"]


def test_repository_inspection_endpoint(tmp_path) -> None:
    repo_path = tmp_path / "demo-repo"
    repo_path.mkdir()
    import subprocess

    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    (repo_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (repo_path / "tests").mkdir()
    (repo_path / "tests" / "test_main.py").write_text("def test_true():\n    assert True\n", encoding="utf-8")

    response = client.post(
        "/api/v1/repositories/inspect",
        json={"repository_path": str(repo_path)},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["repository"]["repository_name"] == "demo-repo"
    assert payload["repository"]["git_initialized"] is True
    assert payload["repository"]["test_files"]


def test_planning_endpoint(tmp_path) -> None:
    repo_path = tmp_path / "demo-repo"
    repo_path.mkdir()
    import subprocess

    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    (repo_path / "service.py").write_text("value = 1\n", encoding="utf-8")

    response = client.post(
        "/api/v1/agent/plan",
        json={
            "title": "Fix service bug",
            "description": "The service should reject invalid input and include a regression check.",
            "repository_path": str(repo_path),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan"]["provider"] == "mock-ai-v1"
    assert payload["plan"]["status"] == "ready"
    assert payload["repository"]["repository_name"] == "demo-repo"


def test_patch_and_evidence_endpoints_return_stored_artifacts(tmp_path, monkeypatch) -> None:
    import subprocess

    repo_path = tmp_path / "patch-repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "user.name", "API Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "api@example.com"],
        check=True,
    )
    (repo_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )

    response = client.post(
        "/api/v1/agent/patch",
        json={
            "title": "Add helper",
            "description": "Add a simple helper to the project.",
            "repository_path": str(repo_path),
        },
    )
    assert response.status_code == 200
    result = response.json()
    task_id = result["task_id"]
    assert result["patch"]["patch_sha256"]
    assert str(repo_path) not in str(result["task"])

    evidence = client.get(f"/api/v1/tasks/{task_id}/evidence")
    assert evidence.status_code == 200
    evidence_payload = evidence.json()
    assert evidence_payload["patch"]["task_id"] == task_id
    assert evidence_payload["worktree"]["task_id"] == task_id
    assert evidence_payload["verification"] is None
    assert str(repo_path) not in str(evidence_payload["task"])

    from app.models.task import ReportStatus
    from app.services.verification_service import VerificationService

    class DockerUnavailable:
        image = "missing-test-image"

        def is_available(self) -> bool:
            return False

        def image_available(self) -> bool:
            return False

    services = app.state.services
    monkeypatch.setattr(
        services,
        "verification_service",
        VerificationService(DockerUnavailable()),
    )
    verification = client.post(
        "/api/v1/verification/run",
        json={"task_id": task_id},
    )
    assert verification.status_code == 200
    assert verification.json()["status"] == ReportStatus.BLOCKED.value
    evidence = client.get(f"/api/v1/tasks/{task_id}/evidence")
    assert evidence.json()["verification"]["status"] == ReportStatus.BLOCKED.value
    assert evidence.json()["task"]["status"] == ReportStatus.BLOCKED.value


def test_rejected_patch_leaves_task_rejected(tmp_path) -> None:
    import subprocess

    repo_path = tmp_path / "rejected-repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.name", "API Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "api@example.com"],
        check=True,
    )
    (repo_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )

    response = client.post(
        "/api/v1/agent/patch",
        json={
            "title": "Reject unsafe patch",
            "description": "This request tests a rejected patch proposal.",
            "repository_path": str(repo_path),
        },
    )
    assert response.status_code == 422
    task_id = response.json()["detail"]["task_id"]
    task = client.get(f"/api/v1/tasks/{task_id}")
    assert task.status_code == 200
    assert task.json()["status"] == "REJECTED"
    assert str(repo_path) not in str(task.json())
    record = app.state.services.task_service.get_worktree(task_id)
    assert app.state.services.worktree_manager.cleanup_worktree(record) is True


def test_task_endpoints_validate_task_ids() -> None:
    assert client.get("/api/v1/tasks/not-a-task-id").status_code == 422
    assert client.get("/api/v1/tasks/not-a-task-id/evidence").status_code == 422


def test_vgro_evaluation_and_latest_decision_endpoints() -> None:
    import hashlib

    from app.models.task import (
        CheckStatus,
        LifecycleStatus,
        PatchArtifact,
        ReportStatus,
        VerificationCheck,
        VerificationReport,
        WorktreeRecord,
    )

    services = app.state.services
    task = services.task_service.create_task(
        "VGRO API decision",
        "Verify that VGRO exposes a stored decision without host paths.",
        "/private/repositories/vgro-api-test",
    )
    base_sha = "d" * 40
    worktree = WorktreeRecord(
        task_id=task.task_id,
        original_repository_path=task.repository_path,
        worktree_path="/private/worktrees/vgro-api-test",
        base_commit_sha=base_sha,
    )
    services.task_service.store_worktree(task.task_id, worktree)
    services.task_service.update_task(
        task.task_id,
        status=LifecycleStatus.WORKTREE_READY,
        base_commit_sha=base_sha,
        worktree_path=worktree.worktree_path,
    )
    services.task_service.update_task(task.task_id, status=LifecycleStatus.PATCH_GENERATED)
    services.task_service.update_task(task.task_id, status=LifecycleStatus.PATCH_APPLIED)

    diff = "diff --git a/src/api.py b/src/api.py\n"
    patch = PatchArtifact(
        task_id=task.task_id,
        patch_id="patch-vgro-api-test",
        patch_sha256=hashlib.sha256(diff.encode()).hexdigest(),
        patch_size=len(diff.encode()),
        diff=diff,
        changed_files=["src/api.py"],
    )
    services.task_service.store_patch(task.task_id, patch)
    checks = [
        VerificationCheck(
            check_id=check_id,
            check_name=check_name,
            command=[check_id],
            status=CheckStatus.PASS,
            mandatory=True,
            exit_code=0,
        )
        for check_id, check_name in (
            ("changed-paths", "Changed-path validation"),
            ("patch-integrity", "Patch integrity"),
            ("docker-pytest", "pytest verification"),
            ("docker-ruff", "ruff verification"),
            ("docker-mypy", "mypy verification"),
        )
    ]
    report = VerificationReport(
        task_id=task.task_id,
        status=ReportStatus.VERIFIED,
        summary="All required checks passed.",
        checks=checks,
        docker_available=True,
        image_available=True,
    )
    services.task_service.store_verification_report(task.task_id, report)
    services.task_service.update_task(task.task_id, status=LifecycleStatus.VERIFYING)
    services.task_service.update_task(task.task_id, status=LifecycleStatus.VERIFIED)

    response = client.post(f"/api/v1/tasks/{task.task_id}/evaluate")

    assert response.status_code == 200
    decision = response.json()
    assert decision["task_id"] == task.task_id
    assert decision["decision"] == "ACCEPT"
    assert "CORRECTNESS_GATE_PASSED" in decision["reason_codes"]
    assert f"task:{task.task_id}" in decision["evidence_references"]
    assert "/private/" not in response.text

    latest = client.get(f"/api/v1/tasks/{task.task_id}/decision")
    assert latest.status_code == 200
    assert latest.json() == decision
    evidence = client.get(f"/api/v1/tasks/{task.task_id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()["latest_vgro_decision"] == decision
    assert "/private/" not in evidence.text

    unknown = "task-" + "0" * 32
    assert client.post(f"/api/v1/tasks/{unknown}/evaluate").status_code == 404
    assert client.get(f"/api/v1/tasks/{unknown}/decision").status_code == 404
