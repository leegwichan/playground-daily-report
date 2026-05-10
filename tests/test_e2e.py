"""End-to-end smoke test — main.cli() 풀체인.

temp git repo + temp DB + dry_run + mock LLM 으로 실제 외부 의존성 없이 전체 통과.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from daily_report.main import cli


def _run_cli(repo: Path, db: Path, project_root: Path) -> int:
    return cli(
        [
            "--repo",
            str(repo),
            "--db",
            str(db),
            "--profile",
            str(project_root / "config" / "profile.example.yaml"),
            "--sources",
            str(project_root / "config" / "sources.example.yaml"),
            "--dry-run",
            "--mock-llm",
        ]
    )


def test_full_pipeline_succeeds(
    two_commit_repo: Path, tmp_path: Path, project_root: Path, capsys
) -> None:
    db = tmp_path / "state.db"
    rc = _run_cli(two_commit_repo, db, project_root)
    assert rc == 0, "CLI should exit 0 on success"

    # SQLite 검증
    conn = sqlite3.connect(db)
    try:
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        reports_count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        assert events_count == 2, f"expected 2 events, got {events_count}"
        assert reports_count == 1, f"expected 1 report, got {reports_count}"

        # report payload 가 유효한 JSON 인지
        row = conn.execute("SELECT kind, title, payload_json FROM reports").fetchone()
        assert row[0] == "daily"
        assert row[1]
        import json

        payload = json.loads(row[2])
        assert payload["kind"] == "daily"
        assert payload["profile_snapshot"]["levels"]
    finally:
        conn.close()


def test_idempotent_run_does_not_duplicate_events(
    two_commit_repo: Path, tmp_path: Path, project_root: Path
) -> None:
    db = tmp_path / "state.db"
    assert _run_cli(two_commit_repo, db, project_root) == 0
    assert _run_cli(two_commit_repo, db, project_root) == 0  # 두 번째 실행

    conn = sqlite3.connect(db)
    try:
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert events_count == 2, f"idempotency broken: {events_count} events after 2 runs"
    finally:
        conn.close()


def test_empty_repo_still_succeeds(
    empty_git_repo: Path, tmp_path: Path, project_root: Path
) -> None:
    """커밋이 없어도 '조용한 하루' 리포트로 통과해야 함."""
    db = tmp_path / "state.db"
    rc = _run_cli(empty_git_repo, db, project_root)
    assert rc == 0

    conn = sqlite3.connect(db)
    try:
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        reports_count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        assert events_count == 0
        assert reports_count == 1  # 조용한 하루도 리포트는 발행
    finally:
        conn.close()
