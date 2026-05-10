"""Notion collector — sources.yaml 의 notion.pages + databases 변경분 추출.

각 페이지의 last_edited_time 이 lookback 윈도우 안이면 Event 1개 생성.
top-level blocks 의 text/heading/list/quote/code 만 markdown 으로 직렬화.
중첩 child block 은 재귀하지 않는다 (v0.4 단순화).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..schemas import Event, SourceType


def collect_notion(
    pages: Optional[list[dict]] = None,
    databases: Optional[list[dict]] = None,
    token: Optional[str] = None,
    lookback_hours: int = 24,
    now: Optional[datetime] = None,
    client: Optional[Any] = None,
) -> list[Event]:
    """Notion API 로 수집.

    client 가 주어지면 그것을 사용 (테스트에서 stub 주입), 아니면 token 으로 새로 생성.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if client is None:
        if not token:
            return []
        from notion_client import Client

        client = Client(auth=token)

    cutoff = now - timedelta(hours=lookback_hours)
    events: list[Event] = []

    # Single pages
    for page_cfg in pages or []:
        try:
            page = client.pages.retrieve(page_id=page_cfg["id"])
        except Exception:
            continue
        ev = _page_to_event(page, client, page_cfg.get("label", ""), cutoff, now)
        if ev is not None:
            events.append(ev)

    # Database queries
    for db_cfg in databases or []:
        results = _query_database(client, db_cfg, cutoff)
        for page in results:
            ev = _page_to_event(page, client, db_cfg.get("label", ""), cutoff, now)
            if ev is not None:
                events.append(ev)

    return events


# ─────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────


def _parse_iso(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _query_database(client: Any, db_cfg: dict, cutoff: datetime) -> list[dict]:
    """database 를 last_edited_time >= cutoff 로 필터해 pages 반환."""
    try:
        # 사용자 정의 filter 가 있으면 우선
        custom_filter = db_cfg.get("filter")
        if custom_filter:
            response = client.databases.query(
                database_id=db_cfg["id"], filter=custom_filter
            )
        else:
            response = client.databases.query(
                database_id=db_cfg["id"],
                filter={
                    "timestamp": "last_edited_time",
                    "last_edited_time": {"on_or_after": cutoff.isoformat()},
                },
            )
    except Exception:
        return []
    return response.get("results", []) or []


def _page_to_event(
    page: dict,
    client: Any,
    label: str,
    cutoff: datetime,
    now: datetime,
) -> Optional[Event]:
    last_edited = _parse_iso(page.get("last_edited_time"))
    if last_edited is None or last_edited < cutoff:
        return None

    page_id = page.get("id")
    if not page_id:
        return None

    title = _extract_page_title(page) or "(제목 없음)"
    body = _extract_page_text(page_id, client)

    url = page.get("url") or f"https://notion.so/{str(page_id).replace('-', '')}"

    return Event(
        id=f"notion:{page_id}:{last_edited.isoformat()}",
        source=SourceType.NOTION,
        occurred_at=last_edited,
        collected_at=now,
        title=title[:300],
        summary=(body[:400] if body else title),
        body=body[:4000] if body else None,
        url=url,
        tags=[label] if label else [],
        metadata={
            "page_id": page_id,
            "label": label,
            "last_edited_time": last_edited.isoformat(),
            "extracted_chars": len(body),
        },
    )


def _extract_page_title(page: dict) -> str:
    """page.properties 의 title 타입 속성에서 텍스트 합침."""
    props = page.get("properties") or {}
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            items = prop.get("title") or []
            return "".join(item.get("plain_text", "") for item in items)
    return ""


def _extract_page_text(page_id: str, client: Any, max_blocks: int = 50) -> str:
    try:
        response = client.blocks.children.list(
            block_id=page_id, page_size=max_blocks
        )
    except Exception:
        return ""
    parts: list[str] = []
    for block in response.get("results", []):
        text = _block_to_text(block)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _rich_text_to_plain(items: list[dict]) -> str:
    return "".join(item.get("plain_text", "") for item in items if isinstance(item, dict))


def _block_to_text(block: dict) -> str:
    btype = block.get("type")
    if not btype:
        return ""
    content = block.get(btype) or {}
    rich = content.get("rich_text") or content.get("text") or []
    text = _rich_text_to_plain(rich)

    if btype == "paragraph":
        return text
    if btype.startswith("heading_"):
        try:
            level = int(btype.split("_")[1])
        except (IndexError, ValueError):
            level = 2
        prefix = "#" * max(1, min(level, 6))
        return f"{prefix} {text}"
    if btype == "bulleted_list_item":
        return f"- {text}"
    if btype == "numbered_list_item":
        return f"1. {text}"
    if btype == "to_do":
        checked = content.get("checked", False)
        marker = "[x]" if checked else "[ ]"
        return f"- {marker} {text}"
    if btype == "quote":
        return f"> {text}"
    if btype == "callout":
        return f"💡 {text}"
    if btype == "code":
        lang = content.get("language", "")
        return f"```{lang}\n{text}\n```"
    if btype == "divider":
        return "---"
    return text
