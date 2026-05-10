"""SQLite 접근 계층 — 멱등 적재, 리포트 아카이브, CS 개념 추적."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
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
