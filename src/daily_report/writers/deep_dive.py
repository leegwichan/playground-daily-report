"""Deep-dive (CS 보강) writer — #cs-foundations 채널 대상.

오늘 events 와 사용자 프로필을 결합해, 가능한 한 *오늘 작업과 연관된*
CS 또는 도구 내부 지식 1개를 다루는 Report 를 생성한다.

연관 토픽이 없으면 profile.cs_foundations.fallback_topics 에서 폴백.
이미 min_days_between_repeat 안에 다룬 개념은 DB 로 필터링.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import AppConfig
from ..db import Database
from ..llm import LLM
from ..schemas import (
    CSFoundationsBlock,
    Event,
    Report,
    ReportSection,
)
from ._common import parse_json


_SYSTEM_PROMPT = """당신은 한 명의 개발자에게 매일 #cs-foundations 채널로 전달되는 CS / 도구 내부지식 보강 콘텐츠 작성자입니다.

독자 프로필:
- 레벨(복수 가능): {levels}
- 약한 영역: {weak_areas}
- 학습 목표: {learning_goals}
- 관심 도구/기술: {current_focus}

CS 보강 정책:
- 우선 영역: {prioritize}
- 가능하면 *오늘 활동에서 직접 연관된 개념* 을 다룰 것 (relate_to_today_work 정책).
- 연관 개념이 없으면 폴백 풀에서 1개 선택.
- 이미 다룬 개념(중복 회피 목록) 은 절대 선택하지 말 것.

작성 가이드:
- 톤: {tone} — 출근길 약 {daily_read_minutes}분에 차분히 읽기 좋게.
- 언어: {language}
- 독자가 여러 레벨이면 perspectives 안에 *각 레벨별로* 1개씩 관점을 작성. key 는 정확히 levels 리스트 항목.
- quick_explanation_md 는 5-10 분 분량의 충실한 markdown. 코드는 최소.
- further_reading 은 신뢰할 수 있는 1차 자료 위주 (공식 문서, 표준, 원논문). 추측 URL 금지.

응답 형식: 아래 JSON 스키마로만 답변. 코드 펜스나 설명 텍스트 금지.
{{
  "triggered_by": "어떤 이벤트/토픽/폴백에서 왔는지 한 줄",
  "concept": "다룰 개념명 (간결, 검색 가능한 표준 명칭)",
  "relates_to_today_work": true | false,
  "perspectives": {{ "<level>": "...", ... }},
  "quick_explanation_md": "markdown 본문",
  "further_reading": ["https://...", ...]
}}
"""


_USER_PROMPT = """오늘({date}) 수집된 이벤트 {count}개:

{events_block}

이미 최근 {min_days}일 안에 다뤄서 이번엔 *피해야 할* 개념:
{covered_block}

오늘 작업과 연관된 개념을 못 찾을 경우에만 이 폴백 풀에서 1개 선택:
{fallback_block}

