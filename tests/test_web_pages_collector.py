"""collectors/web_pages.py 단위 테스트.

trafilatura 의 fetch_url / bare_extraction 을 의존성 주입으로 대체해 네트워크 없이 검증.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from daily_report.collectors.web_pages import (
    _is_expired,
    collect_web_pages,
)
from daily_report.schemas import SourceType


class _StubDoc:
    """trafilatura.Document 흉내."""

    def __init__(self, title: str, text: str, author: str = "", date: str = ""):
        self.title = title
        self.text = text
        self.author = author
        self.date = date


def _stub_fetcher(html_by_url: dict[str, str]):
    def f(url: str) -> str | None:
        return html_by_url.get(url)
    return f


def _stub_extractor(doc_by_html: dict[str, _StubDoc]):
    def f(html: str) -> _StubDoc | None:
        return doc_by_html.get(html)
    return f


def test_collects_feed_url() -> None:
    html = "<html>...</html>"
    doc = _StubDoc(title="블로그 글", text="이것은 본문입니다. " * 30)
    events = collect_web_pages(
        feeds=[{"url": "https://example.com/post-1", "label": "ExampleBlog"}],
        one_off=[],
        now=datetime(2026, 5, 10, tzinfo=timezone.utc),
        fetcher=_stub_fetcher({"https://example.com/post-1": html}),
        extractor=_stub_extractor({html: doc}),
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.source == SourceType.WEB
    assert ev.title == "블로그 글"
    assert ev.id.startswith("web:")
    assert ev.id.endswith(":2026-05-10")
    assert "feed" in ev.tags
    assert "ExampleBlog" in ev.tags
    assert str(ev.url) == "https://example.com/post-1"


def test_skips_when_no_text() -> None:
    html = "<html></html>"
    doc = _StubDoc(title="empty", text="   ")
    events = collect_web_pages(
        feeds=[{"url": "https://x.com/y"}],
        one_off=[],
        fetcher=_stub_fetcher({"https://x.com/y": html}),
        extractor=_stub_extractor({html: doc}),
    )
    assert events == []


def test_skips_when_fetch_returns_none() -> None:
    events = collect_web_pages(
        feeds=[{"url": "https://dead.example/x"}],
        one_off=[],
        fetcher=_stub_fetcher({}),  # 빈 dict → None 반환
        extractor=_stub_extractor({}),
    )
    assert events == []


def test_one_off_within_ttl_collected() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    added = (now - timedelta(days=3)).date().isoformat()
    html = "<html>...</html>"
    doc = _StubDoc(title="기사", text="본문 " * 50)
    events = collect_web_pages(
        feeds=[],
        one_off=[
            {
                "url": "https://example.com/article",
                "added_at": added,
                "ttl_days": 7,
                "note": "트랜스포머 정리",
            }
        ],
        now=now,
        fetcher=_stub_fetcher({"https://example.com/article": html}),
        extractor=_stub_extractor({html: doc}),
    )
    assert len(events) == 1
    assert "one_off" in events[0].tags
    assert "트랜스포머 정리" in events[0].tags


def test_one_off_expired_excluded() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    added = (now - timedelta(days=20)).date().isoformat()
    events = collect_web_pages(
        feeds=[],
        one_off=[
            {
                "url": "https://example.com/old",
                "added_at": added,
                "ttl_days": 7,
            }
        ],
        now=now,
        fetcher=_stub_fetcher({"https://example.com/old": "<html>...</html>"}),
        extractor=_stub_extractor({"<html>...</html>": _StubDoc("t", "x" * 100)}),
    )
    assert events == []


def test_idempotent_id_for_same_day(monkeypatch) -> None:
    """같은 URL 같은 날짜 → 같은 id (DB UPSERT 가능)."""
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    html = "<html>...</html>"
    doc = _StubDoc(title="hello", text="contents " * 30)
    args = dict(
        feeds=[{"url": "https://example.com/p"}],
        one_off=[],
        now=now,
        fetcher=_stub_fetcher({"https://example.com/p": html}),
        extractor=_stub_extractor({html: doc}),
    )
    a = collect_web_pages(**args)
    b = collect_web_pages(**args)
    assert a[0].id == b[0].id


def test_metadata_carries_author_and_date() -> None:
    html = "<html>...</html>"
    doc = _StubDoc(
        title="Spring AOP",
        text="본문 " * 100,
        author="홍길동",
        date="2026-05-09",
    )
    events = collect_web_pages(
        feeds=[{"url": "https://x/y"}],
        one_off=[],
        fetcher=_stub_fetcher({"https://x/y": html}),
        extractor=_stub_extractor({html: doc}),
    )
    ev = events[0]
    assert ev.metadata["author"] == "홍길동"
    assert ev.metadata["publish_date"] == "2026-05-09"
    assert "저자: 홍길동" in ev.summary
    assert "발행: 2026-05-09" in ev.summary


def test_is_expired_helper() -> None:
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    assert _is_expired({"added_at": "2026-05-01", "ttl_days": 7}, now) is True
    assert _is_expired({"added_at": "2026-05-08", "ttl_days": 7}, now) is False
    assert _is_expired({"added_at": "2026-05-10"}, now) is False  # 기본 ttl=7
    assert _is_expired({}, now) is False  # added_at 없으면 만료 안 됨
