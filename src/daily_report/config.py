"""profile.yaml + .env 로더. ${VAR} 형태 환경변수 치환 지원."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


class ProfileFields(BaseModel):
    language: str = "ko"
    levels: list[str]
    experience_years: float = 0
    current_focus: list[str] = Field(default_factory=list)
    learning_goals: list[str] = Field(default_factory=list)
    weak_areas: list[str] = Field(default_factory=list)
    tone: str = "morning_calm"
    daily_read_minutes: int = 25


class CSFoundationsConfig(BaseModel):
    prioritize: list[str] = Field(default_factory=list)
    relate_to_today_work: bool = True
    min_days_between_repeat: int = 14
    fallback_topics: list[str] = Field(default_factory=list)


class DiscordConfig(BaseModel):
    webhook_urls: dict[str, str]
    embed_colors: dict[str, int]


class ScheduleConfig(BaseModel):
    timezone: str = "Asia/Seoul"
    daily_at: str = "08:00"
    weekly_at: str = "MON 08:00"
    monthly_at: str = "1 08:00"


class LLMConfig(BaseModel):
    processor_model: str
    writer_model: str
    prompt_caching: bool = True


class AppConfig(BaseModel):
    profile: ProfileFields
    cs_foundations: CSFoundationsConfig = Field(default_factory=CSFoundationsConfig)
    discord: DiscordConfig
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    llm: LLMConfig


def load_config(profile_path: str | Path) -> AppConfig:
    """profile.yaml 을 읽어 ${VAR} 치환 후 AppConfig 로 검증."""
    load_dotenv()  # .env 가 있으면 환경변수에 주입
    raw = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8"))
    return AppConfig(**_substitute_env(raw))


def load_sources(sources_path: str | Path) -> dict:
    """sources.yaml 은 schema 가 자유로워 dict 그대로 반환."""
    load_dotenv()
    raw = yaml.safe_load(Path(sources_path).read_text(encoding="utf-8"))
    return _substitute_env(raw)
