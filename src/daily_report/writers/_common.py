"""writers 공용 유틸 — JSON 파싱, 이벤트 렌더링, 프로필 블록 포맷.

daily / weekly / monthly 모두 이 모듈을 import.
프롬프트 자체는 각 writer 가 자기 스타일로 정의한다.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import AppConfig
from ..schemas import Event


def parse_json(raw: str) -> dict:
    """LLM 응답에서 JSON 추출 (코드 펜스/잡음/관용 처리).

    LLM 이 ```json``` 으로 감싸거나 앞뒤에 설명 텍스트를 붙여도
    가장 바깥 { ... } 만 추출. 추가 관용 처리:
      - strict=False : string 안의 raw newline/tab 허용 (json spec 위반이지만 LLM 흔함)
      - 끝이 잘렸을 경우 (max_tokens 초과) 마지막 valid 위치까지 시도
    """
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        s = s[start : end + 1]

    # strict=False : string 값 안에 raw control char (newline 등) 가 있어도 허용
    return json.loads(s, strict=False)


def render_events(events: list[Event]) -> str:
    """Event[] 를 LLM 프롬프트용 markdown 블록으로.

    source 별로 의미 있는 metadata 힌트를 짧게 덧붙임 — 프롬프트 토큰 효율 + 신호 보존.
    """
    if not events:
        return "(이벤트 없음)"

    blocks: list[str] = []
    for ev in events:
        meta_hint = _source_specific_hint(ev)
        blocks.append(
            f"- [{ev.source.value}] {ev.title}\n"
            f"  {ev.summary[:200]}{meta_hint}"
        )
    return "\n\n".join(blocks)


def _source_specific_hint(ev: Event) -> str:
    md = ev.metadata if isinstance(ev.metadata, dict) else {}
    src = ev.source.value

    if src == "git":
        stat = md.get("files_stat") or ""
        if stat:
            last_line = stat.splitlines()[-1] if stat.splitlines() else ""
            if last_line:
                return f"\n  파일변경: {last_line}"
    elif src == "claude_session":
        tools = md.get("tool_call_counts") or {}
        if tools:
            top = sorted(tools.items(), key=lambda x: -x[1])[:3]
            tool_str = ", ".join(f"{n}×{c}" for n, c in top)
            extra = ""
            libs = md.get("library_hints") or []
            if libs:
                extra = f" / 라이브러리: {', '.join(libs[:5])}"
            return f"\n  도구: {tool_str}{extra}"
    elif src == "web":
        label = md.get("label") or ""
        if label:
            return f"\n  소스: {label}"
    elif src == "notion":
        page = md.get("page_label") or ""
        if page:
            return f"\n  Notion: {page}"
    elif src == "pdf":
        fname = md.get("file_name") or ""
        if fname:
            return f"\n  PDF: {fname}"
    return ""


def format_profile_block(cfg: AppConfig) -> dict:
    """system prompt 의 .format() 에 그대로 풀어 쓸 수 있는 키-값."""
    p = cfg.profile
    return {
        "levels": ", ".join(p.levels),
        "experience_years": p.experience_years,
        "current_focus": ", ".join(p.current_focus) or "(미지정)",
        "learning_goals": "; ".join(p.learning_goals) or "(미지정)",
        "weak_areas": ", ".join(p.weak_areas) or "(미지정)",
        "tone": p.tone,
        "daily_read_minutes": p.daily_read_minutes,
        "language": p.language,
    }
