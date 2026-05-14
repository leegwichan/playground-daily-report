"""Daily writer — Event[] 를 받아 LLM 으로 Report 생성.

백엔드 도메인 집중 모드:
  - backend 분류 이벤트가 0 이면 None 반환 (Discord webhook 호출 자체 skip).
  - mixed 는 관대 분류 (backend 로 취급, AC #13).
  - sections 정확히 1개, body_md ≤500자, stacks 어휘만 사용.
  - 어제 backend 이벤트 1개의 title 을 yesterday_anchor 로 노출 (1줄 연결).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import AppConfig
from ..llm import LLM
from ..schemas import Event, METADATA_KEY_FILE_CATEGORY, Report, ReportSection
from ._common import format_profile_block, parse_json, render_events


_SYSTEM_PROMPT = """당신은 한 명의 백엔드 개발자에게 매일 아침 8시에 전달되는 학습 리포트 작성자입니다.

독자 프로필:
- 레벨: {levels}  (취준생 + 주니어 두 페르소나가 동시 서빙됨)
- 경력: {experience_years}년
- 주력 스택 (primary, 80% 비중): {stacks_primary}
- 관심 스택 (interest, 20% 비중): {stacks_interest}
- 학습 목표: {learning_goals}
- 약한 영역: {weak_areas}

엄격 규칙 (위반 금지):
1. 출력 sections 는 **정확히 1개**. heading 은 그날의 백엔드 키워드 1개 (5단어 이내).
2. body_md 총 길이는 **≤500자** (한글/영문/공백 포함). lens 1개로 응축.
3. 키워드는 위 stacks 어휘 (primary 우선, interest 곁가지) 안에서만 선택. Python/Postgres 같은 잔재 어휘 금지.
4. AI Agent 작업 (입력에 '분류: agent' 로 표기됨) 은 본문에 인용하지 말 것. 필요시 body_md 끝에 "오늘 도구 사용 N건" 한 줄 메타만 허용.
5. body_md 안에 어제 작업과 연결하는 한 줄 ("어제 …{yesterday_anchor} → 오늘 …") 을 반드시 포함.
6. tldr 은 1~3개, estimated_read_minutes 는 1.

응답 형식: 아래 JSON 스키마로만 답변. 코드 펜스나 설명 텍스트 금지.
{{
  "title": "Daily Report YYYY-MM-DD",
  "tldr": ["핵심 요점 1", ...],
  "sections": [
    {{"heading": "키워드 1개", "body_md": "≤500자 lens 본문", "sources": []}}
  ],
  "estimated_read_minutes": 1
}}
"""


_USER_PROMPT = """오늘({date}) 수집된 백엔드 이벤트 {count}개:

{events_block}

어제 백엔드 작업 앵커 (이 한 줄과 연결해야 함):
{yesterday_anchor}

위 입력으로 daily 리포트를 작성해주세요 (sections 1개, body_md ≤500자)."""


def _backend_events(events: list[Event]) -> list[Event]:
    """daily/deep_dive 입장에서 'backend' 로 취급되는 이벤트만 (mixed → backend 관대)."""
    return [
        e
        for e in events
        if (e.metadata.get(METADATA_KEY_FILE_CATEGORY) or "unknown") in ("backend", "mixed")
    ]


def _yesterday_anchor(events: list[Event], now: datetime) -> str:
    """어제 (24h~48h 전) backend git 이벤트 1개의 title 을 골라 yesterday anchor 로."""
    yesterday_start = now - timedelta(hours=48)
    yesterday_end = now - timedelta(hours=24)
    candidates = [
        e
        for e in events
        if e.source.value == "git"
        and yesterday_start <= e.occurred_at < yesterday_end
        and (e.metadata.get(METADATA_KEY_FILE_CATEGORY) or "unknown")
        in ("backend", "mixed")
    ]
    if not candidates:
        return "(어제 잡힌 backend 활동 없음 — 오늘 흐름에 자유롭게 연결)"
    return candidates[0].title[:120]


def write_daily_report(
    events: list[Event],
    cfg: AppConfig,
    llm: LLM,
    now: datetime,
) -> Optional[Report]:
    """daily Report 생성. backend 이벤트가 0이면 None 반환 (skip 신호).

    None 반환 시 main.run_daily() 가 Discord publish 를 호출하지 않는다 (AC #2, #4).
    """
    period_end = now
    period_start = now - timedelta(hours=24)

    backend_events = _backend_events(events)
    if not backend_events:
        return None

    profile_block = format_profile_block(cfg)
    profile_block["stacks_primary"] = (
        ", ".join(cfg.profile.stacks.primary) or "(미지정)"
    )
    profile_block["stacks_interest"] = (
        ", ".join(cfg.profile.stacks.interest) or "(미지정)"
    )
    yesterday_anchor = _yesterday_anchor(events, now)
    profile_block["yesterday_anchor"] = yesterday_anchor

    system = _SYSTEM_PROMPT.format(**profile_block)
    user = _USER_PROMPT.format(
        date=now.strftime("%Y-%m-%d"),
        count=len(backend_events),
        events_block=render_events(backend_events),
        yesterday_anchor=yesterday_anchor,
    )

    raw = llm.complete(system, user, max_tokens=4000, mock_kind="daily", json_mode=True)
    parsed = parse_json(raw)

    # 안전망: sections 1개로 강제, body_md ≤500자로 잘라냄 (LLM 협조 의존 제거).
    raw_sections = parsed.get("sections") or []
    if not raw_sections:
        raw_sections = [{"heading": "오늘", "body_md": "(빈 응답)", "sources": []}]
    first = raw_sections[0]
    body_md = first.get("body_md") or ""
    if len(body_md) > 500:
        body_md = body_md[:499] + "…"
    section = ReportSection(
        heading=first.get("heading") or "오늘",
        body_md=body_md,
        sources=first.get("sources") or [],
    )

    return Report(
        kind="daily",
        period_start=period_start,
        period_end=period_end,
        title=parsed.get("title") or f"Daily Report {now.strftime('%Y-%m-%d')}",
        tldr=parsed.get("tldr") or ["오늘 한 줄"],
        sections=[section],
        estimated_read_minutes=int(parsed.get("estimated_read_minutes") or 1),
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )
