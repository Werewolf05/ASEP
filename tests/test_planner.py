from app.agent.mock_provider import MockAIProvider
from app.agent.planner import Planner
from app.models.issue import IssueCreate, RepositoryInspectionResult


def test_planner_generates_structured_plan() -> None:
    issue = IssueCreate(
        title="Fix validation bug",
        description="Reject empty usernames in the sign in flow and add a regression test.",
    )
    repository = RepositoryInspectionResult(
        repository_name="demo-service",
        display_path="/tmp/demo-service",
        git_initialized=True,
        branch_name="main",
        status_summary="",
        tracked_files=["app/auth.py", "tests/test_auth.py"],
        language_summary={"python": 2},
        test_files=["tests/test_auth.py"],
        summary="Repository ready for a localized fix.",
    )

    planner = Planner(MockAIProvider())
    result = planner.build_plan(issue, repository)

    assert result.provider == "mock-ai-v1"
    assert result.status == "ready"
    assert result.confidence == "high"
    assert result.summary
    assert result.tasks
    assert result.steps
