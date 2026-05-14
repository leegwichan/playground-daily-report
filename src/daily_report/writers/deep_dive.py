"""Deep-dive (CS 보강) writer — #cs-foundations 채널 대상.

오늘 events 와 사용자 프로필을 결합해, 가능한 한 *오늘 작업과 연관된*
CS 또는 도구 내부 지식 1개를 다루는 Report 를 생성한다.

연관 토픽이 없으면 profile.cs_foundations.fallback_topics 에서 폴백.
이미 min_days_between_repeat 안에 다룬 개념은 DB 로 필터링.

백엔드 도메인 집중 모드 추가 사항:
  - perspectives 는 정확히 backend_jobseeker + backend_junior 두 key (둘 다 빈 문자열 금지).
  - stacks_primary 80%, stacks_interest 20% 분배 — 4주 평균 부족한 쪽을 추천 (tier suggested).
  - 지난 N일 covered 개념 link 후보를 user prompt 에 주입.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from ..config import AppConfig
from ..db import Database
from ..schemas import (
    CSFoundationsBlock,
    Event,
    METADATA_KEY_FILE_CATEGORY,
    Report,
    ReportSection,
)
from ..llm import LLM
from ._common import _source_specific_hint, parse_json


_SYSTEM_PROMPT = """당신은 한 명의 백엔드 개발자에게 매일 #cs-foundations 채널로 전달되는 CS / 도구 내부지식 보강 콘텐츠 작성자입니다.

독자 프로필:
- 레벨(반드시 두 페르소나): {levels}  ← perspectives 의 key 는 정확히 이 두 값.
- 약한 영역: {weak_areas}
- 학습 목표: {learning_goals}
- 주력 스택 (primary, 80%): {stacks_primary}
- 관심 스택 (interest, 20%): {stacks_interest}
- 오늘 추천 tier: {suggested_tier}  ← 오늘 backend 이벤트 연관 토픽이 없을 때만 이 추천을 따름.

CS 보강 정책:
- 우선 영역: {prioritize}
- 가능하면 *오늘 backend 활동에서 직접 연관된 개념* 을 다룰 것.
- 연관 개념이 없으면 폴백 풀에서 1개 선택. tier 분배가 80:20 에서 멀어졌다면 부족한 쪽 어휘 우선.
- 이미 다룬 개념(중복 회피 목록) 은 절대 선택하지 말 것.
- 입력 이벤트에 '분류: agent' 가 붙은 항목은 본문 인용에서 제외 — 도구 사용량 메타 신호로만 인지.
- 입력 events 에 '분류: backend' 또는 '분류: mixed' 만 본문 인용 후보.

작성 가이드:
- 톤: {tone} — 출근길 약 {daily_read_minutes}분에 차분히 읽기 좋게.
- 언어: {language}
- perspectives 는 정확히 두 key (backend_jobseeker, backend_junior). **둘 다 빈 문자열 금지.**
- quick_explanation_md 는 5-10 분 분량의 충실한 markdown. 코드는 최소.
- 지난 N일 안에 다룬 covered 개념이 오늘 주제와 연관되면 quick_explanation_md 또는 further_reading 에 1줄 link/언급. 강제 아님 — 없으면 생략.
- further_reading 은 신뢰할 수 있는 1차 자료 위주 (공식 문서, 표준, 원논문). 추측 URL 금지.
- tier: 고른 개념이 primary 어휘면 "primary", interest 어휘면 "interest". 명백히 어느 한쪽이 아니면 null.

응답 형식: 아래 JSON 스키마로만 답변. 코드 펜스나 설명 텍스트 금지.
{{
  "triggered_by": "어떤 이벤트/토픽/폴백에서 왔는지 한 줄",
  "concept": "다룰 개념명 (간결, 검색 가능한 표준 명칭)",
  "relates_to_today_work": true | false,
  "perspectives": {{ "backend_jobseeker": "...", "backend_junior": "..." }},
  "quick_explanation_md": "markdown 본문",
  "further_reading": ["https://...", ...],
  "tier": "primary" | "interest" | null
}}
"""


_USER_PROMPT = """오늘({date}) 수집된 이벤트 {count}개:

{events_block}

이미 최근 {min_days}일 안에 다뤄서 이번엔 *피해야 할* 개념:
{covered_block}

지난 14일 안에 다룬 개념 (오늘 주제와 연관되면 link/언급 후보):
{recent_concepts_block}

오늘 작업과 연관된 개념을 못 찾을 경우에만 이 폴백 풀에서 1개 선택:
{fallback_block}

