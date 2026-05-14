"""CLI orchestrator — collect → persist → write → publish 한 줄 파이프라인.

v0.1 슬라이스: git_log + daily + Discord. 다른 stage 들은 v0.2+.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .collectors.claude_sessions import collect_claude_sessions
from .collectors.git_log import collect_git_log
from .collectors.notion import collect_notion
from .collectors.pdf_summary import collect_pdfs
from .collectors.web_pages import collect_web_pages
from .config import AppConfig, load_config, load_sources
from .db import Database
from .llm import LLM
from .publishers.discord import publish_to_discord
from .schemas import Event, METADATA_KEY_FILE_CATEGORY, Report, SourceType
from .writers.daily import write_daily_report
from .writers.deep_dive import write_deep_dive_report
from .writers.monthly import write_monthly_report
from .writers.weekly import write_weekly_report

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


def _resolve_collector_overlap(
    events: list[Event],
    time_window_min: int,
) -> list[Event]:
    """claude_session 이벤트의 file_category 를 같은 시간대 git 이벤트가 있으면 git 값으로 덮어쓴다.

    Args:
        events: 모든 collector 의 합쳐진 Event 리스트.
        time_window_min: git 이벤트와 claude_session 이벤트가 ±이 분 안에 있으면 양보.

    Returns:
        새 Event 리스트 (원본 비파괴). git 이벤트는 그대로, claude_session 이벤트 중
        시간대 매칭되는 git 가 있으면 metadata[file_category] 만 덮어씀.
    """
    git_events = [e for e in events if e.source == SourceType.GIT]
    if not git_events:
        return list(events)

    window = timedelta(minutes=time_window_min)
    resolved: list[Event] = []
    for ev in events:
        if ev.source != SourceType.CLAUDE_SESSION:
            resolved.append(ev)
            continue
        # 같은 시간대 git 이벤트 찾기 (가장 가까운 1개)
        nearest_git: Event | None = None
        nearest_delta: timedelta | None = None
        for g in git_events:
            delta = abs(g.occurred_at - ev.occurred_at)
            if delta <= window and (nearest_delta is None or delta < nearest_delta):
                nearest_git = g
                nearest_delta = delta
        if nearest_git is None:
            resolved.append(ev)
            continue
        # git 의 file_category 로 덮어쓰기 (사본 metadata)
        new_metadata = dict(ev.metadata)
        new_metadata[METADATA_KEY_FILE_CATEGORY] = nearest_git.metadata.get(
            METADATA_KEY_FILE_CATEGORY, "unknown"
        )
        resolved.append(ev.model_copy(update={"metadata": new_metadata}))
    return resolved


def _collect_all_events(
    sources: dict,
    repo_override: str | None,
    lookback_hours: int = 24,
    backend_patterns: list[str] | None = None,
    agent_patterns: list[str] | None = None,
    overlap_window_minutes: int = 30,
) -> list[Event]:
    """sources.yaml 의 활성화된 모든 collector 를 순회하며 Event[] 누적.

    --repo override 가 주어지면 git.local_repos 는 무시하고 해당 1개만 사용.
    수집 후 _resolve_collector_overlap 으로 claude_session ↔ git file_category 양보 처리.
    """
    events: list[Event] = []

    # ── Git ─────────────────────────────────────────────
    if repo_override:
        events.extend(
            collect_git_log(
                repo_override,
                since_hours=lookback_hours,
                backend_patterns=backend_patterns,
                agent_patterns=agent_patterns,
            )
        )
    else:
        git_cfg = sources.get("sources", {}).get("git", {})
        if git_cfg.get("enabled", False):
            for repo_cfg in git_cfg.get("local_repos") or []:
                events.extend(
                    collect_git_log(
                        repo_path=repo_cfg["path"],
                        since_hours=lookback_hours,
                        author_email=repo_cfg.get("author_email") or None,
                        branches=repo_cfg.get("branches"),
                        backend_patterns=backend_patterns,
                        agent_patterns=agent_patterns,
                    )
                )

    # ── Claude Code 세션 ────────────────────────────────
    claude_cfg = sources.get("sources", {}).get("claude_sessions", {})
    if claude_cfg.get("enabled", False):
        events.extend(
            collect_claude_sessions(
                projects_dir=claude_cfg["projects_dir"],
                lookback_hours=lookback_hours,
                include_projects=claude_cfg.get("include_projects"),
                exclude_projects=claude_cfg.get("exclude_projects"),
                min_session_duration_minutes=claude_cfg.get(
                    "min_session_duration_minutes", 5
                ),
            )
        )

    # ── Web pages ────────────────────────────────────────
    web_cfg = sources.get("sources", {}).get("web_pages", {})
    if web_cfg.get("enabled", False):
        try:
            events.extend(
                collect_web_pages(
                    feeds=web_cfg.get("feeds") or [],
                    one_off=web_cfg.get("one_off") or [],
                )
            )
        except Exception as e:
            _info(f"[collect] web_pages 실패 (무시하고 계속): {e}")

    # ── Notion ───────────────────────────────────────────
    notion_cfg = sources.get("sources", {}).get("notion", {})
    if notion_cfg.get("enabled", False):
        try:
            events.extend(
                collect_notion(
                    pages=notion_cfg.get("pages") or [],
                    databases=notion_cfg.get("databases") or [],
                    token=notion_cfg.get("token") or None,
                    lookback_hours=lookback_hours,
                )
            )
        except Exception as e:
            _info(f"[collect] notion 실패 (무시하고 계속): {e}")

    # ── PDF (inbox/pdf) ──────────────────────────────────
    pdf_cfg = sources.get("sources", {}).get("pdfs", {})
    if pdf_cfg.get("enabled", False):
        try:
            events.extend(
                collect_pdfs(
                    watch_dir=pdf_cfg["watch_dir"],
                    delete_after_processed=pdf_cfg.get("delete_after_processed", False),
                    summary_max_chars=pdf_cfg.get("summary_max_tokens", 2000),
                )
            )
        except Exception as e:
            _info(f"[collect] pdf 실패 (무시하고 계속): {e}")

    # ── claude_session ↔ git file_category 양보 후처리 ────
    events = _resolve_collector_overlap(events, time_window_min=overlap_window_minutes)
    return events


def _publish_report(
    report: Report,
    cfg: AppConfig,
    db: Database,
    channel: str,
    dry_run: bool,
    log_prefix: str,
) -> bool:
    """단일 Report 를 지정된 채널로 발송하고 DB 에 기록. True = 성공."""
    webhook = cfg.discord.webhook_urls.get(channel, "")
    color = cfg.discord.embed_colors.get(channel, 0)

    effective_dry_run = dry_run
    if not effective_dry_run and not webhook:
        _info(f"{log_prefix} '{channel}' webhook 미설정 → dry-run 으로 전환")
        effective_dry_run = True

    _info(f"{log_prefix} sending → '{channel}' (dry_run={effective_dry_run})")
    result = publish_to_discord(
        report=report,
        webhook_url=webhook,
        channel_label=channel,
        color=color,
        dry_run=effective_dry_run,
    )

    if not result.success:
        _info(f"{log_prefix} FAILED: {result.error}")
        return False

    rid = db.save_report(report, channel_label=channel, message_id=result.discord_message_id)
    _info(f"{log_prefix} saved report id={rid}; total={db.count_reports()}")
    return True


def _run_deep_dive(
    events: list[Event],
    cfg: AppConfig,
    db: Database,
    llm: LLM,
    now: datetime,
    dry_run: bool,
) -> bool:
    """deep_dive Report 생성·발송. 가능한 개념이 없으면 silently skip.

    True = 시도했고 성공 (또는 skip), False = 시도했고 실패.
    daily 와 독립적으로 동작 — deep_dive 실패가 daily 성공을 막지 않는다.
    """
    _info("[deep_dive] generating CS foundations...")
    report = write_deep_dive_report(events, cfg, llm, db, now)
    if report is None:
        _info("[deep_dive] no eligible concept (no events + fallback exhausted) — skip")
        return True

    assert report.cs_foundations is not None
    concept = report.cs_foundations.concept
    _info(
        f"[deep_dive] concept='{concept}' "
        f"(relates_to_today={report.cs_foundations.relates_to_today_work})"
    )

    channel = "cs_foundations"
    webhook = cfg.discord.webhook_urls.get(channel, "")
    color = cfg.discord.embed_colors.get(channel, 0)

    effective_dry_run = dry_run
    if not effective_dry_run and not webhook:
        _info(f"[deep_dive] '{channel}' webhook 미설정 → dry-run 으로 전환")
        effective_dry_run = True

    result = publish_to_discord(
        report=report,
        webhook_url=webhook,
        channel_label=channel,
        color=color,
        dry_run=effective_dry_run,
    )

    if not result.success:
        _info(f"[deep_dive] publish failed: {result.error}")
        return False

    rid = db.save_report(report, channel_label=channel, message_id=result.discord_message_id)
    # tier 는 cs_foundations block 에 LLM 이 채운 값 (없으면 None — DB 가 무관심)
    block_tier = getattr(report.cs_foundations, "tier", None)
    db.mark_concept_covered(concept, report_id=rid, tier=block_tier)
    _info(
        f"[deep_dive] saved report id={rid}, concept marked covered "
        f"(tier={block_tier or '미지정'})"
    )
    return True


def _collect_and_persist(
    sources: dict,
    db: Database,
    repo_override: str | None,
    lookback_hours: int,
    cfg: AppConfig,
) -> list[Event]:
    _info(f"[collect] git_log + claude_sessions + web_pages (lookback={lookback_hours}h)...")
    events = _collect_all_events(
        sources,
        repo_override,
        lookback_hours=lookback_hours,
        backend_patterns=cfg.event_classification.backend_path_patterns,
        agent_patterns=cfg.event_classification.agent_path_patterns,
        overlap_window_minutes=cfg.event_classification.overlap_window_minutes,
    )
    _info(f"[collect] {len(events)} event(s)")
    if events:
        n = db.upsert_events(events)
        _info(f"[db]      upserted {n} event(s); total={db.count_events()}")

    # 분류 텔레메트리 — backend/agent/mixed/unknown 카운트 (AC #22 dry-run 가시화)
    cat_counts = {"backend": 0, "agent": 0, "mixed": 0, "unknown": 0}
    for ev in events:
        cat = ev.metadata.get(METADATA_KEY_FILE_CATEGORY, "unknown")
        if cat in cat_counts:
            cat_counts[cat] += 1
        else:
            cat_counts["unknown"] += 1
    _info(
        f"[collect] backend={cat_counts['backend']} "
        f"agent={cat_counts['agent']} "
        f"mixed={cat_counts['mixed']} "
        f"unknown={cat_counts['unknown']}"
    )
    return events


def run_daily(
    cfg: AppConfig,
    sources: dict,
    db: Database,
    llm: LLM,
    repo_override: str | None,
    dry_run: bool,
    skip_deep_dive: bool = False,
) -> int:
    """daily 와 (옵션) deep_dive 를 페어로 실행.

    0 = daily 성공, 1 = daily 실패. deep_dive 실패는 종료 코드에 영향 없음.
    """
    now = datetime.now(timezone.utc)
    events = _collect_and_persist(sources, db, repo_override, lookback_hours=24, cfg=cfg)

    _info("[daily]   writing report...")
    report = write_daily_report(events, cfg, llm, now)
    if report is None:
        _info("[daily]   skipped — no backend events")
    else:
        _info(
            f"[daily]   title='{report.title}' sections={len(report.sections)} "
            f"read~{report.estimated_read_minutes}분"
        )

        if not _publish_report(
            report, cfg, db, channel="daily", dry_run=dry_run, log_prefix="[daily]  "
        ):
            return 1

    if skip_deep_dive:
        _info("[deep_dive] --skip-deep-dive 지정 → 건너뜀")
    else:
        _run_deep_dive(events, cfg, db, llm, now, dry_run=dry_run)

    _info("[done]    OK")
    return 0


def run_weekly(
    cfg: AppConfig,
    sources: dict,
    db: Database,
    llm: LLM,
    repo_override: str | None,
    dry_run: bool,
) -> int:
    now = datetime.now(timezone.utc)
    events = _collect_and_persist(sources, db, repo_override, lookback_hours=24 * 7, cfg=cfg)

    _info("[weekly]  writing report...")
    report = write_weekly_report(events, cfg, llm, now, db=db)
    _info(
        f"[weekly]  title='{report.title}' sections={len(report.sections)} "
        f"read~{report.estimated_read_minutes}분"
    )

    if not _publish_report(report, cfg, db, channel="weekly", dry_run=dry_run, log_prefix="[weekly] "):
        return 1
    _info("[done]    OK")
    return 0


def run_monthly(
    cfg: AppConfig,
    sources: dict,
    db: Database,
    llm: LLM,
    repo_override: str | None,
    dry_run: bool,
) -> int:
    now = datetime.now(timezone.utc)
    events = _collect_and_persist(sources, db, repo_override, lookback_hours=24 * 30, cfg=cfg)

    _info("[monthly] writing report...")
    report = write_monthly_report(events, cfg, llm, now, db=db)
    _info(
        f"[monthly] title='{report.title}' sections={len(report.sections)} "
        f"read~{report.estimated_read_minutes}분"
    )

    if not _publish_report(report, cfg, db, channel="monthly", dry_run=dry_run, log_prefix="[monthly]"):
        return 1
    _info("[done]    OK")
    return 0


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
        help="Gemini API 호출 없이 캔드 응답 사용 (개발/테스트용)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="단일 git repo 경로 override (sources.yaml 의 git.local_repos 무시)",
    )
    parser.add_argument(
        "--skip-deep-dive",
        action="store_true",
        help="#cs-foundations 채널 deep_dive 발송 건너뛰기",
    )
    parser.add_argument(
        "--kind",
        default="daily",
        choices=["daily", "weekly", "monthly"],
        help="daily(+자동 deep_dive) | weekly | monthly",
    )
    args = parser.parse_args(argv)

    profile_path = _resolve_path(args.profile, _DEFAULT_PROFILE_FALLBACK)
    sources_path = _resolve_path(args.sources, _DEFAULT_SOURCES_FALLBACK)

    cfg = load_config(profile_path)
    sources = load_sources(sources_path)
    db = Database(args.db)
    llm = LLM(model=cfg.llm.writer_model, mock=args.mock_llm)

    if args.kind == "daily":
        return run_daily(
            cfg=cfg,
            sources=sources,
            db=db,
            llm=llm,
            repo_override=args.repo,
            dry_run=args.dry_run,
            skip_deep_dive=args.skip_deep_dive,
        )
    if args.kind == "weekly":
        return run_weekly(
            cfg=cfg,
            sources=sources,
            db=db,
            llm=llm,
            repo_override=args.repo,
            dry_run=args.dry_run,
        )
    if args.kind == "monthly":
        return run_monthly(
            cfg=cfg,
            sources=sources,
            db=db,
            llm=llm,
            repo_override=args.repo,
            dry_run=args.dry_run,
        )

    return 1


if __name__ == "__main__":
    sys.exit(cli())
