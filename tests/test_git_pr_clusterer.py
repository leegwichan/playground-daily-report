"""processors/git_pr_clusterer.py 결정론 검증.

핵심:
  - 동일 입력 → 동일 cluster_id (8자 hex).
  - subject prefix 정규화 (lowercase, 공백 제거).
  - top_level_dir 추출 (changed_files 의 다수파).
"""

from __future__ import annotations

from datetime import datetime, timezone

from daily_report.processors.git_pr_clusterer import (
    _cluster_id,
    _normalize_prefix,
    _top_level_dir,
    cluster_pr_events,
)
from daily_report.schemas import Event, SourceType


def _ev(title: str, sha: str, changed: list[str]) -> Event:
    now = datetime.now(timezone.utc)
    return Event(
        id=f"git:{sha}",
        source=SourceType.GIT,
        occurred_at=now,
        collected_at=now,
        title=title,
        summary="x",
        metadata={"sha": sha, "changed_files": changed, "file_category": "backend"},
    )


def test_normalize_prefix_lowercase() -> None:
    assert _normalize_prefix("feat: foo") == "feat"
    assert _normalize_prefix("FEAT: bar") == "feat"
    assert _normalize_prefix("  Refactor: baz") == "refactor"


def test_normalize_prefix_no_colon_returns_other() -> None:
    assert _normalize_prefix("just a subject") == "other"
    assert _normalize_prefix("") == "other"


def test_top_level_dir_dominant() -> None:
    assert _top_level_dir(["src/a.java", "src/b.java", "tests/c.java"]) == "src"


def test_top_level_dir_alphabetic_tie_break() -> None:
    """동률 → 알파벳 순 첫."""
    assert _top_level_dir(["src/a.java", "tests/b.java"]) == "src"
    assert _top_level_dir(["zzz/a.java", "tests/b.java"]) == "tests"


def test_top_level_dir_empty_returns_root() -> None:
    assert _top_level_dir([]) == "root"
    assert _top_level_dir(["just_a_file"]) == "root"


def test_cluster_id_stable_across_runs() -> None:
    a = _cluster_id("feat", "src")
    b = _cluster_id("feat", "src")
    assert a == b
    assert len(a) == 8


def test_cluster_id_different_for_different_inputs() -> None:
    assert _cluster_id("feat", "src") != _cluster_id("fix", "src")
    assert _cluster_id("feat", "src") != _cluster_id("feat", "tests")


def test_cluster_pr_events_groups_by_prefix_and_dir() -> None:
    events = [
        _ev("feat: cache", "a1", ["src/Cache.java"]),
        _ev("feat: redis", "a2", ["src/Redis.java"]),
        _ev("fix: bug", "b1", ["tests/CacheTest.java"]),
    ]
    clusters = cluster_pr_events(events)
    assert len(clusters) == 2

    by_title = {c.title: c for c in clusters}
    assert "feat:src" in by_title
    assert "fix:tests" in by_title
    assert sorted(by_title["feat:src"].event_shas) == ["a1", "a2"]
    assert by_title["fix:tests"].event_shas == ["b1"]


def test_cluster_pr_events_deterministic_across_calls() -> None:
    events = [
        _ev("feat: a", "1", ["src/A.java"]),
        _ev("fix: b", "2", ["src/B.java"]),
        _ev("feat: c", "3", ["docs/C.md"]),
    ]
    first = cluster_pr_events(events)
    second = cluster_pr_events(events)
    assert [(c.cluster_id, c.title, tuple(c.event_shas)) for c in first] == [
        (c.cluster_id, c.title, tuple(c.event_shas)) for c in second
    ]


def test_cluster_pr_events_skips_non_git_events() -> None:
    """git 이외 source 는 무시."""
    now = datetime.now(timezone.utc)
    git_ev = _ev("feat: x", "g1", ["src/X.java"])
    web_ev = Event(
        id="web:1",
        source=SourceType.WEB,
        occurred_at=now,
        collected_at=now,
        title="blog post",
        summary="x",
    )
    clusters = cluster_pr_events([git_ev, web_ev])
    assert len(clusters) == 1


def test_cluster_pr_events_empty_input() -> None:
    assert cluster_pr_events([]) == []
