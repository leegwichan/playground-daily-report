"""Weekly writer — 지난 7일 backend events 를 받아 2-섹션 주간 리포트 생성.

백엔드 도메인 집중 모드:
  - sections 정확히 2개: 이력서 재료 / 개념 누적 지표.
  - file_category=='backend' 만 본문 인용 (엄격 분류 — AC #13).
  - PR 클러스터링은 `processors/git_pr_clusterer.py` 가 결정론적으로 처리. LLM 은 STAR 텍스트 + 면접 질문 3개만 채움.
  - LLM 응답을 `WeeklyResumeBundle.model_validate()` 로 검증, result_summary 빈 cluster 는 코드가 drop (AC #18).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from ..config import AppConfig
from ..db import Database
from ..llm import LLM
from ..processors.git_pr_clusterer import cluster_pr_events
from ..schemas import (
    Event,
    METADATA_KEY_FILE_CATEGORY,
    PrCluster,
    Report,
    ReportSection,
    ResumeCluster,
    SourceType,
    WeeklyResumeBundle,
)
from ._common import _source_specific_hint, format_profile_block, parse_json


_SYSTEM_PROMPT = """당신은 한 명의 백엔드 개발자에게 매주 월요일 #weekly 채널로 전달되는 주간 회고 리포트 작성자입니다.

독자 프로필:
- 레벨(두 페르소나): {levels}
- 경력: {experience_years}년
- 주력 스택 (primary, 80%): {stacks_primary}
- 관심 스택 (interest, 20%): {stacks_interest}
- 학습 목표: {learning_goals}

엄격 규칙:
1. 출력 sections 는 **정확히 2개**: '이력서 재료', '개념 누적 지표'.
2. 본문 어디서도 'AI Agent 작업' (입력에 '분류: agent' 로 표기) 이벤트는 인용하지 말 것.
3. 입력으로 들어오는 `pre_clustered_prs` 의 각 cluster_id 에 대해 STAR 1줄 + 면접 예상 질문 3개를 채움. **추가 클러스터링 금지** — cluster_id 는 그대로 보존.
4. R(결과/임팩트) 을 추정할 수 없으면 `result_summary` 를 **빈 문자열** 로 두면 됨 — 코드가 자체 제외함.
5. 개념 누적 지표 섹션: 입력 `weekly_concepts` 의 각 concept 에 '면접 답변 연습 prompt' 1줄.

응답 형식: JSON only, 코드 펜스 금지.
{{
  "title": "Weekly Report YYYY-MM-DD ~ YYYY-MM-DD",
  "tldr": ["요점 1", "요점 2"],
  "resume_clusters": [
    {{
      "cluster_id": "abcd1234",
      "title": "feat:src",
      "star_bullet": "한 줄 STAR (S/T/A/R)",
      "result_summary": "R 추정 가능한 경우만 채움. 불가능하면 빈 문자열.",
      "interview_questions": ["질문 1", "질문 2", "질문 3"]
    }}
  ],
  "concept_drills": [
    {{"concept": "Spring AOP", "interview_prompt": "면접관: ..."}}
  ],
  "estimated_read_minutes": 정수
}}
"""


_USER_PROMPT = """이번 주({start} ~ {end}) 수집된 backend 이벤트 {count}개:

{events_block}

결정론적으로 미리 클러스터링된 PR 클러스터 (이 cluster_id 를 그대로 사용하세요):
{clusters_block}

이번 주 새로 covered 된 개념 (없으면 빈 리스트):
{concepts_block}

