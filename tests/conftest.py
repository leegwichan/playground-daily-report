"""공용 pytest fixtures."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# src/ 를 import path 에 추가 (pip install 없이 테스트 가능하도록)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def empty_git_repo(tmp_path: Path) -> Path:
    """커밋이 없는 빈 git repo."""
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    _run_git(["init", "-b", "main"], cwd=repo)
    _run_git(["config", "user.email", "test@example.com"], cwd=repo)
    _run_git(["config", "user.name", "Tester"], cwd=repo)
    return repo


@pytest.fixture
def two_commit_repo(empty_git_repo: Path) -> Path:
    """한글 커밋 메시지 2개를 가진 repo."""
    repo = empty_git_repo
    (repo / "README.md").write_text("# learning notes", encoding="utf-8")
    _run_git(["add", "."], cwd=repo)
    _run_git(
        ["commit", "-m", "docs: 학습 노트 시작", "-m", "Redis 내부 자료구조 SDS 정리."],
        cwd=repo,
    )
    (repo / "cache.py").write_text("class Cache:\n    pass\n", encoding="utf-8")
    _run_git(["add", "."], cwd=repo)
    _run_git(["commit", "-m", "feat: cache 스켈레톤"], cwd=repo)
    return repo


@pytest.fixture
def project_root() -> Path:
    return _PROJECT_ROOT


@pytest.fixture
def example_profile_path(project_root: Path) -> Path:
    return project_root / "config" / "profile.example.yaml"


@pytest.fixture
def example_sources_path(project_root: Path) -> Path:
    return project_root / "config" / "sources.example.yaml"
