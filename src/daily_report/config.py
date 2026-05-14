"""profile.yaml + .env 로더. ${VAR} 형태 환경변수 치환 지원."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


# ─────────────────────────────────────────────────────────
# Default file path patterns for event classification (NB5)
# 사용자 yaml 의 패턴은 이 디폴트를 *확장* 한다 (덮어쓰기 X).
# agent_path_patterns 가 backend 보다 우선순위 — _classify_files 가 agent 먼저 매칭.
# ─────────────────────────────────────────────────────────
_DEFAULT_BACKEND_PATTERNS: tuple[str, ...] = (
    "*.java",
    "*.kt",
    "*.kts",
    "*.sql",
    "*.gradle",
    "*.gradle.kts",
    "pom.xml",
    "build.gradle",
    "src/main/**",
    "src/test/**",
    "src/**/*.java",
    "src/**/*.kt",
    "src/**/*.py",
    "tests/**/*.py",
    "**/dockerfile",
    "**/docker-compose*.yml",
    "**/docker-compose*.yaml",
    "**/application*.yml",
    "**/application*.yaml",
    "**/application*.properties",
    "data/schema.sql",
    "data/**/*.sql",
)

_DEFAULT_AGENT_PATTERNS: tuple[str, ...] = (
    ".claude/**",
    ".claude/**/*.md",
    "skills/**/*.md",
    ".cursor/**",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".omc/**",
    "agents/**/*.md",
    "claude.md",
    "agents.md",
    "**/claude.md",
    "**/agents.md",
)


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


class StacksConfig(BaseModel):
    """사용자 실 스택. primary 가 daily/deep_dive 어휘의 1순위."""

    primary: list[str] = Field(default_factory=list)
    interest: list[str] = Field(default_factory=list)


class EventClassificationConfig(BaseModel):
    """file_category 분류 룰. 사용자 yaml 패턴은 모듈 디폴트를 *확장*한다."""

    backend_path_patterns: list[str] = Field(default_factory=list)
    agent_path_patterns: list[str] = Field(default_factory=list)
    overlap_window_minutes: int = Field(default=30, ge=1, le=240)

    @model_validator(mode="after")
    def _extend_with_defaults(self) -> "EventClassificationConfig":
        """사용자 패턴이 빈 list 면 디폴트만, 값이 있으면 디폴트 + 사용자 합집합."""
        existing_backend = set(self.backend_path_patterns)
        existing_agent = set(self.agent_path_patterns)
        for pat in _DEFAULT_BACKEND_PATTERNS:
            if pat not in existing_backend:
                self.backend_path_patterns.append(pat)
                existing_backend.add(pat)
        for pat in _DEFAULT_AGENT_PATTERNS:
            if pat not in existing_agent:
                self.agent_path_patterns.append(pat)
                existing_agent.add(pat)
        return self

    @classmethod
    def with_defaults(cls) -> "EventClassificationConfig":
        """디폴트 패턴만 담긴 인스턴스. AppConfig.event_classification 의 factory."""
        return cls(backend_path_patterns=[], agent_path_patterns=[])


class ProfileFields(BaseModel):
    language: str = "ko"
    levels: list[str]
    experience_years: float = 0
    # deprecated: current_focus 는 stacks 로 대체. backward compat 위해 유지.
    current_focus: list[str] = Field(default_factory=list)
    stacks: StacksConfig = Field(default_factory=StacksConfig)
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
    event_classification: EventClassificationConfig = Field(
        default_factory=EventClassificationConfig.with_defaults,
    )


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
