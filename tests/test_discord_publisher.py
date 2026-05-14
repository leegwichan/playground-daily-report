"""publishers/discord.py 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

from daily_report.publishers.discord import publish_to_discord, report_to_embeds
from daily_report.schemas import Report, ReportSection


def _sample_report() -> Report:
    now = datetime.now(timezone.utc)
    return Report(
        kind="daily",
        period_start=now,
        period_end=now,
        title="Daily Report 2026-05-10",
        tldr=["요점1", "요점2", "요점3"],
        sections=[
            ReportSection(heading="섹션 A", body_md="본문 A"),
            ReportSection(heading="섹션 B", body_md="본문 B" * 100),
        ],
        estimated_read_minutes=5,
        generated_at=now,
    )


def test_report_to_embeds_main_plus_sections() -> None:
    rpt = _sample_report()
    embeds = report_to_embeds(rpt, color=3447003)

    assert len(embeds) == 1 + len(rpt.sections)  # main + per-section
    assert embeds[0]["title"] == rpt.title
    assert all("•" in line or line.startswith("•") for line in embeds[0]["description"].split("\n"))
    assert embeds[0]["color"] == 3447003
    assert embeds[0]["footer"]["text"] == "~5분 읽기"


def test_report_to_embeds_truncates_long_body() -> None:
    rpt = _sample_report()
    rpt.sections[0].body_md = "x" * 5000  # 4096 limit 초과
    embeds = report_to_embeds(rpt, color=0)
    long_section = embeds[1]
    assert len(long_section["description"]) <= 4096
    assert long_section["description"].endswith("…")


def test_report_to_embeds_caps_at_10() -> None:
    """Discord 제약: 한 메시지에 최대 10 embeds."""
    rpt = _sample_report()
    rpt.sections = [ReportSection(heading=f"s{i}", body_md=str(i)) for i in range(20)]
    embeds = report_to_embeds(rpt, color=0)
    assert len(embeds) == 10


def test_publish_dry_run_returns_success_without_post() -> None:
    rpt = _sample_report()
    result = publish_to_discord(
        report=rpt,
        webhook_url="",  # dry_run 이면 무시
        channel_label="daily",
        color=0,
        dry_run=True,
    )
    assert result.success is True
    assert result.error is None
    assert result.discord_message_id is None


def test_publish_without_webhook_fails_when_not_dry_run() -> None:
    rpt = _sample_report()
    result = publish_to_discord(
        report=rpt,
        webhook_url="",
        channel_label="daily",
        color=0,
        dry_run=False,
    )
    assert result.success is False
    assert "missing webhook_url" in (result.error or "")


def _weekly_report() -> Report:
    now = datetime.now(timezone.utc)
    return Report(
        kind="weekly",
        period_start=now,
        period_end=now,
        title="Weekly Report",
        tldr=["x"],
        sections=[ReportSection(heading="섹션", body_md="본문")],
        estimated_read_minutes=10,
        generated_at=now,
    )


def _monthly_report() -> Report:
    now = datetime.now(timezone.utc)
    return Report(
        kind="monthly",
        period_start=now,
        period_end=now,
        title="Monthly Report",
        tldr=["x"],
        sections=[ReportSection(heading="섹션", body_md="본문")],
        estimated_read_minutes=15,
        generated_at=now,
    )


def test_footer_dispatch_weekly_includes_resume_phrase() -> None:
    """AC #19 / E2: weekly 의 footer 에 '직접 골라 이력서에 옮길 후보' 부착."""
    embeds = report_to_embeds(_weekly_report(), color=0)
    assert "직접 골라 이력서에 옮길 후보" in embeds[0]["footer"]["text"]


def test_footer_dispatch_monthly_includes_resume_phrase() -> None:
    """AC #19: monthly 도 동일 표기."""
    embeds = report_to_embeds(_monthly_report(), color=0)
    assert "직접 골라 이력서에 옮길 후보" in embeds[0]["footer"]["text"]


def test_footer_dispatch_daily_does_not_include_resume_phrase() -> None:
    """daily 의 footer 는 이력서 표기 없음 (kind dispatch 정확성)."""
    embeds = report_to_embeds(_sample_report(), color=0)
    assert "직접 골라 이력서에 옮길 후보" not in embeds[0]["footer"]["text"]
