"""End-to-end smoke test — main.cli() 풀체인.

temp git repo + temp DB + dry_run + mock LLM 으로 실제 외부 의존성 없이 전체 통과.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from daily_report.main import cli


def _run_cli_daily_only(
    repo: Path, db: Path, project_root: Path, sources_yaml: Path
) -> int:
    """daily 만 (deep_dive 스킵) — 기존 v0.1 동작 회귀."""
    return cli(
        [
            "--repo",
            str(repo),
            "--db",
            str(db),
            "--profile",
            str(project_root / "config" / "profile.example.yaml"),
            "--sources",
            str(sources_yaml),
            "--dry-run",
            "--mock-llm",
            "--skip-deep-dive",
        ]
    )


def _run_cli_with_deep_dive(
    repo: Path, db: Path, project_root: Path, sources_yaml: Path
) -> int:
    """daily + deep_dive 페어 — v0.2 신규 동작."""
    return cli(
        [
            "--repo",
            str(repo),
            "--db",
            str(db),
            "--profile",
            str(project_root / "config" / "profile.example.yaml"),
            "--sources",
            str(sources_yaml),
            "--dry-run",
            "--mock-llm",
        ]
    )


def test_full_pipeline_succeeds(
    two_commit_repo: Path,
    tmp_path: Path,
    project_root: Path,
    git_only_sources_yaml: Path,
) -> None:
    db = tmp_path / "state.db"
    rc = _run_cli_daily_only(two_commit_repo, db, project_root, git_only_sources_yaml)
    assert rc == 0, "CLI should exit 0 on success"

    conn = sqlite3.connect(db)
    try:
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        reports_count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        assert events_count == 2, f"expected 2 events, got {events_count}"
        assert reports_count == 1, f"expected 1 report (daily), got {reports_count}"

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
    two_commit_repo: Path,
    tmp_path: Path,
    project_root: Path,
    git_only_sources_yaml: Path,
) -> None:
    db = tmp_path / "state.db"
    assert _run_cli_daily_only(two_commit_repo, db, project_root, git_only_sources_yaml) == 0
    assert _run_cli_daily_only(two_commit_repo, db, project_root, git_only_sources_yaml) == 0

    conn = sqlite3.connect(db)
    try:
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert events_count == 2, f"idempotency broken: {events_count} events"
    finally:
        conn.close()


def test_empty_repo_still_succeeds(
    empty_git_repo: Path,
    tmp_path: Path,
    project_root: Path,
    git_only_sources_yaml: Path,
) -> None:
    """커밋이 없어도 '조용한 하루' 리포트로 통과해야 함."""
    db = tmp_path / "state.db"
    rc = _run_cli_daily_only(empty_git_repo, db, project_root, git_only_sources_yaml)
    assert rc == 0

    conn = sqlite3.connect(db)
    try:
        events_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        reports_count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        assert events_count == 0
        assert reports_count == 1  # 조용한 하루도 리포트는 발행
    finally:
        conn.close()


def test_daily_and_deep_dive_publish_as_pair(
    two_commit_repo: Path,
    tmp_path: Path,
    project_root: Path,
    git_only_sources_yaml: Path,
) -> None:
    """v0.2: daily 가 발송되면 deep_dive 도 같이 발송되고 cs_concepts_covered 가 기록됨."""
    db = tmp_path / "state.db"
    rc = _run_cli_with_deep_dive(two_commit_repo, db, project_root, git_only_sources_yaml)
    assert rc == 0

    conn = sqlite3.connect(db)
    try:
        kinds = sorted(
            r[0] for r in conn.execute("SELECT kind FROM reports").fetchall()
        )
        assert kinds == ["daily", "deep_dive"], f"got reports kinds: {kinds}"

        channels = sorted(
            r[0] for r in conn.execute(
                "SELECT discord_channel_label FROM reports"
            ).fetchall()
        )
        assert channels == ["cs_foundations", "daily"]

        # cs_concepts_covered 에 1 행
        concepts = conn.execute(
            "SELECT concept, times_covered FROM cs_concepts_covered"
        ).fetchall()
        assert len(concepts) == 1
        assert concepts[0][1] == 1
    finally:
        conn.close()


def test_deep_dive_repeat_avoidance_across_runs(
    two_commit_repo: Path,
    tmp_path: Path,
    project_root: Path,
    git_only_sources_yaml: Path,
) -> None:
    """같은 mock 응답을 두 번 받아도 cs_concepts_covered 는 같은 개념을 +1 증가만."""
    db = tmp_path / "state.db"
    assert _run_cli_with_deep_dive(two_commit_repo, db, project_root, git_only_sources_yaml) == 0
    assert _run_cli_with_deep_dive(two_commit_repo, db, project_root, git_only_sources_yaml) == 0

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT concept, times_covered FROM cs_concepts_covered"
        ).fetchall()
        assert len(rows) == 1, "동일 개념이 새 row 로 추가되면 안 됨"
        assert rows[0][1] == 2, "times_covered 는 2 회"
    finally:
        conn.close()


def _run_cli_kind(
    repo: Path,
    db: Path,
    project_root: Path,
    sources_yaml: Path,
    kind: str,
) -> int:
    return cli(
        [
            "--repo",
            str(repo),
            "--db",
            str(db),
            "--profile",
            str(project_root / "config" / "profile.example.yaml"),
            "--sources",
            str(sources_yaml),
            "--dry-run",
            "--mock-llm",
            "--kind",
            kind,
        ]
    )


def test_weekly_kind_publishes_to_weekly_channel(
    two_commit_repo: Path,
    tmp_path: Path,
    project_root: Path,
    git_only_sources_yaml: Path,
) -> None:
    db = tmp_path / "state.db"
    rc = _run_cli_kind(two_commit_repo, db, project_root, git_only_sources_yaml, "weekly")
    assert rc == 0

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT kind, discord_channel_label FROM reports"
        ).fetchone()
        assert row[0] == "weekly"
        assert row[1] == "weekly"
    finally:
        conn.close()


def test_monthly_kind_publishes_to_monthly_channel(
    two_commit_repo: Path,
    tmp_path: Path,
    project_root: Path,
    git_only_sources_yaml: Path,
) -> None:
    db = tmp_path / "state.db"
    rc = _run_cli_kind(two_commit_repo, db, project_root, git_only_sources_yaml, "monthly")
    assert rc == 0

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT kind, discord_channel_label FROM reports"
        ).fetchone()
        assert row[0] == "monthly"
        assert row[1] == "monthly"
    finally:
        conn.close()
