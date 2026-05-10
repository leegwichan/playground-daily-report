"""Weekly writer — 지난 7일 events 를 받아 주간 회고 Report 생성."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import AppConfig
from ..llm import LLM
from ..schemas import Event, Report, ReportSection
from ._common import format_profile_block, parse_json, render_events


_SYSTEM_PROMPT = """당신은 한 명의 개발자에게 매주 월요일 아침 #weekly 채널로 전달되는 주간 회고 리포트 작성자입니다.

독자 프로필:
- 레벨(복수 가능): {levels}
- 경력: {experience_years}년
- 현재 포커스: {current_focus}
- 학습 목표: {learning_goals}
- 약한 영역: {weak_areas}

작성 가이드:
- 톤: {tone} — 한 주를 돌아보는 차분하고 구조적인 톤.
- 언어: {language}
- 분량: 약 10분 분량 (daily 보다 깊고, monthly 보다 짧게).
- 독자가 여러 레벨이면 모든 관점이 통과하도록 작성.
- **패턴 발견 우선**: 같은 주제/도구/개념이 여러 번 등장했다면 묶어서 1개 섹션으로.
- "다음 주 1-2 우선순위" 섹션을 마지막에 항상 포함.

응답 형식: JSON only, 코드 펜스/설명 텍스트 금지.
{{
  "title": "Weekly Report YYYY-MM-DD ~ YYYY-MM-DD",
  "tldr": ["요점 1", "요점 2", "요점 3"],
  "sections": [
    {{"heading": "섹션 제목", "body_md": "마크다운 본문", "sources": []}}
  ],
  "estimated_read_minutes": 정수
}}
"""


_USER_PROMPT = """이번 주({start} ~ {end}) 수집된 이벤트 {count}개:

{events_block}

위 이벤트로 weekly 회고 리포트를 작성해주세요."""


def _empty_week_report(
    cfg: AppConfig, period_start: datetime, period_end: datetime
) -> Report:
    return Report(
        kind="weekly",
        period_start=period_start,
        period_end=period_end,
        title=f"Weekly Report {period_start.strftime('%Y-%m-%d')} ~ {period_end.strftime('%Y-%m-%d')}",
        tldr=["이번 주는 추적된 활동이 적었습니다."],
        sections=[
            ReportSection(
                heading="조용한 한 주",
                body_md=(
                    "이번 주는 git/Notion/세션 등에서 잡히는 활동이 거의 없었어요. "
                    "휴식이거나 별도 환경에서 작업한 시간일 수 있습니다. 다음 주 다시 모아드릴게요."
                ),
            )
        ],
        estimated_read_minutes=2,
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )


def write_weekly_report(
    events: list[Event],
    cfg: AppConfig,
    llm: LLM,
    now: datetime,
    lookback_days: int = 7,
) -> Report:
    period_end = now
    period_start = now - timedelta(days=lookback_days)

    if not events:
        return _empty_week_report(cfg, period_start, period_end)

    system = _SYSTEM_PROMPT.format(**format_profile_block(cfg))
    user = _USER_PROMPT.format(
        start=period_start.strftime("%Y-%m-%d"),
        end=period_end.strftime("%Y-%m-%d"),
        count=len(events),
        events_block=render_events(events),
    )

    raw = llm.complete(system, user, max_tokens=5000, mock_kind="weekly", json_mode=True)
    parsed = parse_json(raw)

    return Report(
        kind="weekly",
        period_start=period_start,
        period_end=period_end,
        title=parsed["title"],
        tldr=parsed["tldr"],
        sections=[ReportSection(**s) for s in parsed["sections"]],
        estimated_read_minutes=int(parsed["estimated_read_minutes"]),
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )
