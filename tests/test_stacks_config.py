"""StacksConfig + EventClassificationConfig 단위 테스트.

핵심:
  - 두 신규 필드 모두 `Field(default_factory=...)` 로 backward compat (R8).
  - 디폴트 패턴은 모듈 상수에서 확장 — 사용자 yaml 은 추가만, 덮어쓰기 X (NB5).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from daily_report.config import (
    AppConfig,
    EventClassificationConfig,
    ProfileFields,
    StacksConfig,
    load_config,
)


def test_stacks_config_default_empty_lists() -> None:
    s = StacksConfig()
    assert s.primary == []
    assert s.interest == []


def test_profile_fields_stacks_default() -> None:
    p = ProfileFields(levels=["backend_jobseeker"])
    assert isinstance(p.stacks, StacksConfig)
    assert p.stacks.primary == []
    assert p.stacks.interest == []


def test_event_classification_with_defaults_loads_patterns() -> None:
    ec = EventClassificationConfig.with_defaults()
    # 디폴트가 비어있지 않아야 (모듈 상수가 적용됨)
    assert len(ec.backend_path_patterns) > 0
    assert len(ec.agent_path_patterns) > 0
    assert ec.overlap_window_minutes == 30


def test_event_classification_user_patterns_extend_defaults() -> None:
    """사용자 패턴은 디폴트를 *확장* 한다 (덮어쓰기 X) — NB5."""
    user_backend = ["*.scala"]
    user_agent = ["custom-agent/**"]
    ec = EventClassificationConfig(
        backend_path_patterns=user_backend,
        agent_path_patterns=user_agent,
    )
    # 사용자 추가 패턴 포함
    assert "*.scala" in ec.backend_path_patterns
    assert "custom-agent/**" in ec.agent_path_patterns
    # 디폴트도 같이 포함 (합집합)
    assert "*.java" in ec.backend_path_patterns
    assert ".claude/**" in ec.agent_path_patterns


def test_event_classification_overlap_window_clamped() -> None:
    """overlap_window_minutes 가 합리적 범위 내 (1~240)."""
    ec = EventClassificationConfig.with_defaults()
    assert 1 <= ec.overlap_window_minutes <= 240


def test_legacy_profile_without_stacks_or_event_classification_loads(
    tmp_path: Path,
) -> None:
    """E5/R8: 기존 profile.yaml (stacks / event_classification 필드 없음) 도
    `default_factory` 로 ValidationError 없이 로드되어야 한다."""
    legacy_yaml = {
        "profile": {
            "language": "ko",
            "levels": ["backend_jobseeker", "backend_junior"],
            "experience_years": 1.0,
            "tone": "morning_calm",
            "daily_read_minutes": 25,
        },
        "discord": {
            "webhook_urls": {
                "daily": "x",
                "weekly": "x",
                "monthly": "x",
                "cs_foundations": "x",
            },
            "embed_colors": {
                "daily": 0,
                "weekly": 0,
                "monthly": 0,
                "cs_foundations": 0,
            },
        },
        "llm": {
            "processor_model": "p",
            "writer_model": "w",
        },
    }
    p = tmp_path / "legacy_profile.yaml"
    p.write_text(yaml.safe_dump(legacy_yaml), encoding="utf-8")

    cfg = load_config(p)
    # stacks 와 event_classification 둘 다 디폴트 인스턴스로 로드
    assert isinstance(cfg.profile.stacks, StacksConfig)
    assert cfg.profile.stacks.primary == []
    assert isinstance(cfg.event_classification, EventClassificationConfig)
    assert len(cfg.event_classification.backend_path_patterns) > 0  # 디폴트 패턴


def test_example_profile_loads_with_stacks(example_profile_path: Path) -> None:
    """갱신된 example yaml 의 stacks 가 Java/Spring/MySQL/AWS + Kotlin."""
    cfg = load_config(example_profile_path)
    assert "java" in cfg.profile.stacks.primary
    assert "kotlin" in cfg.profile.stacks.interest
    # current_focus deprecated 였지만 backward compat 유지
    assert isinstance(cfg.profile.current_focus, list)
