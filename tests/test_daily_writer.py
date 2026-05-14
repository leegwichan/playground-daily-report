"""writers/daily.py 단위 테스트 (mock LLM).

백엔드 도메인 집중 모드:
  - backend 분류 이벤트 0 → None (skip)
  - agent-only 이벤트 → None (skip)
  - backend 또는 mixed 이벤트 → Report (1 section, body_md ≤500자)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from daily_report.config import load_config
from daily_report.llm import LLM
from daily_report.schemas import Event, SourceType
from daily_report.writers._common import parse_json
from daily_report.writers.daily import write_daily_report


def _ev(file_category: str = "backend", title: str = "feat: cache") -> Event:
    now = datetime.now(timezone.utc)
    return Event(
        id=f"git:{title}",
        source=SourceType.GIT,
        occurred_at=now,
        collected_at=now,
        title=title,
        summary="Redis 캐시 레이어 추가.",
        body=None,
        url=None,
        tags=["test-repo"],
        metadata={
            "sha": "abc",
            "author_email": "x@y",
            "files_stat": "1 file changed",
            "file_category": file_category,
            "backend_file_count": 1 if file_category in ("backend", "mixed") else 0,
            "agent_file_count": 1 if file_category in ("agent", "mixed") else 0,
        },
    )


def test_writes_report_with_backend_event(example_profile_path: Path) -> None:
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_daily_report([_ev("backend")], cfg, llm, datetime.now(timezone.utc))

    assert report is not None
    assert report.kind == "daily"
    assert report.title
    assert len(report.tldr) >= 1
    # AC #3: sections 정확히 1개
    assert len(report.sections) == 1
    # AC #3: body_md ≤500자
    assert len(report.sections[0].body_md) <= 500
    assert report.estimated_read_minutes >= 1
    # 프로필 스냅샷이 들어가서 재현 가능
    assert report.profile_snapshot["levels"] == cfg.profile.levels


def test_empty_events_returns_none(example_profile_path: Path) -> None:
    """AC #2: backend 이벤트 0이면 None — Discord 호출 skip 신호."""
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_daily_report([], cfg, llm, datetime.now(timezone.utc))
    assert report is None


def test_only_agent_events_returns_none(example_profile_path: Path) -> None:
    """AC #4: agent-only 이벤트 → None (backend 0이므로)."""
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_daily_report(
        [_ev("agent", "docs: skill md")], cfg, llm, datetime.now(timezone.utc)
    )
    assert report is None


def test_mixed_event_counted_as_backend(example_profile_path: Path) -> None:
    """AC #13: mixed 는 daily 입력 시 backend 로 관대 취급."""
    cfg = load_config(example_profile_path)
    llm = LLM(model="x", mock=True)
    report = write_daily_report(
        [_ev("mixed", "refactor: both")], cfg, llm, datetime.now(timezone.utc)
    )
    assert report is not None
    assert len(report.sections) == 1
    assert len(report.sections[0].body_md) <= 500


def test_body_md_truncated_if_too_long(example_profile_path: Path) -> None:
    """LLM 이 500자 룰을 어겨도 코드가 잘라낸다 (NB3)."""
    cfg = load_config(example_profile_path)

    class _LongLLM(LLM):
        def __init__(self) -> None:
            self.mock = True
            self.model = "x"
            self._client = None

        def complete(self, system: str, user: str, **kw):  # type: ignore[override]
            import json as _json

            return _json.dumps(
                {
                    "title": "Daily long",
                    "tldr": ["x"],
                    "sections": [
                        {"heading": "k", "body_md": "가" * 800, "sources": []}
                    ],
                    "estimated_read_minutes": 1,
                },
                ensure_ascii=False,
            )

    report = write_daily_report(
        [_ev("backend")], cfg, _LongLLM(), datetime.now(timezone.utc)
    )
    assert report is not None
    assert len(report.sections[0].body_md) <= 500


def test_parse_json_handles_code_fence() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert parse_json(raw) == {"a": 1}


def test_parse_json_handles_surrounding_noise() -> None:
    raw = 'Here is the report:\n{"a": 1}\nLet me know if changes needed.'
    assert parse_json(raw) == {"a": 1}
