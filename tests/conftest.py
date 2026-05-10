"""공용 pytest fixtures."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# src/ 를 import path 에 추가 (pip install 없이 테스트 가능하도록)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# 사용자 .env / 시스템 환경에 GIT_AUTHOR_EMAIL 등이 설정되어 있으면
# git commit 이 이를 local config 보다 우선시하여 fixture 가 의도한 author 가 적용되지 않는다.
# 테스트 환경에서는 명시적으로 제거.
_GIT_AUTHOR_OVERRIDE_VARS = (
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_NAME",
    "EMAIL",
)


def _isolated_env() -> dict[str, str]:
    env = os.environ.copy()
    for var in _GIT_AUTHOR_OVERRIDE_VARS:
        env.pop(var, None)
    return env


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=_isolated_env(),
    )


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


@pytest.fixture
def git_only_sources_yaml(tmp_path: Path) -> Path:
    """git collector 만 활성화한 sources.yaml — e2e 테스트 격리용.

    실제 sources.example.yaml 은 claude_sessions 가 사용자 ~/.claude/projects 를
    가리키므로 테스트가 비결정적이 됨. 테스트 전용으로 모두 비활성.
    """
    p = tmp_path / "sources-test.yaml"
    p.write_text(
        "sources:\n"
        "  git:\n"
        "    enabled: true\n"
        "    local_repos: []\n"
        "  claude_sessions:\n"
        "    enabled: false\n"
        "  notion:\n"
        "    enabled: false\n"
        "  web_pages:\n"
        "    enabled: false\n"
        "  pdfs:\n"
        "    enabled: false\n"
        "collection:\n"
        "  daily_lookback_hours: 24\n",
        encoding="utf-8",
    )
    return p
