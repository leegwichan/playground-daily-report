"""Discord webhook publisher — Report 를 Embed 로 직렬화하여 발송."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..schemas import PublishResult, Report


def _utf8_print(text: str) -> None:
    """Windows cp949 콘솔에서도 한글·이모지 등 UTF-8 출력이 깨지지 않도록.

    sys.stdout.buffer 가 있으면 UTF-8 바이트로 직접 쓰고,
    없으면 ASCII backslashreplace 로 폴백.
    """
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    except (AttributeError, OSError):
        sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")

# Discord 제약
_EMBED_DESC_LIMIT = 4096
_EMBED_TITLE_LIMIT = 256
_EMBEDS_PER_MESSAGE = 10


def report_to_embeds(report: Report, color: int) -> list[dict]:
    """Report → Discord Embed 객체 리스트.

    구조:
      embeds[0]   : 메인 (title + tldr 불릿 + 풋터)
      embeds[1..] : 각 섹션 1개씩
      마지막      : CSFoundationsBlock 이 있으면 별도 embed (deep_dive 도 같은 채널일 때만 합침)
    """
    embeds: list[dict] = []

    # 1) 메인 embed — footer 는 report.kind 에 따라 dispatch (AC #19 구조적 보장).
    # weekly / monthly 는 "직접 골라 이력서에 옮길 후보" 표기를 publisher 가 직접 부착해
    # LLM 협조 의존을 제거한다.
    footer_text = f"~{report.estimated_read_minutes}분 읽기"
    if report.kind in ("weekly", "monthly"):
        footer_text += " · 직접 골라 이력서에 옮길 후보"
    embeds.append(
        {
            "title": _truncate(report.title, _EMBED_TITLE_LIMIT),
            "description": "\n".join(f"• {b}" for b in report.tldr),
            "color": color,
            "footer": {"text": footer_text},
            "timestamp": report.generated_at.isoformat(),
        }
    )

    # 2) 섹션 embed (Discord 한 메시지당 최대 10개)
    remaining = _EMBEDS_PER_MESSAGE - len(embeds)
    for section in report.sections[:remaining]:
        embeds.append(
            {
                "title": _truncate(section.heading, _EMBED_TITLE_LIMIT),
                "description": _truncate(section.body_md, _EMBED_DESC_LIMIT),
                "color": color,
            }
        )
    return embeds


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def publish_to_discord(
    report: Report,
    webhook_url: str,
    channel_label: str,
    color: int,
    dry_run: bool = False,
    timeout_seconds: float = 30.0,
) -> PublishResult:
    """Report 를 Discord webhook 으로 POST.

    dry_run=True 면 실제 POST 하지 않고 payload 를 stdout 으로 출력.
    """
    embeds = report_to_embeds(report, color)
    payload = {"embeds": embeds}

    if dry_run:
        _utf8_print(f"\n[dry-run] would POST to channel='{channel_label}':")
        _utf8_print(json.dumps(payload, indent=2, ensure_ascii=False))
        return PublishResult(
            report_kind=report.kind,
            channel_label=channel_label,
            published_at=datetime.now(timezone.utc),
            success=True,
        )

    if not webhook_url:
        return PublishResult(
            report_kind=report.kind,
            channel_label=channel_label,
            published_at=datetime.now(timezone.utc),
            success=False,
            error="missing webhook_url",
        )

    try:
        # ?wait=true 를 붙이면 응답 본문에 message 객체가 포함됨
        url = webhook_url + ("&wait=true" if "?" in webhook_url else "?wait=true")
        resp = httpx.post(url, json=payload, timeout=timeout_seconds)
        resp.raise_for_status()
        message_id: Optional[str] = None
        try:
            message_id = resp.json().get("id")
        except Exception:
            pass
        return PublishResult(
            report_kind=report.kind,
            channel_label=channel_label,
            discord_message_id=message_id,
            published_at=datetime.now(timezone.utc),
            success=True,
        )
    except httpx.HTTPError as e:
        return PublishResult(
            report_kind=report.kind,
            channel_label=channel_label,
            published_at=datetime.now(timezone.utc),
            success=False,
            error=f"{type(e).__name__}: {e}",
        )
