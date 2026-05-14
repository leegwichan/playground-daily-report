"""Monthly writer — 지난 30일 backend events 를 받아 3-섹션 월간 리포트 생성.

백엔드 도메인 집중 모드:
  - sections 정확히 3개: 메이저 테마 (재클러스터링) / 진도도 지표 / 다음 달 학습 방향.
  - 진도도 지표 (first_seen / revisit / tier_ratio) 는 코드가 결정론적으로 계산. LLM 은 narrative.
  - 다음 달 학습 방향 섹션은 read-only audit — tier 분배 enforcement 는 deep_dive 가 단독 책임.
  - file_category=='backend' 만 본문 인용 (엄격 분류).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import AppConfig
from ..db import Database
from ..llm import LLM
from ..schemas import Event, METADATA_KEY_FILE_CATEGORY, Report, ReportSection
from ._common import _source_specific_hint, format_profile_block, parse_json


_SYSTEM_PROMPT = """당신은 한 명의 백엔드 개발자에게 매월 1일 #monthly 채널로 전달되는 월간 회고 리포트 작성자입니다.

독자 프로필:
- 레벨(두 페르소나): {levels}
- 경력: {experience_years}년
- 주력 스택 (primary, 80%): {stacks_primary}
- 관심 스택 (interest, 20%): {stacks_interest}
- 학습 목표: {learning_goals}

엄격 규칙:
1. 출력 sections 는 **정확히 3개**: '메이저 테마 (재클러스터링)', '진도도 지표', '다음 달 학습 방향'.
2. 본문 어디서도 'AI Agent 작업' (입력에 '분류: agent') 이벤트는 인용하지 말 것.
3. 진도도 지표 섹션: 입력으로 받은 `first_seen_count`, `revisit_count`, `primary_count`, `interest_count` 숫자를 **그대로** 본문에 노출. LLM 은 산출하지 말 것 (코드가 이미 결정론적으로 계산).
4. 다음 달 학습 방향: 분배가 80:20 에서 멀어졌으면 부족한 쪽 어휘 우선 추천 (read-only audit — enforcement 는 daily/deep_dive 가 단독 책임).
5. 메이저 테마 섹션: 4주간 PR 클러스터를 합산해 5~7개 메이저 테마로 재구성, 각 테마당 STAR 통합본 1줄 + 면접 질문 3개.

응답 형식: JSON only.
{{
  "title": "Monthly Report YYYY-MM",
  "tldr": ["요점 1", "요점 2", "요점 3"],
  "sections": [
    {{"heading": "메이저 테마 (재클러스터링)", "body_md": "...", "sources": []}},
    {{"heading": "진도도 지표", "body_md": "first_seen={first_seen_count} / revisit={revisit_count} / primary:interest={primary_count}:{interest_count}\\n\\n해설...", "sources": []}},
    {{"heading": "다음 달 학습 방향", "body_md": "...", "sources": []}}
  ],
  "estimated_read_minutes": 정수
}}
"""


_USER_PROMPT = """이번 달({start} ~ {end}) 수집된 backend 이벤트 {count}개:

{events_block}

이번 달 covered 개념 ({concept_count}개):
{concepts_block}

진도도 지표 (코드가 계산한 결정론적 숫자 — 본문에 그대로 노출):
- first_seen_count: {first_seen_count}  (이번 달 처음 등장한 개념 수)
- revisit_count   : {revisit_count}     (재회독 개념 수)
- primary_count   : {primary_count}     (tier=primary 카운트)
- interest_count  : {interest_count}    (tier=interest 카운트)

