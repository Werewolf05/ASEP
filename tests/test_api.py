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
