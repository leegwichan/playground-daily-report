"""writers/daily.py 단위 테스트 (mock LLM)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from daily_report.config import load_config
from daily_report.llm import LLM
from daily_report.schemas import Event, SourceType
from daily_report.writers.daily import _parse_json, write_daily_report


def _sample_event() -> Event:
    now = datetime.now(timezone.utc)
    return Event(
        id="git:abc",
        source=SourceType.GIT,
        occurred_at=now,
        collected_at=now,
        title="feat: 캐시 추가",
        summary="Redis 캐시 레이어 추가.",
        body=None,
        url=None,
        tags=["test-repo"],
        metadata={"sha": "abc", "author_email": "x@y", "files_stat": "1 file changed"},
    )


def test_writes_report_with_mock_llm(example_profile_path: Path) -> None:
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_daily_report([_sample_event()], cfg, llm, datetime.now(timezone.utc))

    assert report.kind == "daily"
    assert report.title
    assert len(report.tldr) >= 1
    assert len(report.sections) >= 1
    assert report.estimated_read_minutes >= 1
    # 프로필 스냅샷이 들어가서 재현 가능
    assert report.profile_snapshot["levels"] == cfg.profile.levels


def test_empty_events_produces_quiet_day_report(example_profile_path: Path) -> None:
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)  # 호출 안 됨 (empty path 는 LLM skip)
    report = write_daily_report([], cfg, llm, datetime.now(timezone.utc))

    assert report.kind == "daily"
    assert any("조용한" in s.heading or "활동" in t for s, t in zip(report.sections, report.tldr))
    assert report.estimated_read_minutes == 1


def test_parse_json_handles_code_fence() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert _parse_json(raw) == {"a": 1}


def test_parse_json_handles_surrounding_noise() -> None:
    raw = 'Here is the report:\n{"a": 1}\nLet me know if changes needed.'
    assert _parse_json(raw) == {"a": 1}
