"""CLI orchestrator — collect → persist → write → publish 한 줄 파이프라인.

v0.1 슬라이스: git_log + daily + Discord. 다른 stage 들은 v0.2+.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collectors.git_log import collect_git_log
from .config import AppConfig, load_config, load_sources
from .db import Database
from .llm import LLM
from .publishers.discord import publish_to_discord
from .schemas import Event
from .writers.daily import write_daily_report

_DEFAULT_PROFILE = "config/profile.yaml"
_DEFAULT_PROFILE_FALLBACK = "config/profile.example.yaml"
_DEFAULT_SOURCES = "config/sources.yaml"
_DEFAULT_SOURCES_FALLBACK = "config/sources.example.yaml"
_DEFAULT_DB = "data/state.db"


def _info(text: str) -> None:
    """UTF-8 콘솔 출력 (Windows cp949 호환)."""
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    except (AttributeError, OSError):
        sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def _resolve_path(path: str, fallback: str) -> str:
    """path 가 없으면 fallback 사용."""
    if Path(path).exists():
        return path
    if Path(fallback).exists():
        _info(f"[config] {path} 없음 → {fallback} 사용")
        return fallback
    raise FileNotFoundError(f"neither {path} nor {fallback} exists")


def _collect_git_events(sources: dict, repo_override: str | None) -> list[Event]:
    """sources.yaml 의 git.local_repos 를 순회하며 collect.

    --repo 가 주어지면 해당 1개 경로만 사용.
    """
    if repo_override:
        return collect_git_log(repo_override, since_hours=24)

    git_cfg = sources.get("sources", {}).get("git", {})
    if not git_cfg.get("enabled", False):
        return []

    events: list[Event] = []
    for repo_cfg in git_cfg.get("local_repos") or []:
        events.extend(
            collect_git_log(
                repo_path=repo_cfg["path"],
                since_hours=24,
                author_email=repo_cfg.get("author_email") or None,
                branches=repo_cfg.get("branches"),
            )
        )
    return events


def run_daily(
    cfg: AppConfig,
    sources: dict,
    db: Database,
    llm: LLM,
    repo_override: str | None,
    dry_run: bool,
) -> int:
    """0 = success, 1 = failure (CLI exit code)."""

    # 1. Collect
    _info("[collect] git_log...")
    events = _collect_git_events(sources, repo_override)
    _info(f"[collect] {len(events)} event(s)")

    # 2. Persist (멱등 — 같은 commit 재실행 시 덮어씀)
    if events:
        n = db.upsert_events(events)
        _info(f"[db]      upserted {n} event(s); total={db.count_events()}")

    # 3. Write
    _info("[write]   daily report...")
    now = datetime.now(timezone.utc)
    report = write_daily_report(events, cfg, llm, now)
    _info(f"[write]   title='{report.title}' sections={len(report.sections)} read~{report.estimated_read_minutes}분")

    # 4. Publish
    channel = "daily"
    webhook = cfg.discord.webhook_urls.get(channel, "")
    color = cfg.discord.embed_colors.get(channel, 0)

    if not dry_run and not webhook:
        _info(f"[publish] '{channel}' webhook 미설정 → dry-run 으로 전환")
        dry_run = True

    _info(f"[publish] sending → '{channel}' (dry_run={dry_run})")
    result = publish_to_discord(
        report=report,
        webhook_url=webhook,
        channel_label=channel,
        color=color,
        dry_run=dry_run,
    )

    if result.success:
        rid = db.save_report(report, channel_label=channel, message_id=result.discord_message_id)
        _info(f"[db]      report saved id={rid}; total={db.count_reports()}")
        _info("[done]    OK")
        return 0
    else:
        _info(f"[publish] FAILED: {result.error}")
        return 1


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="daily-report",
        description="매일 아침 8시(KST) 개발 학습 리포트 생성·발송",
    )
    parser.add_argument("--profile", default=_DEFAULT_PROFILE)
    parser.add_argument("--sources", default=_DEFAULT_SOURCES)
    parser.add_argument("--db", default=_DEFAULT_DB)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discord 에 실제 POST 하지 않고 payload 만 stdout 출력",
    )
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help="Anthropic API 호출 없이 캔드 응답 사용 (개발/테스트용)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="단일 git repo 경로 override (sources.yaml 무시)",
    )
    parser.add_argument(
        "--kind",
        default="daily",
        choices=["daily"],
        help="v0.1 은 daily 만 지원",
    )
    args = parser.parse_args(argv)

    profile_path = _resolve_path(args.profile, _DEFAULT_PROFILE_FALLBACK)
    sources_path = _resolve_path(args.sources, _DEFAULT_SOURCES_FALLBACK)

    cfg = load_config(profile_path)
    sources = load_sources(sources_path)
    db = Database(args.db)
    llm = LLM(model=cfg.llm.writer_model, mock=args.mock_llm)

    if args.kind == "daily":
        return run_daily(cfg, sources, db, llm, args.repo, args.dry_run)

    return 1


if __name__ == "__main__":
    sys.exit(cli())
