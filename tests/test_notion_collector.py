"""collectors/notion.py 단위 테스트.

notion-client SDK 호출을 stub 으로 대체해 네트워크 없이 검증.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from daily_report.collectors.notion import (
    _block_to_text,
    _extract_page_title,
    collect_notion,
)
from daily_report.schemas import SourceType


# ─── stub Notion client ────────────────────────────────


class _StubPages:
    def __init__(self, by_id: dict[str, dict]):
        self._by_id = by_id

    def retrieve(self, page_id: str) -> dict:
        if page_id not in self._by_id:
            raise KeyError(page_id)
        return self._by_id[page_id]


class _StubDatabases:
    def __init__(self, by_id: dict[str, list[dict]]):
        self._by_id = by_id

    def query(self, database_id: str, **_kw: Any) -> dict:
        return {"results": self._by_id.get(database_id, [])}


class _StubBlocksChildren:
    def __init__(self, by_page: dict[str, list[dict]]):
        self._by_page = by_page

    def list(self, block_id: str, **_kw: Any) -> dict:
        return {"results": self._by_page.get(block_id, [])}


class _StubBlocks:
    def __init__(self, children: _StubBlocksChildren):
        self.children = children


class _StubClient:
    def __init__(
        self,
        pages: dict[str, dict],
        databases: dict[str, list[dict]],
        page_blocks: dict[str, list[dict]],
    ):
        self.pages = _StubPages(pages)
        self.databases = _StubDatabases(databases)
        self.blocks = _StubBlocks(_StubBlocksChildren(page_blocks))


# ─── helpers ────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _page(
    page_id: str,
    title: str,
    last_edited: datetime,
) -> dict:
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id.replace('-', '')}",
        "last_edited_time": last_edited.isoformat().replace("+00:00", "Z"),
        "properties": {
            "Name": {
                "type": "title",
                "title": [{"plain_text": title}],
            }
        },
    }


def _para_block(text: str) -> dict:
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": text}]},
    }


# ─── tests ──────────────────────────────────────────────


def test_returns_empty_when_no_pages_or_databases() -> None:
    assert collect_notion(client=_StubClient({}, {}, {})) == []


def test_returns_empty_when_no_token_and_no_client() -> None:
    assert collect_notion(pages=[{"id": "x"}], token=None) == []


def test_collects_page_within_lookback() -> None:
    now = _now()
    page_id = "abc-123"
    p = _page(page_id, "스터디 노트", last_edited=now - timedelta(hours=2))
    blocks = [_para_block("오늘 학습한 내용")]
    client = _StubClient(
        pages={page_id: p}, databases={}, page_blocks={page_id: blocks}
    )

    events = collect_notion(
        pages=[{"id": page_id, "label": "Study"}],
        client=client,
        now=now,
        lookback_hours=24,
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.source == SourceType.NOTION
    assert ev.title == "스터디 노트"
    assert "오늘 학습한 내용" in (ev.body or "")
    assert "Study" in ev.tags
    assert ev.id.startswith(f"notion:{page_id}:")


def test_skips_page_outside_lookback() -> None:
    now = _now()
    page_id = "old-page"
    p = _page(page_id, "오래된", last_edited=now - timedelta(days=10))
    client = _StubClient({page_id: p}, {}, {page_id: []})
    events = collect_notion(pages=[{"id": page_id}], client=client, now=now)
    assert events == []


def test_collects_database_query_results() -> None:
    now = _now()
    db_id = "db-xyz"
    p1 = _page("p1", "TIL 1", now - timedelta(hours=1))
    p2 = _page("p2", "TIL 2", now - timedelta(hours=3))
    client = _StubClient(
        pages={},
        databases={db_id: [p1, p2]},
        page_blocks={"p1": [_para_block("내용 1")], "p2": [_para_block("내용 2")]},
    )
    events = collect_notion(
        databases=[{"id": db_id, "label": "TIL"}],
        client=client,
        now=now,
    )
    assert len(events) == 2
    titles = sorted(ev.title for ev in events)
    assert titles == ["TIL 1", "TIL 2"]


def test_extract_page_title() -> None:
    assert _extract_page_title({"properties": {}}) == ""
    assert _extract_page_title({}) == ""
    p = {
        "properties": {
            "AnyName": {
                "type": "title",
                "title": [{"plain_text": "Hello "}, {"plain_text": "World"}],
            }
        }
    }
    assert _extract_page_title(p) == "Hello World"


def test_block_to_text_supports_common_types() -> None:
    rt = lambda t: {"rich_text": [{"plain_text": t}]}
    assert _block_to_text({"type": "paragraph", "paragraph": rt("hi")}) == "hi"
    assert (
        _block_to_text({"type": "heading_2", "heading_2": rt("title")}) == "## title"
    )
    assert (
        _block_to_text({"type": "bulleted_list_item", "bulleted_list_item": rt("a")})
        == "- a"
    )
    assert _block_to_text({"type": "quote", "quote": rt("q")}) == "> q"
    assert (
        _block_to_text({"type": "code", "code": {"rich_text": [{"plain_text": "x"}], "language": "py"}})
        == "```py\nx\n```"
    )
    assert _block_to_text({"type": "divider", "divider": {}}) == "---"


def test_idempotent_id_for_same_edit_time() -> None:
    """같은 페이지를 같은 last_edited_time 으로 두 번 → 같은 id."""
    now = _now()
    pid = "stable"
    edit_time = now - timedelta(hours=1)
    p = _page(pid, "t", last_edited=edit_time)
    client = _StubClient({pid: p}, {}, {pid: []})

    a = collect_notion(pages=[{"id": pid}], client=client, now=now)
    b = collect_notion(pages=[{"id": pid}], client=client, now=now)
    assert a[0].id == b[0].id
