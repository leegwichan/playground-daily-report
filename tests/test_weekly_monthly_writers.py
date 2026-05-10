"""writers/weekly.py + writers/monthly.py 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_report.config import load_config
from daily_report.llm import LLM
from daily_report.schemas import Event, SourceType
from daily_report.writers.monthly import write_monthly_report
from daily_report.writers.weekly import write_weekly_report


def _sample_events(n: int = 3) -> list[Event]:
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
            metadata={"sha": str(i)},
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