위 입력으로 weekly 회고 리포트를 작성해주세요 (sections 정확히 2개, agent 이벤트 본문 인용 금지)."""


def _empty_week_report(
    cfg: AppConfig, period_start: datetime, period_end: datetime
) -> Report:
    return Report(
        kind="weekly",
        period_start=period_start,
        period_end=period_end,
        title=f"Weekly Report {period_start.strftime('%Y-%m-%d')} ~ {period_end.strftime('%Y-%m-%d')}",
        tldr=["이번 주는 추적된 backend 활동이 적었습니다."],
        sections=[
            ReportSection(
                heading="조용한 한 주",
                body_md=(
                    "이번 주는 backend 분류 이벤트가 거의 없었어요. "
                    "휴식이거나 별도 환경에서 작업한 시간일 수 있습니다."
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
    for ev in events:
        hint = _source_specific_hint(ev)
        blocks.append(f"- [{ev.source.value}] {ev.title}\n  {ev.summary[:160]}{hint}")
    return "\n\n".join(blocks)


def _render_clusters(clusters: list[PrCluster]) -> str:
    if not clusters:
        return "(없음 — 이번 주 backend PR 클러스터 없음)"
    return "\n".join(
        f"- cluster_id={c.cluster_id} title='{c.title}' commits={len(c.event_shas)}"
        for c in clusters
    )


def _render_concepts(concepts: list[tuple[str, str | None, str]]) -> str:
    """concepts_covered_between 결과를 user prompt 용 텍스트로."""
    if not concepts:
        return "(없음)"
    lines: list[str] = []
    for concept, tier, _ts in concepts:
        tier_tag = f" (tier={tier})" if tier else ""
        lines.append(f"- {concept}{tier_tag}")
    return "\n".join(lines)


def _validate_clusters(parsed: dict, allowed_ids: set[str]) -> list[ResumeCluster]:
    """LLM 응답의 resume_clusters 를 검증 + drop. allowed_ids 와 매칭 + result_summary 비면 drop."""
    raw_clusters = parsed.get("resume_clusters") or []
    valid: list[ResumeCluster] = []
    for raw in raw_clusters:
        if not isinstance(raw, dict):
            continue
        cid = raw.get("cluster_id")
        if cid not in allowed_ids:
            continue
        # result_summary 가 빈 문자열 또는 미달이면 drop (AC #18)
        rs = (raw.get("result_summary") or "").strip()
        if not rs:
            continue
        try:
            cluster = ResumeCluster(
                cluster_id=cid,
                title=raw.get("title") or cid,
                star_bullet=(raw.get("star_bullet") or "").strip(),
                result_summary=rs,
                interview_questions=list(raw.get("interview_questions") or []),
            )
        except ValidationError:
            continue
        valid.append(cluster)
    return valid


def _resume_section_body(clusters: list[ResumeCluster]) -> str:
    if not clusters:
        return "(이번 주 LLM 검증 통과한 이력서 후보 없음 — 다음 주 더 풍부한 events 기대)"
    blocks: list[str] = []
    for c in clusters:
        q_block = "\n".join(f"  - {q}" for q in c.interview_questions)
        blocks.append(
            f"**[{c.title}] ({c.cluster_id})**\n"
            f"- STAR: {c.star_bullet}\n"
            f"- 결과: {c.result_summary}\n"
            f"- 예상 면접 질문:\n{q_block}"
        )
    return "\n\n".join(blocks)


def _concept_section_body(parsed: dict) -> str:
    drills = parsed.get("concept_drills") or []
    if not drills:
        return "(이번 주 새로 covered 된 개념 없음)"
    lines: list[str] = []
    for d in drills:
        if not isinstance(d, dict):
            continue
        c = d.get("concept") or ""
        p = d.get("interview_prompt") or ""
        if c and p:
            lines.append(f"- **{c}** — {p}")
    if not lines:
        return "(이번 주 새로 covered 된 개념 없음)"
    return "\n".join(lines)


def write_weekly_report(
    events: list[Event],
    cfg: AppConfig,
    llm: LLM,
    now: datetime,
    db: Database | None = None,
    lookback_days: int = 7,
) -> Report:
    """주간 2-섹션 리포트 (이력서 재료 + 개념 누적 지표).

    db 가 None 이면 (기존 호출 시그니처 호환) 개념 섹션은 빈 리스트로 시도.
    """
    period_end = now
    period_start = now - timedelta(days=lookback_days)

    # 엄격 분류 — file_category == 'backend' 만 (AC #13)
    backend_events = [
        e
        for e in events
        if e.metadata.get(METADATA_KEY_FILE_CATEGORY) == "backend"
    ]
    git_backend = [e for e in backend_events if e.source == SourceType.GIT]

    if not backend_events and not git_backend:
        return _empty_week_report(cfg, period_start, period_end)

    # 1) 결정론적 PR 클러스터링
    clusters = cluster_pr_events(git_backend)
    allowed_ids = {c.cluster_id for c in clusters}

    # 2) 이번 주 covered 개념
    weekly_concepts: list[tuple[str, str | None, str]] = []
    if db is not None:
        weekly_concepts = db.concepts_covered_between(period_start, period_end)

    profile_block = format_profile_block(cfg)
    profile_block["stacks_primary"] = (
        ", ".join(cfg.profile.stacks.primary) or "(미지정)"
    )
    profile_block["stacks_interest"] = (
        ", ".join(cfg.profile.stacks.interest) or "(미지정)"
    )

    system = _SYSTEM_PROMPT.format(**profile_block)
    user = _USER_PROMPT.format(
        start=period_start.strftime("%Y-%m-%d"),
        end=period_end.strftime("%Y-%m-%d"),
        count=len(backend_events),
        events_block=_render_events_simple(backend_events),
        clusters_block=_render_clusters(clusters),
        concepts_block=_render_concepts(weekly_concepts),
    )

    raw = llm.complete(system, user, max_tokens=5000, mock_kind="weekly", json_mode=True)
    parsed = parse_json(raw)

    valid_clusters = _validate_clusters(parsed, allowed_ids)
    # Pydantic bundle wrap (LLM 응답이 모델 단위로도 검증되도록)
    try:
        _ = WeeklyResumeBundle(clusters=valid_clusters)
    except ValidationError:
        valid_clusters = []

    resume_body = _resume_section_body(valid_clusters)
    concept_body = _concept_section_body(parsed)

    sections = [
        ReportSection(heading="이력서 재료", body_md=resume_body),
        ReportSection(heading="개념 누적 지표", body_md=concept_body),
    ]

    title = parsed.get("title") or (
        f"Weekly Report {period_start.strftime('%Y-%m-%d')} ~ "
        f"{period_end.strftime('%Y-%m-%d')}"
    )
    tldr = parsed.get("tldr") or ["이번 주 backend 회고"]

    return Report(
        kind="weekly",
        period_start=period_start,
        period_end=period_end,
        title=title,
        tldr=tldr,
        sections=sections,
        estimated_read_minutes=int(parsed.get("estimated_read_minutes") or 10),
        generated_at=datetime.now(timezone.utc),
        profile_snapshot=cfg.profile.model_dump(),
    )
