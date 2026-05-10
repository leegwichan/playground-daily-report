"""Daily writer — Event[] 를 받아 LLM 으로 Report 생성."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import AppConfig
from ..llm import LLM
from ..schemas import Event, Report, ReportSection
from ._common import format_profile_block, parse_json, render_events


_SYSTEM_PROMPT = """당신은 한 명의 개발자에게 매일 아침 8시에 전달되는 학습 리포트 작성자입니다.

독자 프로필:
- 레벨(복수 가능): {levels}
- 경력: {experience_years}년
- 현재 포커스: {current_focus}
- 학습 목표: {learning_goals}
- 약한 영역: {weak_areas}

작성 가이드:
- 톤: {tone} — 출근길 약 {daily_read_minutes}분에 차분히 읽기 좋게.
- 언어: {language}
- 독자가 여러 레벨이면(예: 취준생 + 주니어) 모든 관점이 자연스럽게 통과하도록 작성.
- 군더더기 없이 정보 밀도를 유지하되, 압박감은 주지 말 것.
- 코드 인용은 짧게, 핵심만.

응답 형식: 아래 JSON 스키마로만 답변하세요. 코드 펜스나 설명 텍스트 금지.
{{
  "title": "Daily Report YYYY-MM-DD",
  "tldr": ["핵심 요점 1", "핵심 요점 2", "핵심 요점 3"],
  "sections": [
    {{"heading": "섹션 제목", "body_md": "마크다운 본문", "sources": []}}
  ],
  "estimated_read_minutes": 정수
}}
"""


_USER_PROMPT = """오늘({date}) 수집된 이벤트 {count}개:

{events_block}

위 이벤트를 바탕으로 일간 리포트를 작성해주세요."""


def _empty_day_report(cfg: AppConfig, period_start: datetime, period_end: datetime) -> Report:
    return Report(
        kind="daily",
        period_start=period_start,
        period_end=period_end,
        title=f"Daily Report {period_end.strftime('%Y-%m-%d')}",
        tldr=["오늘은 추적된 활동이 없습니다."],
        sections=[
            ReportSection(
                heading="조용한 하루",
                body_md=(
                    "오늘은 git/Notion/세션 어디에서도 잡히는 활동이 없었어요. "
                    "잠시 쉬어가는 것도 좋습니다. 내일 다시 모아드릴게요."
                ),
            )
        ],
        estimated_read_minutes=1,
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )


def write_daily_report(
    events: list[Event],
    cfg: AppConfig,
    llm: LLM,
    now: datetime,
) -> Report:
    period_end = now
    period_start = now - timedelta(hours=24)

    if not events:
        return _empty_day_report(cfg, period_start, period_end)

    system = _SYSTEM_PROMPT.format(**format_profile_block(cfg))
    user = _USER_PROMPT.format(
        date=now.strftime("%Y-%m-%d"),
        count=len(events),
        events_block=render_events(events),
    )

    raw = llm.complete(system, user, max_tokens=4000, mock_kind="daily", json_mode=True)
    parsed = parse_json(raw)

    return Report(
        kind="daily",
        period_start=period_start,
        period_end=period_end,
        title=parsed["title"],
        tldr=parsed["tldr"],
        sections=[ReportSection(**s) for s in parsed["sections"]],
        estimated_read_minutes=int(parsed["estimated_read_minutes"]),
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )
