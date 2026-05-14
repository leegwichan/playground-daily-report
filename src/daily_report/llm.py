"""Google Gemini API 래퍼. --mock-llm 모드면 google-genai SDK 임포트조차 하지 않는다.

무료 티어를 쓰기 위해 Anthropic Claude → Google Gemini 로 전환됨 (전 v0.5).
공개 인터페이스 (`LLM(model, mock).complete(system, user, max_tokens, mock_kind=...)`)
는 동일하게 유지하여 writers 코드는 변경 불필요.
"""

from __future__ import annotations

import json
from typing import Optional


# mock 모드에서 writer 가 받을 캔드 응답들. mock_kind 로 선택.
_MOCK_DAILY_REPORT_JSON = json.dumps(
    {
        "title": "Daily Report (mock)",
        "tldr": ["오늘 키워드 1줄"],
        "sections": [
            {
                "heading": "Spring 트랜잭션 전파",
                "body_md": (
                    "어제 cache 스켈레톤 → 오늘 Spring @Transactional 전파 옵션 분기. "
                    "REQUIRES_NEW 는 새 세션·새 connection 을 강제하므로 outer commit/rollback 과 분리된다. "
                    "InnoDB next-key lock 의 영향 범위가 짧아지는 이점이 있다."
                ),
                "sources": [],
            }
        ],
        "estimated_read_minutes": 1,
    },
    ensure_ascii=False,
)


_MOCK_DEEP_DIVE_JSON = json.dumps(
    {
        "triggered_by": "오늘 작업: Redis 캐시 레이어 리팩토링",
        "concept": "Redis 내부 자료구조 (SDS, ziplist, skiplist)",
        "relates_to_today_work": True,
        "tier": "primary",
        "perspectives": {
            "backend_jobseeker": (
                "면접에서 'Redis 가 왜 빠른가' 질문이 나오면 단순히 'in-memory' 라고만 답하는 후보가 많다. "
                "SDS (Simple Dynamic String) 의 length-prefix 와 ziplist→hashtable 변환 임계점을 언급하면 "
                "기본기 깊이로 차별화된다."
            ),
            "backend_junior": (
                "실무에서 hash 자료형의 메모리 사용량을 튜닝할 때 hash-max-ziplist-entries / hash-max-ziplist-value "
                "설정을 알면 RAM 비용을 크게 줄일 수 있다. 작은 hash 는 ziplist (배열) 로, 임계점 넘으면 hashtable 로 "
                "전환되는 동작을 직접 INFO memory 로 검증해보면 좋다."
            ),
        },
        "quick_explanation_md": (
            "## 1. Redis 가 자료구조 서버인 이유\n"
            "Redis 가 단순한 key-value 저장소가 아닌 이유는 *값* 자체가 5+ 가지 자료구조 (string/list/hash/set/zset) 로 "
            "노출되기 때문. 각 타입은 사용 패턴에 따라 내부 구현을 자동 전환한다.\n\n"
            "## 2. SDS (Simple Dynamic String)\n"
            "Redis 의 string 은 C 의 null-terminated string 이 아니다. SDS 는 length-prefix 구조 (`len`, `free`, `buf`) 로:\n"
            "- O(1) strlen\n"
            "- 버퍼 오버플로우 방지\n"
            "- binary-safe\n\n"
            "## 3. ziplist vs hashtable\n"
            "작은 hash/list/zset 은 ziplist (연속 메모리 압축 배열) 로 저장 → 메모리 효율↑, 조회 O(N).\n"
            "임계점 넘으면 hashtable 로 자동 변환 → 조회 O(1), 메모리↑.\n"
            "임계점은 `hash-max-ziplist-entries` (기본 128), `hash-max-ziplist-value` (기본 64 bytes) 로 설정.\n\n"
            "## 4. skiplist (sorted set 의 비밀)\n"
            "ZADD/ZRANGE 가 빠른 이유는 zset 이 hashtable + skiplist 두 구조를 동시에 유지하기 때문.\n"
            "skiplist 는 평균 O(log N) 조회, 구현이 balanced tree 보다 단순해서 락 분할에 유리."
        ),
        "further_reading": [
            "https://redis.io/docs/latest/develop/reference/internals/",
            "https://github.com/redis/redis/blob/unstable/src/sds.h",
        ],
    },
    ensure_ascii=False,
)


_MOCK_WEEKLY_REPORT_JSON = json.dumps(
    {
        "title": "Weekly Report (mock)",
        "tldr": [
            "이번 주 backend PR 클러스터 2개 — feat:src, fix:src",
            "JPA dirty checking 과 Spring 트랜잭션 전파 두 개념 covered",
        ],
        "resume_clusters": [
            {
                "cluster_id": "deadbeef",
                "title": "feat:src",
                "star_bullet": "S: 캐시 조회 RT 가 평균 120ms / T: 50ms 미만으로 / A: Redis SDS + ziplist 구간 튜닝 / R: P95 35ms",
                "result_summary": "P95 35ms 달성 — 기존 대비 70% 단축",
                "interview_questions": [
                    "Redis hash 의 ziplist → hashtable 임계점은 무엇으로 정해지나?",
                    "캐시 일관성 모델 중 Write-Through 와 Cache-Aside 의 trade-off?",
                    "TTL 만료와 active expire / passive expire 의 차이?",
                ],
            },
            {
                "cluster_id": "cafebabe",
                "title": "fix:src",
                "star_bullet": "S: 트랜잭션 전파 옵션 오용 / T: REQUIRES_NEW 와 REQUIRED 충돌 해소 / A: 분리된 자원 경계 도입 / R: 데드락 0건",
                "result_summary": "데드락 발생 0건으로 안정화",
                "interview_questions": [
                    "@Transactional 의 propagation 7가지 중 REQUIRES_NEW 가 새 connection 을 요구하는 이유는?",
                    "InnoDB next-key lock 이 트랜잭션 격리 수준과 어떻게 상호작용하나?",
                    "롤백 시 영속성 컨텍스트의 dirty checking 은 어떻게 무효화되나?",
                ],
            },
        ],
        "concept_drills": [
            {
                "concept": "Spring 트랜잭션 전파",
                "interview_prompt": "면접관: REQUIRES_NEW 를 잘못 썼을 때 어떤 장애가 발생할 수 있나요?",
            },
            {
                "concept": "JPA dirty checking",
                "interview_prompt": "면접관: dirty checking 비활성화로 성능을 올리고 싶을 때 어떤 옵션이 있나요?",
            },
        ],
        "estimated_read_minutes": 10,
    },
    ensure_ascii=False,
)


_MOCK_MONTHLY_REPORT_JSON = json.dumps(
    {
        "title": "Monthly Report (mock) 2026-05",
        "tldr": [
            "이 달 backend 메이저 테마 3개 정리",
            "first_seen=4 / revisit=2 / primary:interest=5:1",
            "다음 달은 interest 비중을 늘려 80:20 균형 회복",
        ],
        "sections": [
            {
                "heading": "메이저 테마 (재클러스터링)",
                "body_md": (
                    "## 1. Spring 트랜잭션 전파 & InnoDB lock\n"
                    "STAR: @Transactional REQUIRES_NEW 오용 → 자원 경계 분리로 데드락 0건.\n"
                    "면접 질문: propagation 7가지 / next-key lock / 영속성 컨텍스트 dirty checking 무효화.\n\n"
                    "## 2. JVM GC & Redis 내부 구조\n"
                    "STAR: heap dump 분석으로 G1 → ZGC 전환 결정. 캐시 RT P95 35ms.\n"
                    "면접 질문: ziplist 임계점 / GC pause 비교 / Redis active expire."
                ),
                "sources": [],
            },
            {
                "heading": "진도도 지표",
                "body_md": (
                    "- first_seen_count: 4 (이번 달 처음 등장한 개념)\n"
                    "- revisit_count   : 2\n"
                    "- primary:interest = 5:1 — primary 비중 83% (80% 근접)\n\n"
                    "primary 가 살짝 우세하지만 80:20 범위 내."
                ),
                "sources": [],
            },
            {
                "heading": "다음 달 학습 방향",
                "body_md": (
                    "1. Kotlin coroutine structured concurrency (interest 보강)\n"
                    "2. Spring + Kotlin coroutine 통합 (WebFlux) (interest)\n"
                    "3. AWS ECS Fargate task definition 깊이 (primary)\n"
                    "→ interest 비중을 늘려 80:20 균형 회복 권장 (read-only audit; deep_dive 가 enforce)."
                ),
                "sources": [],
            },
        ],
        "estimated_read_minutes": 15,
    },
    ensure_ascii=False,
)


_MOCK_RESPONSES = {
    "daily": _MOCK_DAILY_REPORT_JSON,
    "deep_dive": _MOCK_DEEP_DIVE_JSON,
    "weekly": _MOCK_WEEKLY_REPORT_JSON,
    "monthly": _MOCK_MONTHLY_REPORT_JSON,
}


class LLM:
    """Google Gemini 클라이언트. mock=True 면 캔드 응답 반환.

    api_key 미지정 시 google-genai 가 GOOGLE_API_KEY (또는 GEMINI_API_KEY) 환경변수 자동 사용.
    """

    def __init__(self, model: str, mock: bool = False, api_key: Optional[str] = None):
        self.model = model
        self.mock = mock
        self._client = None
        if not mock:
            # lazy import — mock 모드면 google-genai 미설치여도 돌아가야 함
            from google import genai

            self._client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4000,
        *,
        mock_kind: str = "daily",
        json_mode: bool = False,
    ) -> str:
        """LLM 호출.

        json_mode=True 면 Gemini 의 native structured output (response_mime_type=application/json)
        을 켜서 *유효한 JSON* 만 반환하도록 강제. 멀티라인 문자열 안의 따옴표/줄바꿈으로
        JSON 이 깨지는 문제를 근본 차단.
        """
        if self.mock:
            if mock_kind not in _MOCK_RESPONSES:
                raise ValueError(f"Unknown mock_kind: {mock_kind!r}")
            return _MOCK_RESPONSES[mock_kind]

        # google-genai: lazy import types only when needed
        from google.genai import types

        config_kwargs: dict = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            # writers 가 모두 JSON 응답을 강제 — 안전을 위해 약간 낮은 온도
            "temperature": 0.4,
        }
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        response = self._client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        # response.text 가 None 일 가능성 (안전 필터 차단 등) → 빈 문자열로
        return response.text or ""
