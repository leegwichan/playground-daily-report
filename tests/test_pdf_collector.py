"""collectors/pdf_summary.py 단위 테스트.

pypdf 의존성을 우회하기 위해 text_extractor 를 stub 으로 주입.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from daily_report.collectors.pdf_summary import collect_pdfs
from daily_report.schemas import SourceType


def _touch_pdf(dir_: Path, name: str, content: str = "dummy") -> Path:
    """가짜 .pdf 파일 (텍스트 추출은 stub 가 처리)."""
    p = dir_ / name
    p.write_text(content, encoding="utf-8")
    return p


def test_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert collect_pdfs(tmp_path / "nope") == []


def test_returns_empty_when_no_pdfs(tmp_path: Path) -> None:
    (tmp_path / "other.txt").write_text("not a pdf")
    assert collect_pdfs(tmp_path) == []


def test_collects_pdf_with_stub_extractor(tmp_path: Path) -> None:
    _touch_pdf(tmp_path, "spring-aop.pdf")
    extracted = "Spring AOP 동작 원리에 대한 정리 문서입니다. " * 30

    events = collect_pdfs(
        watch_dir=tmp_path,
        text_extractor=lambda _f: extracted,
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.source == SourceType.PDF
    assert ev.title == "spring-aop"
    assert ev.id.startswith("pdf:")
    assert "Spring AOP" in ev.summary
    assert ev.metadata["file_name"] == "spring-aop.pdf"
    assert ev.metadata["extracted_chars"] == len(extracted)
    assert "pdf" in ev.tags


def test_skips_pdfs_with_no_extractable_text(tmp_path: Path) -> None:
    _touch_pdf(tmp_path, "scanned.pdf")
    events = collect_pdfs(watch_dir=tmp_path, text_extractor=lambda _f: "   ")
    assert events == []


def test_skips_pdfs_when_extractor_raises(tmp_path: Path) -> None:
    _touch_pdf(tmp_path, "broken.pdf")

    def boom(_f: Path) -> str:
        raise RuntimeError("corrupt PDF")

    events = collect_pdfs(watch_dir=tmp_path, text_extractor=boom)
    assert events == []


def test_collects_multiple_pdfs(tmp_path: Path) -> None:
    _touch_pdf(tmp_path, "a.pdf")
    _touch_pdf(tmp_path, "b.pdf")
    _touch_pdf(tmp_path, "c.pdf")
    events = collect_pdfs(
        watch_dir=tmp_path,
        text_extractor=lambda f: f"본문 of {f.name} " * 50,
    )
    assert len(events) == 3
    titles = sorted(ev.title for ev in events)
    assert titles == ["a", "b", "c"]


def test_idempotent_id_for_unchanged_pdf(tmp_path: Path) -> None:
    """같은 파일 (같은 mtime) → 두 번 collect 해도 같은 id."""
    _touch_pdf(tmp_path, "stable.pdf")
    text = "내용 " * 50
    a = collect_pdfs(watch_dir=tmp_path, text_extractor=lambda _f: text)
    b = collect_pdfs(watch_dir=tmp_path, text_extractor=lambda _f: text)
    assert a[0].id == b[0].id


def test_delete_after_processed_removes_file(tmp_path: Path) -> None:
    p = _touch_pdf(tmp_path, "ephemeral.pdf")
    assert p.exists()
    events = collect_pdfs(
        watch_dir=tmp_path,
        delete_after_processed=True,
        text_extractor=lambda _f: "텍스트 " * 50,
    )
    assert len(events) == 1
    assert not p.exists()


def test_delete_after_processed_keeps_file_when_false(tmp_path: Path) -> None:
    p = _touch_pdf(tmp_path, "keep.pdf")
    events = collect_pdfs(
        watch_dir=tmp_path,
        delete_after_processed=False,
        text_extractor=lambda _f: "x" * 100,
    )
    assert len(events) == 1
    assert p.exists()


def test_summary_max_chars_truncates_body(tmp_path: Path) -> None:
    _touch_pdf(tmp_path, "long.pdf")
    long_text = "x" * 10000
    events = collect_pdfs(
        watch_dir=tmp_path,
        summary_max_chars=500,
        text_extractor=lambda _f: long_text,
    )
    assert events[0].body is not None
    assert len(events[0].body) == 500
