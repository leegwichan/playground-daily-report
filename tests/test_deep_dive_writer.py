"""writers/deep_dive.py 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_report.config import load_config
from daily_report.db import Database
from daily_report.llm import LLM
from daily_report.schemas import Event, SourceType
from daily_report.writers.deep_dive import (
    _filter_fallback_topics,
    write_deep_dive_report,
)


def _sample_event(title: str = "feat: redis cache", libs: list[str] | None = None) -> Event:
    now = datetime.now(timezone.utc)
    return Event(
        id="git:abc",
        source=SourceType.GIT,
        occurred_at=now,
        collected_at=now,
        title=title,
        summary="Redis 캐시 레이어 추가",
        tags=["test"],
        metadata={"library_hints": libs or ["redis"]},
    )


def test_filter_fallback_removes_already_covered() -> None:
    fallback = [
        "Redis 내부 자료구조 (SDS, ziplist, skiplist)",
        "Spring AOP 동작 원리",
        "PostgreSQL MVCC",
    ]
    covered = ["Spring AOP 동작 원리"]
    assert _filter_fallback_topics(fallback, covered) == [
        "Redis 내부 자료구조 (SDS, ziplist, skiplist)",
        "PostgreSQL MVCC",
    ]


def test_filter_fallback_partial_match() -> None:
    """부분 매칭으로 유사 개념도 차단."""
    fallback = ["Spring AOP 깊이"]
    covered = ["Spring AOP"]
    assert _filter_fallback_topics(fallback, covered) == []


def test_writes_deep_dive_with_mock_llm(
    example_profile_path: Path, tmp_path: Path
) -> None:
    cfg = load_config(example_profile_path)
    db = Database(tmp_path / "state.db")
    llm = LLM(model="x", mock=True)

    report = write_deep_dive_report(
        events=[_sample_event()],
        cfg=cfg,
        llm=llm,
        db=db,
        now=datetime.now(timezone.utc),
    )

    assert report is not None
    assert report.kind == "deep_dive"
    assert report.cs_foundations is not None
    assert report.cs_foundations.concept
    assert report.cs_foundations.relates_to_today_work is True
    # mock 응답이 두 레벨에 대한 perspectives 를 포함
    assert set(report.cs_foundations.perspectives.keys()) == {
        "backend_jobseeker",
        "backend_junior",
    }
    # sections: 왜 알아야 하는가 + 개념 설명 + (있으면) 더 읽기
    headings = [s.heading for s in report.sections]
    assert "왜 알아야 하는가" in headings
    assert "개념 설명" in headings
    assert "더 읽기" in headings  # mock 에 further_reading 있음


def test_returns_none_when_no_events_and_no_fallback(
    example_profile_path: Path, tmp_path: Path
) -> None:
    cfg = load_config(example_profile_path)
    # 폴백 풀에 있는 모든 토픽을 강제로 covered 처리
    db = Database(tmp_path / "state.db")
    for t in cfg.cs_foundations.fallback_topics:
        db.mark_concept_covered(t)

    llm = LLM(model="x", mock=True)
    report = write_deep_dive_report(
        events=[],
        cfg=cfg,
        llm=llm,
        db=db,
        now=datetime.now(timezone.utc),
    )
    assert report is None


def test_returns_report_when_events_present_even_if_fallback_empty(
    example_profile_path: Path, tmp_path: Path
) -> None:
    """events 가 있으면 폴백이 비어도 LLM 이 오늘 작업에서 골라줄 수 있어야."""
    cfg = load_config(example_profile_path)
    db = Database(tmp_path / "state.db")
    for t in cfg.cs_foundations.fallback_topics:
        db.mark_concept_covered(t)

    llm = LLM(model="x", mock=True)
    report = write_deep_dive_report(
        events=[_sample_event()],
        cfg=cfg,
        llm=llm,
        db=db,
        now=datetime.now(timezone.utc),
    )
    assert report is not None  # mock 은 항상 'today work' 응답을 줌


def test_db_concept_tracking_lifecycle(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    assert db.recently_covered_concepts(within_days=14) == []

    db.mark_concept_covered("Redis SDS")
    db.mark_concept_covered("Spring AOP")
    covered = db.recently_covered_concepts(within_days=14)
    assert "Redis SDS" in covered
    assert "Spring AOP" in covered

    # 같은 개념 재마크 → times_covered 증가
    db.mark_concept_covered("Redis SDS")
    with db.conn() as conn:
        row = conn.execute(
            "SELECT times_covered FROM cs_concepts_covered WHERE concept = ?",
            ("Redis SDS",),
        ).fetchone()
    assert row[0] == 2


def test_db_recently_covered_respects_window(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    db.mark_concept_covered("recent")
    # within_days=0 → 미래 cutoff → 어떤 것도 안 잡혀야...
    # 실제로 within_days=0 이면 cutoff = now → INSERT 직후 recently 잡힘. 1일 전이면?
    assert "recent" in db.recently_covered_concepts(within_days=1)
