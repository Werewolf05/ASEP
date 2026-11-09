from __future__ import annotations

import subprocess
from pathlib import Path

from app.repository.git_manager import GitRepositoryManager
from app.repository.inspector import RepositoryInspector


def test_repository_inspector_detects_git_repo(tmp_path: Path) -> None:
    repo_path = tmp_path / "demo-repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    (repo_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (repo_path / "tests").mkdir()
    (repo_path / "tests" / "test_app.py").write_text("def test_true():\n    assert True\n", encoding="utf-8")

    manager = GitRepositoryManager(allowed_roots=[str(tmp_path)])
    inspector = RepositoryInspector(manager, allowed_roots=[str(tmp_path)])
    result = inspector.inspect(str(repo_path))

    assert result.repository_name == "demo-repo"
    assert result.git_initialized is True
    assert "app.py" in result.tracked_files
    assert "tests/test_app.py" in result.test_files
    assert result.metadata["is_clean"] is False
