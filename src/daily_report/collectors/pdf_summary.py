"""PDF collector — watch_dir 안의 .pdf 파일을 읽어 Event 로 변환.

원본 PDF 는 commit 하지 않는다 (inbox/pdf/* 가 .gitignore 됨).
추출된 텍스트만 Event.body 에 보존. v0.4 는 LLM 요약 없이 raw 추출 텍스트의 prefix 사용.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..schemas import Event, SourceType


# 기본 text 추출은 pypdf 사용. 테스트는 의존성 주입으로 우회.
TextExtractor = Callable[[Path], str]


def collect_pdfs(
    watch_dir: str | Path,
    delete_after_processed: bool = False,
    summary_max_chars: int = 2000,
    now: Optional[datetime] = None,
    text_extractor: Optional[TextExtractor] = None,
) -> list[Event]:
    """watch_dir 안의 모든 .pdf 를 Event[] 로 변환.

    파일별로 1개 Event. id 는 (filename + mtime) 해시로 안정.
    같은 파일이 같은 mtime 이면 재실행해도 같은 id (DB UPSERT 멱등).
    """
    watch = Path(watch_dir)
    if not watch.is_dir():
        return []
    if now is None:
        now = datetime.now(timezone.utc)
    if text_extractor is None:
        text_extractor = _default_extract_pdf_text

    events: list[Event] = []
    for pdf_file in sorted(watch.glob("*.pdf")):
        try:
            text = text_extractor(pdf_file)
        except Exception:
            continue
        if not text or not text.strip():
            continue

        events.append(_pdf_to_event(pdf_file, text, summary_max_chars, now))

        if delete_after_processed:
            try:
                pdf_file.unlink()
            except OSError:
                pass

    return events


# ─────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────


def _pdf_to_event(
    pdf_file: Path,
    text: str,
    summary_max_chars: int,
    now: datetime,
) -> Event:
    stat = pdf_file.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    # stable id: 파일명 + mtime → SHA1 prefix
    fingerprint = f"{pdf_file.name}:{mtime.isoformat()}"
    file_hash = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]

    summary = " ".join(text.split())[:400]
    body = text[:summary_max_chars]

    return Event(
        id=f"pdf:{file_hash}",
        source=SourceType.PDF,
        occurred_at=mtime,
        collected_at=now,
        title=pdf_file.stem[:300],
        summary=summary,
        body=body,
        url=None,
        tags=["pdf"],
        metadata={
            "file_name": pdf_file.name,
            "file_size_bytes": stat.st_size,
            "file_mtime": mtime.isoformat(),
            "extracted_chars": len(text),
        },
    )


def _default_extract_pdf_text(pdf_file: Path) -> str:
    """pypdf 로 모든 페이지 텍스트 합침. 페이지별 실패는 무시."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_file))
    except Exception:
        return ""
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(parts).strip()
