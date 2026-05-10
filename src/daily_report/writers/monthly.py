"""Monthly writer — 지난 30일 events 를 받아 월간 회고 Report 생성."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import AppConfig
from ..llm import LLM
from ..schemas import Event, Report, ReportSection
from ._common import format_profile_block, parse_json, render_events


_SYSTEM_PROMPT = """당신은 한 명의 개발자에게 매월 1일 아침 #monthly 채널로 전달되는 월간 회고 리포트 작성자입니다.

독자 프로필:
- 레벨(복수 가능): {levels}
- 경력: {experience_years}년
- 현재 포커스: {current_focus}
- 학습 목표: {learning_goals}
- 약한 영역: {weak_areas}

작성 가이드:
- 톤: {tone} — 한 달을 돌아보는 통찰 위주의 톤. 단순 활동 나열 금지.
- 언어: {language}
- 분량: 약 15-20분 분량 (가장 깊은 회고).
- 독자가 여러 레벨이면 모든 관점이 통과하도록 작성.
- **메이저 토픽 추출 우선**: 한 달간 반복적으로 다룬 주제 3-5개를 정제하여 깊이 있게 다룰 것.
- "이 달에 늘어난 역량" 섹션 + "다음 달 학습 방향 제안" 섹션을 항상 포함.

응답 형식: JSON only, 코드 펜스/설명 텍스트 금지.
{{
  "title": "Monthly Report YYYY-MM",
  "tldr": ["요점 1", "요점 2", "요점 3"],
  "sections": [
    {{"heading": "섹션 제목", "body_md": "마크다운 본문", "sources": []}}
  ],
  "estimated_read_minutes": 정수
}}
"""


_USER_PROMPT = """이번 달({start} ~ {end}) 수집된 이벤트 {count}개:

{events_block}

위 이벤트로 monthly 회고 리포트를 작성해주세요."""


def _empty_month_report(
    cfg: AppConfig, period_start: datetime, period_end: datetime
) -> Report:
    return Report(
        kind="monthly",
        period_start=period_start,
        period_end=period_end,
        title=f"Monthly Report {period_end.strftime('%Y-%m')}",
        tldr=["이번 달은 추적된 활동이 적었습니다."],
        sections=[
            ReportSection(
                heading="조용한 한 달",
                body_md=(
                    "이번 달은 자동 수집되는 소스에서 잡히는 활동이 적었어요. "
                    "다음 달에는 더 풍성한 회고를 모아드릴게요."
                ),
            )
        ],
        estimated_read_minutes=2,
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )


def write_monthly_report(
    events: list[Event],
    cfg: AppConfig,
    llm: LLM,
    now: datetime,
    lookback_days: int = 30,
) -> Report:
    period_end = now
    period_start = now - timedelta(days=lookback_days)

    if not events:
        return _empty_month_report(cfg, period_start, period_end)

    system = _SYSTEM_PROMPT.format(**format_profile_block(cfg))
    user = _USER_PROMPT.format(
        start=period_start.strftime("%Y-%m-%d"),
        end=period_end.strftime("%Y-%m-%d"),
        count=len(events),
        events_block=render_events(events),
    )

    raw = llm.complete(system, user, max_tokens=6000, mock_kind="monthly", json_mode=True)
    parsed = parse_json(raw)

    return Report(
        kind="monthly",
        period_start=period_start,
        period_end=period_end,
        title=parsed["title"],
        tldr=parsed["tldr"],
        sections=[ReportSection(**s) for s in parsed["sections"]],
        estimated_read_minutes=int(parsed["estimated_read_minutes"]),
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )
