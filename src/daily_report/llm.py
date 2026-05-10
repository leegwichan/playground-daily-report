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
        "tldr": [
            "git_log collector 가 정상 동작했다",
            "writer 가 LLM 없이도 끝까지 통과한다",
            "Discord publisher payload 가 생성됐다",
        ],
        "sections": [
            {
                "heading": "오늘의 작업",
                "body_md": (
                    "## 요약\n"
                    "오늘은 daily-report MVP 슬라이스를 만들었다. "
                    "git_log → daily writer → Discord publisher 한 줄 파이프라인.\n\n"
                    "## 다음 할 일\n"
                    "- 실제 LLM 호출 검증\n"
                    "- 다른 collectors 추가 (notion, claude_sessions, web)\n"
                ),
                "sources": [],
            }
        ],
        "estimated_read_minutes": 3,
    },
    ensure_ascii=False,
)


_MOCK_DEEP_DIVE_JSON = json.dumps(
    {
        "triggered_by": "오늘 작업: Redis 캐시 레이어 리팩토링",
        "concept": "Redis 내부 자료구조 (SDS, ziplist, skiplist)",
        "relates_to_today_work": True,
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
            "이번 주는 daily-report 봇의 v0.1 → v0.3 핵심 파이프라인이 완성됐다",
            "claude_sessions / web_pages 두 collector 가 새로 추가됐고 모두 단위·e2e 테스트 통과",
            "다음 주 우선순위: notion + pdf collector + GitHub Actions cron",
        ],
        "sections": [
            {
                "heading": "이번 주 메이저 진척",
                "body_md": (
                    "## 1. 4-team 파이프라인 안정화\n"
                    "schemas.py 의 데이터 계약이 stage 간 어긋남 없이 통과되도록 정착.\n\n"
                    "## 2. CS 보강 채널 가동\n"
                    "deep_dive writer 가 오늘 작업과 연관된 CS 토픽을 자동 골라 #cs-foundations 로 별도 발송."
                ),
                "sources": [],
            },
            {
                "heading": "패턴 발견",
                "body_md": (
                    "Mock-first 개발 패턴이 잘 맞았다. `--mock-llm` + `--dry-run` 두 플래그로 외부 의존성 0 으로 풀체인 검증."
                ),
                "sources": [],
            },
            {
                "heading": "다음 주 우선순위",
                "body_md": (
                    "1. **notion collector** — Notion API 통합\n"
                    "2. **GitHub Actions cron** — 매일 KST 08:00 자동 트리거"
                ),
                "sources": [],
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
            "이 달의 한 줄: 'Discord 일/주/월 학습 리포트 봇' 0 → 1 완성",
            "메이저 토픽: pipeline 설계 / 멱등성 / mock-first 검증 / GitHub Actions cron",
            "다음 달: 운영 안정화 + 신규 collector 추가",
        ],
        "sections": [
            {
                "heading": "이 달의 메이저 토픽",
                "body_md": (
                    "## 1. 4-team 파이프라인 설계\n"
                    "collectors → processors → writers → publishers 의 명확한 stage 계약.\n\n"
                    "## 2. 멱등성 보장\n"
                    "`PRIMARY KEY (source, id)` + ON CONFLICT UPDATE 로 매 회차 안전 재실행.\n\n"
                    "## 3. Mock-first 개발 사이클\n"
                    "`--mock-llm` + `--dry-run` 으로 외부 의존성 0 으로 풀체인 회귀 검증."
                ),
                "sources": [],
            },
            {
                "heading": "이 달에 늘어난 역량",
                "body_md": (
                    "- Pydantic v2 의 dump/validate round-trip 활용\n"
                    "- SQLite 의 ON CONFLICT UPDATE 패턴\n"
                    "- Discord webhook embed 제약 (10 embeds/메시지, 4096자/desc)\n"
                    "- 의존성 주입을 통한 네트워크 의존 제거"
                ),
                "sources": [],
            },
            {
                "heading": "다음 달 학습 방향",
                "body_md": (
                    "1. Notion API 통합 — 워크스페이스 변경 추적\n"
                    "2. GitHub Actions cron 운영\n"
                    "3. PDF 요약 collector\n"
                    "4. 운영 후 실제 데이터로 deep_dive 프롬프트 튜닝"
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