위 입력으로 deep_dive JSON 을 작성해주세요."""


def _render_events(events: list[Event]) -> str:
    if not events:
        return "(오늘 수집된 이벤트 없음)"
    blocks = []
    for ev in events:
        tag_hint = f"  태그: {', '.join(ev.tags)}" if ev.tags else ""
        lib_hint = ""
        libs = ev.metadata.get("library_hints") if isinstance(ev.metadata, dict) else None
        if libs:
            lib_hint = f"\n  라이브러리: {', '.join(libs)}"
        blocks.append(
            f"- [{ev.source.value}] {ev.title}\n"
            f"  {ev.summary[:200]}{tag_hint}{lib_hint}"
        )
    return "\n\n".join(blocks)


def _bullet_list(items: list[str], empty_text: str = "(없음)") -> str:
    if not items:
        return empty_text
    return "\n".join(f"- {x}" for x in items)


def _filter_fallback_topics(
    fallback_topics: list[str], covered: list[str]
) -> list[str]:
    """min_days 내 다룬 개념을 폴백에서 제거. 부분 문자열 매칭으로 유사 개념도 차단."""
    if not covered:
        return list(fallback_topics)
    covered_lower = [c.lower() for c in covered]
    return [
        t
        for t in fallback_topics
        if not any(c in t.lower() or t.lower() in c for c in covered_lower)
    ]


def _build_report_from_block(
    block: CSFoundationsBlock,
    cfg: AppConfig,
    period_start: datetime,
    period_end: datetime,
) -> Report:
    """CSFoundationsBlock 을 디스코드 가독성 좋은 sections 로 풀어서 Report 로."""
    sections: list[ReportSection] = []

    # 1) 왜 알아야 하는가 — 레벨별 관점
    if block.perspectives:
        body = "\n\n".join(
            f"**{level}**\n{view}" for level, view in block.perspectives.items()
        )
        sections.append(
            ReportSection(heading="왜 알아야 하는가", body_md=body)
        )

    # 2) 본문 설명
    sections.append(
        ReportSection(heading="개념 설명", body_md=block.quick_explanation_md)
    )

    # 3) 더 읽기
    if block.further_reading:
        body = "\n".join(f"- <{url}>" for url in block.further_reading)
        sections.append(ReportSection(heading="더 읽기", body_md=body))

    relate_tag = "오늘 작업 연관" if block.relates_to_today_work else "폴백 풀"
    return Report(
        kind="deep_dive",
        period_start=period_start,
        period_end=period_end,
        title=f"Deep Dive · {block.concept}",
        tldr=[
            f"개념: {block.concept}",
            f"트리거: {block.triggered_by}",
            f"분류: {relate_tag}",
        ],
        sections=sections,
        cs_foundations=block,
        estimated_read_minutes=max(5, min(10, cfg.profile.daily_read_minutes // 2)),
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )


def write_deep_dive_report(
    events: list[Event],
    cfg: AppConfig,
    llm: LLM,
    db: Database,
    now: datetime,
) -> Optional[Report]:
    """deep_dive Report 생성. 가능한 개념이 전혀 없으면 None.

    None 반환 시 main.py 가 #cs-foundations 발송을 건너뛰면 됨.
    """
    period_end = now
    period_start = now - timedelta(hours=24)

    covered = db.recently_covered_concepts(
        within_days=cfg.cs_foundations.min_days_between_repeat
    )
    available_fallbacks = _filter_fallback_topics(
        cfg.cs_foundations.fallback_topics, covered
    )

    # 오늘 events 도 없고 폴백도 다 소진되면 skip
    if not events and not available_fallbacks:
        return None

    system = _SYSTEM_PROMPT.format(
        levels=", ".join(cfg.profile.levels),
        weak_areas=", ".join(cfg.profile.weak_areas) or "(미지정)",
        learning_goals="; ".join(cfg.profile.learning_goals) or "(미지정)",
        current_focus=", ".join(cfg.profile.current_focus) or "(미지정)",
        prioritize=", ".join(cfg.cs_foundations.prioritize) or "(미지정)",
        tone=cfg.profile.tone,
        daily_read_minutes=cfg.profile.daily_read_minutes,
        language=cfg.profile.language,
    )
    user = _USER_PROMPT.format(
        date=now.strftime("%Y-%m-%d"),
        count=len(events),
        events_block=_render_events(events),
        min_days=cfg.cs_foundations.min_days_between_repeat,
        covered_block=_bullet_list(covered),
        fallback_block=_bullet_list(available_fallbacks, empty_text="(폴백 풀이 모두 소진됨 — 오늘 활동에서 반드시 골라야 함)"),
    )

    # deep_dive 는 quick_explanation_md 가 가장 긴 콘텐츠 → 토큰 여유 충분히
    raw = llm.complete(system, user, max_tokens=8000, mock_kind="deep_dive", json_mode=True)
    parsed = parse_json(raw)

    block = CSFoundationsBlock(
        triggered_by=parsed["triggered_by"],
        concept=parsed["concept"],
        relates_to_today_work=bool(parsed["relates_to_today_work"]),
        perspectives=parsed.get("perspectives") or {},
        quick_explanation_md=parsed["quick_explanation_md"],
        further_reading=parsed.get("further_reading") or [],
    )

    return _build_report_from_block(block, cfg, period_start, period_end)
