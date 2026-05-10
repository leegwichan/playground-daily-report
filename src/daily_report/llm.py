"""Claude API 래퍼. --mock-llm 모드면 anthropic SDK 임포트조차 하지 않는다."""

from __future__ import annotations

import json
from typing import Optional


# mock 모드에서 writer 가 받을 캔드 응답 (Daily Report JSON)
MOCK_DAILY_REPORT_JSON = json.dumps(
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


class LLM:
    """Anthropic Claude 클라이언트. mock=True 면 캔드 응답 반환."""

    def __init__(self, model: str, mock: bool = False, api_key: Optional[str] = None):
        self.model = model
        self.mock = mock
        self._client = None
        if not mock:
            # lazy import — mock 모드면 anthropic 미설치여도 돌아가야 함
            from anthropic import Anthropic

            self._client = Anthropic(api_key=api_key) if api_key else Anthropic()

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        if self.mock:
            return MOCK_DAILY_REPORT_JSON
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # content[0] 가 TextBlock 인 경우. 향후 tool_use 등 확장 시 변경 필요.
        return msg.content[0].text
