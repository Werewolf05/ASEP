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