위 입력으로 deep_dive JSON 을 작성해주세요."""


def _render_events(events: list[Event]) -> str:
    if not events:
        return "(오늘 수집된 이벤트 없음)"
    blocks = []
    for ev in events:
        # file_category prefix + source-specific hint 재사용 (DRY — _common 의 헬퍼)
        hint = _source_specific_hint(ev)
        tag_hint = f"\n  태그: {', '.join(ev.tags)}" if ev.tags else ""
        blocks.append(
            f"- [{ev.source.value}] {ev.title}\n"
            f"  {ev.summary[:200]}{hint}{tag_hint}"
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


def _choose_tier_for_today(db: Database, within_days: int = 28) -> Literal["primary", "interest"]:
    """80:20 분배에서 부족한 쪽을 오늘의 추천 tier 로 반환.

    이 추천은 deep_dive call 의 유일한 enforcement 지점. monthly 는 read-only audit.
    """
    dist = db.tier_distribution(within_days=within_days)
    primary = dist.get("primary", 0)
    interest = dist.get("interest", 0)
    total = primary + interest
    if total == 0:
        return "primary"
    primary_share = primary / total
    # 0.80 미만이면 primary 부족 → primary, 0.80 이상이면 interest 부족 → interest 추천
    if primary_share < 0.80:
        return "primary"
    return "interest"


def _ensure_perspectives_two_keys(
    perspectives: dict[str, str], levels: list[str]
) -> dict[str, str]:
    """perspectives 의 두 key (backend_jobseeker, backend_junior) 모두 비어있지 않도록 보강.

    LLM 이 한 쪽을 빼먹거나 빈 문자열을 주면 placeholder 로 채워 publisher 가 두 key 모두 노출.
    """
    required = list(levels) if levels else ["backend_jobseeker", "backend_junior"]
    result = dict(perspectives or {})
    for key in required:
        v = result.get(key)
        if not (isinstance(v, str) and v.strip()):
            result[key] = (
                f"(LLM 이 {key} 관점 채우지 않음 — 다음 회차에 명시적으로 두 관점 요구)"
            )
    return result


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
    tier_tag = block.tier or "tier 미지정"
    return Report(
        kind="deep_dive",
        period_start=period_start,
        period_end=period_end,
        title=f"Deep Dive · {block.concept}",
        tldr=[
            f"개념: {block.concept}",
            f"트리거: {block.triggered_by}",
            f"분류: {relate_tag} / {tier_tag}",
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
    backend/mixed 만 LLM 본문 인용 후보 (관대 분류 — AC #13).
    """
    period_end = now
    period_start = now - timedelta(hours=24)

    # daily 와 동일한 관대 분류: backend + mixed 만 인용 대상
    backend_events = [
        e
        for e in events
        if (e.metadata.get(METADATA_KEY_FILE_CATEGORY) or "unknown") in ("backend", "mixed")
    ]

    covered = db.recently_covered_concepts(
        within_days=cfg.cs_foundations.min_days_between_repeat
    )
    available_fallbacks = _filter_fallback_topics(
        cfg.cs_foundations.fallback_topics, covered
    )

    # 오늘 backend events 도 없고 폴백도 다 소진되면 skip
    if not backend_events and not available_fallbacks:
        return None

    # tier 분배 추천 (80:20)
    suggested_tier = _choose_tier_for_today(db, within_days=28)

    # 지난 14일 covered concepts (link 후보)
    recent_with_meta = db.recently_covered_concepts_with_meta(within_days=14)
    recent_concepts_block = (
        _bullet_list([c for c, _ts in recent_with_meta])
        if recent_with_meta
        else "(없음 — link 후보 없음)"
    )

    system = _SYSTEM_PROMPT.format(
        levels=", ".join(cfg.profile.levels),
        weak_areas=", ".join(cfg.profile.weak_areas) or "(미지정)",
        learning_goals="; ".join(cfg.profile.learning_goals) or "(미지정)",
        stacks_primary=", ".join(cfg.profile.stacks.primary) or "(미지정)",
        stacks_interest=", ".join(cfg.profile.stacks.interest) or "(미지정)",
        suggested_tier=suggested_tier,
        prioritize=", ".join(cfg.cs_foundations.prioritize) or "(미지정)",
        tone=cfg.profile.tone,
        daily_read_minutes=cfg.profile.daily_read_minutes,
        language=cfg.profile.language,
    )
    user = _USER_PROMPT.format(
        date=now.strftime("%Y-%m-%d"),
        count=len(backend_events),
        events_block=_render_events(backend_events),
        min_days=cfg.cs_foundations.min_days_between_repeat,
        covered_block=_bullet_list(covered),
        recent_concepts_block=recent_concepts_block,
        fallback_block=_bullet_list(
            available_fallbacks,
            empty_text="(폴백 풀이 모두 소진됨 — 오늘 활동에서 반드시 골라야 함)",
        ),
    )

    # deep_dive 는 quick_explanation_md 가 가장 긴 콘텐츠 → 토큰 여유 충분히
    raw = llm.complete(system, user, max_tokens=8000, mock_kind="deep_dive", json_mode=True)
    parsed = parse_json(raw)

    perspectives = _ensure_perspectives_two_keys(
        parsed.get("perspectives") or {}, cfg.profile.levels
    )

    tier_val = parsed.get("tier")
    if tier_val not in ("primary", "interest"):
        tier_val = None

    block = CSFoundationsBlock(
        triggered_by=parsed["triggered_by"],
        concept=parsed["concept"],
        relates_to_today_work=bool(parsed["relates_to_today_work"]),
        perspectives=perspectives,
        quick_explanation_md=parsed["quick_explanation_md"],
        further_reading=parsed.get("further_reading") or [],
        tier=tier_val,
    )

    return _build_report_from_block(block, cfg, period_start, period_end)
