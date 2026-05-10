"""collectors/git_log.py 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from daily_report.collectors.git_log import collect_git_log, stable_event_id
from daily_report.schemas import SourceType


def test_returns_empty_when_not_a_git_dir(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert collect_git_log(plain) == []


def test_returns_empty_for_empty_repo(empty_git_repo: Path) -> None:
    assert collect_git_log(empty_git_repo) == []


def test_collects_two_commits(two_commit_repo: Path) -> None:
    events = collect_git_log(two_commit_repo, since_hours=24)
    assert len(events) == 2
    for ev in events:
        assert ev.source == SourceType.GIT
        assert ev.id.startswith("git:")
        assert "sha" in ev.metadata
        assert ev.metadata["author_email"] == "test@example.com"


def test_stable_id_across_runs(two_commit_repo: Path) -> None:
    """같은 repo 를 두 번 수집해도 같은 id 가 나와야 dedup 가능."""
    first = {ev.id for ev in collect_git_log(two_commit_repo)}
    second = {ev.id for ev in collect_git_log(two_commit_repo)}
    assert first == second


def test_korean_subject_preserved(two_commit_repo: Path) -> None:
    events = collect_git_log(two_commit_repo)
    titles = [ev.title for ev in events]
    assert any("학습 노트" in t for t in titles)
    assert any("스켈레톤" in t for t in titles)


def test_author_filter(two_commit_repo: Path) -> None:
    matched = collect_git_log(two_commit_repo, author_email="test@example.com")
    assert len(matched) == 2

    nobody = collect_git_log(two_commit_repo, author_email="nobody@nowhere")
    assert nobody == []


def test_stable_event_id_helper() -> None:
    a = stable_event_id("notion", "page-1", "2026-05-10T00:00:00")
    b = stable_event_id("notion", "page-1", "2026-05-10T00:00:00")
    c = stable_event_id("notion", "page-1", "2026-05-10T00:00:01")
    assert a == b
    assert a != c
    assert len(a) == 16
