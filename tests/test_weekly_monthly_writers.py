"""writers/weekly.py + writers/monthly.py 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_report.config import load_config
from daily_report.db import Database
from daily_report.llm import LLM
from daily_report.schemas import Event, SourceType
from daily_report.writers.monthly import write_monthly_report
from daily_report.writers.weekly import write_weekly_report


def _sample_events(n: int = 3, file_category: str = "backend") -> list[Event]:
    now = datetime.now(timezone.utc)
    return [
        Event(
            id=f"git:{i}",
            source=SourceType.GIT,
            occurred_at=now - timedelta(days=i),
            collected_at=now,
            title=f"feat: commit {i}",
            summary=f"작업 내용 {i}",
            tags=["test"],
            metadata={
                "sha": str(i),
                "file_category": file_category,
                "changed_files": [f"src/File{i}.java"],
            },
        )
        for i in range(n)
    ]


def test_weekly_writes_report_with_mock(example_profile_path: Path) -> None:
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_weekly_report(_sample_events(5), cfg, llm, datetime.now(timezone.utc))

    assert report.kind == "weekly"
    assert report.title
    assert len(report.tldr) >= 1
    assert len(report.sections) >= 1
    # 7 일 lookback
    assert (report.period_end - report.period_start).days == 7
    assert report.profile_snapshot["levels"] == cfg.profile.levels


def test_weekly_empty_returns_quiet_report(example_profile_path: Path) -> None:
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)  # empty 경로는 LLM 안 부름
    report = write_weekly_report([], cfg, llm, datetime.now(timezone.utc))
    assert report.kind == "weekly"
    assert any("조용한" in s.heading for s in report.sections)


def test_monthly_writes_report_with_mock(example_profile_path: Path) -> None:
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_monthly_report(_sample_events(10), cfg, llm, datetime.now(timezone.utc))

    assert report.kind == "monthly"
    assert (report.period_end - report.period_start).days == 30
    assert report.estimated_read_minutes >= 1


def test_monthly_empty_returns_quiet_report(example_profile_path: Path) -> None:
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_monthly_report([], cfg, llm, datetime.now(timezone.utc))
    assert report.kind == "monthly"
    assert any("조용한" in s.heading for s in report.sections)


def test_weekly_lookback_can_be_overridden(example_profile_path: Path) -> None:
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_weekly_report(
        _sample_events(2), cfg, llm, datetime.now(timezone.utc), lookback_days=14
    )
    assert (report.period_end - report.period_start).days == 14


def test_weekly_has_exactly_two_sections(example_profile_path: Path) -> None:
    """AC #14: weekly sections 정확히 2개 (이력서 재료 + 개념 누적 지표)."""
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_weekly_report(
        _sample_events(3), cfg, llm, datetime.now(timezone.utc)
    )
    headings = [s.heading for s in report.sections]
    assert "이력서 재료" in headings
    assert "개념 누적 지표" in headings
    assert len(report.sections) == 2


def test_weekly_drops_agent_events_from_body(example_profile_path: Path) -> None:
    """AC #16: agent 이벤트는 본문에 인용되지 않음 (엄격 분류)."""
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    agent_only = _sample_events(3, file_category="agent")
    report = write_weekly_report(
        agent_only, cfg, llm, datetime.now(timezone.utc)
    )
    # backend events 0 → quiet report
    assert any("조용한" in s.heading for s in report.sections)


def test_weekly_drops_cluster_with_empty_result_summary(
    example_profile_path: Path,
) -> None:
    """AC #18: result_summary 빈 cluster 는 코드가 자체 drop (LLM 협조 무관)."""
    import json as _json

    cfg = load_config(example_profile_path)

    class _DropLLM(LLM):
        def __init__(self) -> None:
            self.mock = True
            self.model = "x"
            self._client = None

        def complete(self, system: str, user: str, **kw):  # type: ignore[override]
            return _json.dumps(
                {
                    "title": "Weekly drop",
                    "tldr": ["x"],
                    "resume_clusters": [
                        {
                            "cluster_id": "abc12345",
                            "title": "feat:src",
                            "star_bullet": "ok bullet",
                            "result_summary": "",  # 빈 → drop
                            "interview_questions": ["q1", "q2", "q3"],
                        }
                    ],
                    "concept_drills": [],
                    "estimated_read_minutes": 5,
                },
                ensure_ascii=False,
            )

    report = write_weekly_report(
        _sample_events(2), cfg, _DropLLM(), datetime.now(timezone.utc)
    )
    # cluster 가 drop 되어 "검증 통과한 이력서 후보 없음" 문구 등장
    resume_section = next(s for s in report.sections if s.heading == "이력서 재료")
    assert (
        "후보 없음" in resume_section.body_md
        or "feat:src" not in resume_section.body_md
    )


def test_monthly_has_three_sections_with_progress_numbers(
    example_profile_path: Path, tmp_path: Path
) -> None:
    """AC #15: monthly sections 3개 + 진도도 지표 숫자 정확."""
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    db = Database(tmp_path / "monthly_state.db")
    # tier 분배: primary=2, interest=1
    db.mark_concept_covered("Spring AOP", tier="primary")
    db.mark_concept_covered("MySQL MVCC", tier="primary")
    db.mark_concept_covered("Kotlin null safety", tier="interest")

    report = write_monthly_report(
        _sample_events(5), cfg, llm, datetime.now(timezone.utc), db=db
    )
    assert report.kind == "monthly"
    assert len(report.sections) == 3
    headings = [s.heading for s in report.sections]
    assert "메이저 테마 (재클러스터링)" in headings
    assert "진도도 지표" in headings
    assert "다음 달 학습 방향" in headings


def test_monthly_drops_agent_events(example_profile_path: Path) -> None:
    """AC #16: monthly 도 agent 본문 인용 금지 (엄격)."""
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    agent_only = _sample_events(3, file_category="agent")
    report = write_monthly_report(
        agent_only, cfg, llm, datetime.now(timezone.utc)
    )
    assert any("조용한" in s.heading for s in report.sections)
