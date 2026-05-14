"""SQLite 접근 계층 — 멱등 적재, 리포트 아카이브, CS 개념 추적."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from .schemas import Event, Report

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "schema.sql"


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(ddl)
            # idempotent migration — tier 칼럼은 schema.sql 에 추가되었지만
            # 기존 sqlite 파일은 ALTER TABLE 로만 받아 가능. 이미 있으면 OperationalError 무시.
            # (NB2: schema.sql ↔ _init_schema 가 함께 lockstep).
            try:
                conn.execute(
                    "ALTER TABLE cs_concepts_covered ADD COLUMN tier TEXT"
                )
            except sqlite3.OperationalError:
                pass  # duplicate column 또는 이미 추가됨

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_events(self, events: list[Event]) -> int:
        if not events:
            return 0
        rows = [
            (
                ev.id,
                ev.source.value,
                ev.occurred_at.isoformat(),
                ev.collected_at.isoformat(),
                ev.title,
                ev.summary,
                ev.body,
                str(ev.url) if ev.url else None,
                json.dumps(ev.tags, ensure_ascii=False),
                json.dumps(ev.metadata, ensure_ascii=False),
            )
            for ev in events
        ]
        with self.conn() as conn:
            conn.executemany(
                """
                INSERT INTO events
                  (id, source, occurred_at, collected_at, title, summary, body, url, tags, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, id) DO UPDATE SET
                  occurred_at  = excluded.occurred_at,
                  collected_at = excluded.collected_at,
                  title        = excluded.title,
                  summary      = excluded.summary,
                  body         = excluded.body,
                  url          = excluded.url,
                  tags         = excluded.tags,
                  metadata     = excluded.metadata
                """,
                rows,
            )
        return len(rows)

    def save_report(
        self,
        report: Report,
        channel_label: str,
        message_id: Optional[str] = None,
    ) -> int:
        with self.conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO reports
                  (kind, period_start, period_end, title, payload_json,
                   discord_message_id, discord_channel_label, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, period_start, period_end) DO UPDATE SET
                  payload_json          = excluded.payload_json,
                  discord_message_id    = excluded.discord_message_id,
                  discord_channel_label = excluded.discord_channel_label,
                  published_at          = excluded.published_at
                """,
                (
                    report.kind,
                    report.period_start.isoformat(),
                    report.period_end.isoformat(),
                    report.title,
                    report.model_dump_json(),
                    message_id,
                    channel_label,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cur.lastrowid or 0

    def count_events(self) -> int:
        with self.conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def count_reports(self) -> int:
        with self.conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]

    # ─────────────────────────────────────────────────────────
    # CS 개념 추적 (deep_dive 반복 방지 + tier 분배 audit)
    # ─────────────────────────────────────────────────────────
    def recently_covered_concepts(self, within_days: int) -> list[str]:
        """min_days_between_repeat 내에 이미 다룬 개념 목록.

        within_days 일수 안에 last_covered_at 가 있는 개념들을 반환.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        with self.conn() as conn:
            rows = conn.execute(
                "SELECT concept FROM cs_concepts_covered WHERE last_covered_at >= ? ORDER BY last_covered_at DESC",
                (cutoff,),
            ).fetchall()
            return [r[0] for r in rows]

    def recently_covered_concepts_with_meta(
        self, within_days: int
    ) -> list[tuple[str, datetime]]:
        """(concept, last_covered_at) 튜플 리스트. deep_dive 의 (A) 복습 link 후보 검색용."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        with self.conn() as conn:
            rows = conn.execute(
                "SELECT concept, last_covered_at FROM cs_concepts_covered "
                "WHERE last_covered_at >= ? ORDER BY last_covered_at DESC",
                (cutoff,),
            ).fetchall()
        result: list[tuple[str, datetime]] = []
        for r in rows:
            try:
                ts = datetime.fromisoformat(r[1])
            except (ValueError, TypeError):
                continue
            result.append((r[0], ts))
        return result

    def tier_distribution(self, within_days: int) -> dict[str, int]:
        """최근 N일의 tier 카운트. monthly 진도도 지표 + deep_dive 분배 추천에 사용.

        Returns:
            {'primary': int, 'interest': int, 'unknown': int}
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        result = {"primary": 0, "interest": 0, "unknown": 0}
        with self.conn() as conn:
            rows = conn.execute(
                "SELECT tier, COUNT(*) FROM cs_concepts_covered "
                "WHERE last_covered_at >= ? GROUP BY tier",
                (cutoff,),
            ).fetchall()
        for tier, n in rows:
            if tier in ("primary", "interest"):
                result[tier] = n
            else:
                result["unknown"] += n
        return result

    def concepts_covered_between(
        self, start: datetime, end: datetime
    ) -> list[tuple[str, Optional[str], str]]:
        """주어진 기간 [start, end) 안에 covered 된 개념 — weekly/monthly 용.

        Returns:
            (concept, tier, last_covered_at_iso) 튜플 리스트.
        """
        with self.conn() as conn:
            rows = conn.execute(
                "SELECT concept, tier, last_covered_at FROM cs_concepts_covered "
                "WHERE last_covered_at >= ? AND last_covered_at < ? "
                "ORDER BY last_covered_at DESC",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def mark_concept_covered(
        self,
        concept: str,
        report_id: Optional[int] = None,
        tier: Optional[str] = None,
    ) -> None:
        """개념을 다뤘다고 기록.

        - 신규 행 : INSERT (tier 도 함께).
        - 기존 행 : last_covered_at + times_covered+1 갱신.
                    tier 는 새 값이 있으면 갱신, 없으면 기존 값 유지 (COALESCE).
        """
        if tier is not None and tier not in ("primary", "interest"):
            raise ValueError(f"invalid tier: {tier!r}")
        now = datetime.now(timezone.utc).isoformat()
        with self.conn() as conn:
            conn.execute(
                """
                INSERT INTO cs_concepts_covered (concept, last_covered_at, times_covered, last_report_id, tier)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(concept) DO UPDATE SET
                  last_covered_at = excluded.last_covered_at,
                  times_covered   = times_covered + 1,
                  last_report_id  = excluded.last_report_id,
                  tier            = COALESCE(excluded.tier, tier)
                """,
                (concept, now, report_id, tier),
            )
