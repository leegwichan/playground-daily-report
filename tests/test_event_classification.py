"""file_category 분류 로직 단위 테스트 + git_log collector 통합 테스트.

backend 도메인 집중 모드의 분류 룰:
  - 모든 경로는 lowercase 정규화 후 fnmatch 매칭.
  - agent_path_patterns 가 backend 보다 우선순위.
  - 동률 → 'mixed', 양쪽 매칭 0 → 'unknown'.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_report.collectors.git_log import _classify_files, _matches_glob, collect_git_log
from daily_report.config import EventClassificationConfig


@pytest.fixture
def default_ec() -> EventClassificationConfig:
    return EventClassificationConfig.with_defaults()


def test_classify_backend_only(default_ec: EventClassificationConfig) -> None:
    cat, counts = _classify_files(
        ["src/Main.java", "src/Cache.java"],
        default_ec.backend_path_patterns,
        default_ec.agent_path_patterns,
    )
    assert cat == "backend"
    assert counts["backend_file_count"] == 2
    assert counts["agent_file_count"] == 0


def test_classify_agent_only(default_ec: EventClassificationConfig) -> None:
    cat, counts = _classify_files(
        ["skills/foo.md", ".claude/agents.md"],
        default_ec.backend_path_patterns,
        default_ec.agent_path_patterns,
    )
    assert cat == "agent"
    assert counts["agent_file_count"] == 2
    assert counts["backend_file_count"] == 0


def test_classify_mixed_equal_count(default_ec: EventClassificationConfig) -> None:
    cat, counts = _classify_files(
        ["src/Main.java", "skills/foo.md"],
        default_ec.backend_path_patterns,
        default_ec.agent_path_patterns,
    )
    assert cat == "mixed"
    assert counts["backend_file_count"] == 1
    assert counts["agent_file_count"] == 1


def test_classify_dominant_backend(default_ec: EventClassificationConfig) -> None:
    cat, _ = _classify_files(
        ["src/A.java", "src/B.java", "skills/x.md"],
        default_ec.backend_path_patterns,
        default_ec.agent_path_patterns,
    )
    assert cat == "backend"


def test_classify_dominant_agent(default_ec: EventClassificationConfig) -> None:
    cat, _ = _classify_files(
        ["src/A.java", "skills/x.md", "skills/y.md"],
        default_ec.backend_path_patterns,
        default_ec.agent_path_patterns,
    )
    assert cat == "agent"


def test_classify_unknown_when_no_matches(default_ec: EventClassificationConfig) -> None:
    cat, counts = _classify_files(
        ["random.txt", "binary.bin"],
        default_ec.backend_path_patterns,
        default_ec.agent_path_patterns,
    )
    assert cat == "unknown"
    assert counts["backend_file_count"] == 0
    assert counts["agent_file_count"] == 0


def test_classify_case_insensitive_dockerfile(default_ec: EventClassificationConfig) -> None:
    """E6 명시: src/Main.YML / Dockerfile 등 대소문자 변종도 정확히 매칭."""
    cat, _ = _classify_files(
        ["src/Main.YML", "Dockerfile"],
        default_ec.backend_path_patterns,
        default_ec.agent_path_patterns,
    )
    # Dockerfile matches `**/dockerfile` after lowercase → backend (1 backend, 0 agent)
    assert cat == "backend"


def test_classify_agent_precedence_workflows(default_ec: EventClassificationConfig) -> None:
    """R3: .github/workflows/*.yml 은 agent 로 우선 매칭 (backend 룰보다 우선)."""
    cat, _ = _classify_files(
        [".github/workflows/ci.yml"],
        default_ec.backend_path_patterns,
        default_ec.agent_path_patterns,
    )
    assert cat == "agent"


def test_classify_empty_files() -> None:
    cat, counts = _classify_files([], ["*.java"], ["skills/**/*.md"])
    assert cat == "unknown"
    assert counts == {"backend_file_count": 0, "agent_file_count": 0}


def test_matches_glob_doublestar_zero_dirs() -> None:
    """`**/X` 가 0개 이상 dirs 를 포함하는지 검증."""
    assert _matches_glob("dockerfile", "**/dockerfile")
    assert _matches_glob("a/dockerfile", "**/dockerfile")
    assert _matches_glob("a/b/c/dockerfile", "**/dockerfile")


def test_matches_glob_single_star_no_slash() -> None:
    """`*.java` 가 `/` 를 포함하면 매칭 안 함 (단일 path component)."""
    assert _matches_glob("main.java", "*.java")
    assert not _matches_glob("src/main.java", "*.java")


def test_git_log_integrates_file_category(two_commit_repo: Path) -> None:
    """git_log collector 가 Event.metadata.file_category 를 채우는지 통합 검증.

    two_commit_repo 의 두 번째 커밋은 src/Cache.java + tests/CacheTest.java →
    `*.java` / `src/**/*.java` 패턴에 매치 → backend.
    """
    default_ec = EventClassificationConfig.with_defaults()
    events = collect_git_log(
        two_commit_repo,
        since_hours=24,
        backend_patterns=default_ec.backend_path_patterns,
        agent_patterns=default_ec.agent_path_patterns,
    )
    # 두 커밋 중 적어도 하나는 backend 분류
    cats = [e.metadata.get("file_category") for e in events]
    assert "backend" in cats
    # changed_files 가 metadata 에 저장됨
    for e in events:
        assert "changed_files" in e.metadata
        assert isinstance(e.metadata["changed_files"], list)
