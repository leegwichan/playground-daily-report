"""Web page collector — sources.yaml 의 web_pages.feeds + one_off URL 스크래핑.

각 URL 1개를 1개 Event 로 변환. trafilatura 의 bare_extraction 으로 본문/제목 추출.

설계 결정:
- feeds: 매일 다시 수집 (frequency 무시 — 일단 daily 만)
- one_off: added_at + ttl_days 경과 시 자동 제외
- 같은 URL 같은 날 = 같은 id (멱등). 다음날 재수집 시 새 Event.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from ..schemas import Event, SourceType


def collect_web_pages(
    feeds: Optional[list[dict]] = None,
    one_off: Optional[list[dict]] = None,
    now: Optional[datetime] = None,
    fetcher: Optional[Any] = None,
    extractor: Optional[Any] = None,
) -> list[Event]:
    """sources.yaml > web_pages 의 feeds + one_off URL 들을 스크래핑.

    fetcher / extractor 는 테스트용 의존성 주입 — 기본은 trafilatura.fetch_url / bare_extraction.
    실제 네트워크 없이 단위 테스트 가능하도록 분리.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if fetcher is None or extractor is None:
        # lazy import — 테스트에서는 둘 다 주입해서 trafilatura 의존을 우회 가능
        import trafilatura

        if fetcher is None:
            fetcher = trafilatura.fetch_url
        if extractor is None:
            extractor = lambda html: trafilatura.bare_extraction(html, with_metadata=True)

    events: list[Event] = []

    for entry in feeds or []:
        ev = _build_event(
            url=entry["url"],
            label=entry.get("label", ""),
            kind="feed",
            now=now,
            fetcher=fetcher,
            extractor=extractor,
        )
        if ev is not None:
            events.append(ev)

    for entry in one_off or []:
        if _is_expired(entry, now):
            continue
        ev = _build_event(
            url=entry["url"],
            label=entry.get("note") or entry.get("label", ""),
            kind="one_off",
            now=now,
            fetcher=fetcher,
            extractor=extractor,
        )
        if ev is not None:
            events.append(ev)

    return events


# ─────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────


def _is_expired(entry: dict, now: datetime) -> bool:
    """one_off 엔트리가 added_at + ttl_days 를 지났는지."""
    added_at_str = entry.get("added_at")
    if not added_at_str:
        return False
    try:
        added = datetime.fromisoformat(str(added_at_str))
    except ValueError:
        return False
    if added.tzinfo is None:
        added = added.replace(tzinfo=timezone.utc)
    ttl_days = int(entry.get("ttl_days", 7))
    age_days = (now - added).total_seconds() / 86400
    return age_days > ttl_days


def _doc_field(doc: Any, key: str) -> Any:
    """Document 객체 또는 dict 양쪽에서 필드 안전 추출."""
    if isinstance(doc, dict):
        return doc.get(key)
    return getattr(doc, key, None)


def _shorten(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _build_event(
    url: str,
    label: str,
    kind: str,
    now: datetime,
    fetcher: Any,
    extractor: Any,
) -> Optional[Event]:
    try:
        html = fetcher(url)
    except Exception:
        return None
    if not html:
        return None

    try:
        doc = extractor(html)
    except Exception:
        return None
    if doc is None:
        return None

    title = _doc_field(doc, "title") or label or url
    text = _doc_field(doc, "text") or ""
    if not text.strip():
        # 본문 없으면 의미 없음
        return None

    author = _doc_field(doc, "author") or ""
    pub_date = _doc_field(doc, "date") or ""

    # stable id: URL hash + 수집 날짜 (당일 재실행은 같은 id 로 멱등)
    url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    event_id = f"web:{url_hash}:{now.date().isoformat()}"

    summary_lines = []
    if author:
        summary_lines.append(f"저자: {author}")
    if pub_date:
        summary_lines.append(f"발행: {pub_date}")
    summary_lines.append(_shorten(text, 400))
    summary = "\n".join(summary_lines)

    tags: list[str] = [kind]
    if label and label != title:
        tags.append(label)

    return Event(
        id=event_id,
        source=SourceType.WEB,
        occurred_at=now,
        collected_at=now,
        title=_shorten(str(title), 300),
        summary=summary,
        body=text[:4000] or None,
        url=url,
        tags=tags,
        metadata={
            "kind": kind,
            "label": label,
            "author": author,
            "publish_date": pub_date,
            "scraped_chars": len(text),
        },
    )
