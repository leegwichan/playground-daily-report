"""Pipeline data contracts.

Stage 간 인터페이스를 한 곳에서 정의한다.
collectors → processors → writers → publishers 모든 단계가 이 모듈을 import.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


# ─────────────────────────────────────────────────────────
# File category (Event.metadata convention)
#
# git collector 가 변경 파일 분류 후 dominant category 를 Event.metadata 에 push.
# claude_session collector 는 잠정값을 두고, main._resolve_collector_overlap()
# 이 같은 시간대 git 이벤트가 있으면 git 값으로 덮어쓴다.
# ─────────────────────────────────────────────────────────
FileCategory = Literal["backend", "agent", "mixed", "unknown"]
METADATA_KEY_FILE_CATEGORY = "file_category"


# ─────────────────────────────────────────────────────────
# Source taxonomy
# ─────────────────────────────────────────────────────────
class SourceType(str, Enum):
    NOTION = "notion"
    GIT = "git"
    CLAUDE_SESSION = "claude_session"
    WEB = "web"
    PDF = "pdf"


# ─────────────────────────────────────────────────────────
# Stage 1 output: collectors → processors
# ─────────────────────────────────────────────────────────
class Event(BaseModel):
    """Normalized output from any collector.

    각 collector 는 자신의 SourceType 으로 채워서 list[Event] 반환.
    `id` 는 stable hash 여야 한다 (재실행 시 중복 방지).
    예) git:<commit_sha>, notion:<page_id>:<edited_at_iso>, web:<url_sha1>
    """

    id: str = Field(..., description="Stable hash; deduplication key")
    source: SourceType
    occurred_at: datetime = Field(..., description="이벤트 발생 시각")
    collected_at: datetime = Field(..., description="collector 가 수집한 시각")

    title: str = Field(..., max_length=300)
    summary: str = Field(..., description="2-4 문장, LLM 친화적 요약")
    body: Optional[str] = Field(None, description="전문 (있을 때만)")
    url: Optional[HttpUrl] = None

    tags: list[str] = Field(default_factory=list, description="자동/수동 태그")
    metadata: dict = Field(
        default_factory=dict,
        description="source-specific (commit_hash, page_id, ...)",
    )


# ─────────────────────────────────────────────────────────
# Stage 2 output: processors → writers
# ─────────────────────────────────────────────────────────
SuggestedDepth = Literal["mention", "summarize", "deep_dive"]


class TopicCluster(BaseModel):
    """관련 이벤트를 하나의 주제로 묶음."""

    topic: str = Field(..., description="LLM 으로 추출한 주제명")
    summary: str = Field(..., description="클러스터 전체 요약")
    events: list[Event]
    importance: float = Field(..., ge=0.0, le=1.0)
    suggested_depth: SuggestedDepth
    related_concepts: list[str] = Field(
        default_factory=list,
        description="CS/이론 보강 후보 키워드",
    )


class ProcessedBatch(BaseModel):
    """processors 의 최종 출력."""

    period_start: datetime
    period_end: datetime
    clusters: list[TopicCluster]
    orphan_events: list[Event] = Field(
        default_factory=list,
        description="어느 클러스터에도 묶이지 않은 이벤트",
    )


# ─────────────────────────────────────────────────────────
# Weekly 이력서 재료 — processors/git_pr_clusterer.py 가 만들고 LLM 이 채움
# ─────────────────────────────────────────────────────────
class PrCluster(BaseModel):
    """처리적 PR 클러스터 — git_pr_clusterer 가 결정론적으로 생성.

    LLM 은 이 cluster_id 별 STAR bullet + 면접 질문 3개를 채운다.
    """

    cluster_id: str = Field(..., description="sha256(prefix + top_dir)[:8] — stable across runs")
    title: str = Field(..., description="prefix + top_level_dir (e.g., 'feat:src')")
    event_shas: list[str] = Field(default_factory=list, description="이 cluster 에 속한 git commit sha")


class ResumeCluster(BaseModel):
    """이력서 후보 한 줄 — weekly 의 1섹션 단위.

    result_summary 가 빈 문자열 또는 min_length 미달이면 weekly writer 코드가 자체 drop (AC #18).
    """

    cluster_id: str
    title: str
    star_bullet: str = Field(min_length=10, description="STAR (Situation/Task/Action/Result) 1줄")
    result_summary: str = Field(min_length=10, description="R(결과/임팩트) — 추정 불가 시 빈 문자열 → drop")
    interview_questions: list[str] = Field(min_length=3, max_length=3, description="이 cluster 에 대한 면접 예상 질문 3개")


class WeeklyResumeBundle(BaseModel):
    clusters: list[ResumeCluster] = Field(default_factory=list)


class ConceptDrill(BaseModel):
    """이번 주 covered 개념에 대한 면접 답변 연습 prompt."""

    concept: str
    interview_prompt: str = Field(min_length=10)


class WeeklyConceptDrillBundle(BaseModel):
    drills: list[ConceptDrill] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────
# Stage 3 output: writers → publishers
# ─────────────────────────────────────────────────────────
ReportKind = Literal["daily", "weekly", "monthly", "deep_dive"]


class ReportSection(BaseModel):
    heading: str
    body_md: str = Field(..., description="markdown 본문")
    sources: list[HttpUrl] = Field(default_factory=list)


class CSFoundationsBlock(BaseModel):
    """deep_dive writer 가 만드는 CS 보강 콘텐츠.

    프로필의 levels / weak_areas / learning_goals 와
    오늘의 이벤트에서 트리거된 개념을 결합해 생성.
    여러 levels 가 지정되어 있으면 perspectives 에 각 관점을 분리해 담는다.
    """

    triggered_by: str = Field(..., description="어떤 이벤트/토픽이 트리거했는지")
    concept: str = Field(..., description="다룰 CS 개념 (예: Two-phase locking)")

    # 오늘 작업과 연결됐는지 (false 면 fallback_topics 에서 가져온 것)
    relates_to_today_work: bool = Field(
        ...,
        description="True 면 오늘의 events 와 직접 연결, False 면 fallback_topics",
    )

    # 레벨별 관점 — 같은 개념을 여러 시각으로 통과시킴
    # 키는 profile.levels 의 항목 (예: "backend_jobseeker", "backend_junior")
    # 값은 그 레벨에 특화된 markdown 설명 (1-2 문단)
    # 예) {"backend_jobseeker": "면접에서 ~ 자주 나옴", "backend_junior": "실무에선 ~"}
    perspectives: dict[str, str] = Field(
        default_factory=dict,
        description="레벨별 why_it_matters. profile.levels 항목별로 1개씩.",
    )

    quick_explanation_md: str = Field(
        ...,
        description="5-10 분 분량 markdown 본문 설명 (레벨 무관, 공통)",
    )
    further_reading: list[HttpUrl] = Field(default_factory=list)

    # tier: deep_dive 가 stacks.primary 어휘로 골랐는지 stacks.interest 어휘로 골랐는지.
    # monthly 진도도 audit + cs_concepts_covered.tier 칼럼의 source.
    tier: Optional[Literal["primary", "interest"]] = Field(
        None,
        description="primary (사용자 실 스택) 또는 interest (학습 곁가지). 추정 불가 시 None.",
    )


class Report(BaseModel):
    """writer 의 최종 출력. publisher 가 그대로 직렬화하여 발송."""

    kind: ReportKind
    period_start: datetime
    period_end: datetime
    title: str
    tldr: list[str] = Field(..., min_length=1, max_length=7)
    sections: list[ReportSection]
    cs_foundations: Optional[CSFoundationsBlock] = None
    estimated_read_minutes: int = Field(..., ge=1)
    generated_at: datetime
    profile_snapshot: dict = Field(
        default_factory=dict,
        description="이 리포트 생성 시점의 프로필 스냅샷 (재현성)",
    )


# ─────────────────────────────────────────────────────────
# Stage 4 output: publishers (디스코드 발송 결과)
# ─────────────────────────────────────────────────────────
class PublishResult(BaseModel):
    report_kind: ReportKind
    channel_label: str = Field(..., description="profile.discord.webhook_urls 의 키")
    discord_message_id: Optional[str] = None
    published_at: datetime
    success: bool
    error: Optional[str] = None