위 입력으로 monthly 리포트를 작성해주세요 (sections 정확히 3개, agent 이벤트 본문 인용 금지)."""


def _empty_month_report(
    cfg: AppConfig, period_start: datetime, period_end: datetime
) -> Report:
    return Report(
        kind="monthly",
        period_start=period_start,
        period_end=period_end,
        title=f"Monthly Report {period_end.strftime('%Y-%m')}",
        tldr=["이번 달은 추적된 backend 활동이 적었습니다."],
        sections=[
            ReportSection(
                heading="조용한 한 달",
                body_md=(
                    "이번 달은 backend 분류 이벤트가 거의 없었어요. "
                    "다음 달에는 더 풍성한 회고를 모아드릴게요."
                ),
            )
        ],
        estimated_read_minutes=2,
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )


def _render_events_simple(events: list[Event]) -> str:
    if not events:
        return "(없음)"
    blocks: list[str] = []
    for ev in events[:30]:
        hint = _source_specific_hint(ev)
        blocks.append(f"- [{ev.source.value}] {ev.title}\n  {ev.summary[:140]}{hint}")
    extra = len(events) - 30
    if extra > 0:
        blocks.append(f"(... 외 {extra}개 더)")
    return "\n\n".join(blocks)


def _render_concepts(concepts: list[tuple[str, str | None, str]]) -> str:
    if not concepts:
        return "(없음)"
    lines: list[str] = []
    for concept, tier, _ts in concepts[:40]:
        tier_tag = f" (tier={tier})" if tier else ""
        lines.append(f"- {concept}{tier_tag}")
    extra = len(concepts) - 40
    if extra > 0:
        lines.append(f"(... 외 {extra}개 더)")
    return "\n".join(lines)


def write_monthly_report(
    events: list[Event],
    cfg: AppConfig,
    llm: LLM,
    now: datetime,
    db: Database | None = None,
    lookback_days: int = 30,
) -> Report:
    period_end = now
    period_start = now - timedelta(days=lookback_days)

    # 엄격 분류 — file_category == 'backend' 만 (AC #13, #16)
    backend_events = [
        e
        for e in events
        if e.metadata.get(METADATA_KEY_FILE_CATEGORY) == "backend"
    ]

    if not backend_events:
        return _empty_month_report(cfg, period_start, period_end)

    # 진도도 지표 (결정론적 — DB 가 있을 때만)
    first_seen_count = 0
    revisit_count = 0
    primary_count = 0
    interest_count = 0
    month_concepts: list[tuple[str, str | None, str]] = []
    if db is not None:
        month_concepts = db.concepts_covered_between(period_start, period_end)
        dist = db.tier_distribution(within_days=lookback_days)
        primary_count = dist.get("primary", 0)
        interest_count = dist.get("interest", 0)
        # first_seen vs revisit: 이번 달이 처음 등장이면 first_seen,
        # 그 이전에도 다룬 적이 있으면 revisit. 단순 휴리스틱:
        # times_covered 가 1 이면 first_seen, 2 이상이면 revisit.
        with db.conn() as conn:
            rows = conn.execute(
                "SELECT concept, times_covered FROM cs_concepts_covered "
                "WHERE last_covered_at >= ? AND last_covered_at < ?",
                (period_start.isoformat(), period_end.isoformat()),
            ).fetchall()
        for r in rows:
            if (r[1] or 1) >= 2:
                revisit_count += 1
            else:
                first_seen_count += 1

    profile_block = format_profile_block(cfg)
    profile_block["stacks_primary"] = (
        ", ".join(cfg.profile.stacks.primary) or "(미지정)"
    )
    profile_block["stacks_interest"] = (
        ", ".join(cfg.profile.stacks.interest) or "(미지정)"
    )
    profile_block["first_seen_count"] = first_seen_count
    profile_block["revisit_count"] = revisit_count
    profile_block["primary_count"] = primary_count
    profile_block["interest_count"] = interest_count

    system = _SYSTEM_PROMPT.format(**profile_block)
    user = _USER_PROMPT.format(
        start=period_start.strftime("%Y-%m-%d"),
        end=period_end.strftime("%Y-%m-%d"),
        count=len(backend_events),
        events_block=_render_events_simple(backend_events),
        concept_count=len(month_concepts),
        concepts_block=_render_concepts(month_concepts),
        first_seen_count=first_seen_count,
        revisit_count=revisit_count,
        primary_count=primary_count,
        interest_count=interest_count,
    )

    raw = llm.complete(system, user, max_tokens=6000, mock_kind="monthly", json_mode=True)
    parsed = parse_json(raw)

    # 안전망: sections 정확히 3개 (모자라면 진도도 섹션 코드가 보강)
    raw_sections = list(parsed.get("sections") or [])
    if len(raw_sections) < 3:
        # 진도도 섹션을 코드가 강제 부착 (LLM 누락 방어)
        progress_body = (
            f"- first_seen_count: {first_seen_count}\n"
            f"- revisit_count: {revisit_count}\n"
            f"- primary:interest = {primary_count} : {interest_count}"
        )
        while len(raw_sections) < 3:
            if len(raw_sections) == 0:
                raw_sections.append(
                    {"heading": "메이저 테마 (재클러스터링)", "body_md": "(LLM 응답 부족)", "sources": []}
                )
            elif len(raw_sections) == 1:
                raw_sections.append(
                    {"heading": "진도도 지표", "body_md": progress_body, "sources": []}
                )
            else:
                raw_sections.append(
                    {"heading": "다음 달 학습 방향", "body_md": "(LLM 응답 부족)", "sources": []}
                )
    sections = [ReportSection(**s) for s in raw_sections[:3]]

    return Report(
        kind="monthly",
        period_start=period_start,
        period_end=period_end,
        title=parsed.get("title") or f"Monthly Report {period_end.strftime('%Y-%m')}",
        tldr=parsed.get("tldr") or ["월간 backend 회고"],
        sections=sections,
        estimated_read_minutes=int(parsed.get("estimated_read_minutes") or 15),
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )
