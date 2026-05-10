"""collectors/claude_sessions.py 단위 테스트."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from daily_report.collectors.claude_sessions import (
    _extract_library_hints,
    _extract_user_text,
    _is_natural_prompt,
    collect_claude_sessions,
)
from daily_report.schemas import SourceType


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(offset_min: int = 0) -> str:
    return (_now() + timedelta(minutes=offset_min)).isoformat()


def _user_record(text: str, ts: str, session_id: str, cwd: str = "C:/proj") -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": cwd,
        "gitBranch": "main",
    }


def _assistant_record(
    blocks: list[dict], ts: str, session_id: str, cwd: str = "C:/proj"
) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "model": "test", "content": blocks},
        "timestamp": ts,
        "sessionId": session_id,
        "cwd": cwd,
        "gitBranch": "main",
    }


def _write_session(dir_: Path, session_id: str, records: list[dict]) -> Path:
    f = dir_ / f"{session_id}.jsonl"
    f.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8",
    )
    return f


@pytest.fixture
def synthetic_projects_dir(tmp_path: Path) -> Path:
    """가짜 ~/.claude/projects 구조 만들기."""
    root = tmp_path / "projects"
    root.mkdir()
    return root


def test_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert collect_claude_sessions(tmp_path / "nope") == []


def test_returns_empty_when_no_sessions(synthetic_projects_dir: Path) -> None:
    (synthetic_projects_dir / "proj-a").mkdir()
    assert collect_claude_sessions(synthetic_projects_dir) == []


def test_collects_session_with_natural_prompt(synthetic_projects_dir: Path) -> None:
    proj = synthetic_projects_dir / "C--Users-cnddk-Documents-foo"
    proj.mkdir()
    sid = "session-1"
    records = [
        _user_record("Spring AOP 동작 원리 정리해줘", _ts(0), sid),
        _assistant_record(
            [
                {"type": "text", "text": "정리해드릴게요."},
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "pip install jinja2"},
                },
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "C:/proj/notes.md"},
                },
            ],
            _ts(20),
            sid,
        ),
    ]
    _write_session(proj, sid, records)

    events = collect_claude_sessions(synthetic_projects_dir, min_session_duration_minutes=1)
    assert len(events) == 1
    ev = events[0]

    assert ev.source == SourceType.CLAUDE_SESSION
    assert ev.id.startswith("claude_session:session-1:")
    assert "Spring AOP" in ev.title
    assert ev.metadata["tool_call_counts"] == {"Bash": 1, "Write": 1}
    assert "C:/proj/notes.md" in ev.metadata["files_touched"]
    assert ev.metadata["library_hints"] == ["jinja2"]
    assert ev.metadata["errors_count"] == 0


def test_skips_short_session(synthetic_projects_dir: Path) -> None:
    proj = synthetic_projects_dir / "p"
    proj.mkdir()
    sid = "tiny"
    records = [
        _user_record("hi", _ts(0), sid),
        _assistant_record([{"type": "text", "text": "hi"}], _ts(1), sid),
    ]
    _write_session(proj, sid, records)
    # 기본 min=5 분, 위 세션은 1분
    assert collect_claude_sessions(synthetic_projects_dir) == []


def test_lookback_window_filters_old_records(synthetic_projects_dir: Path) -> None:
    proj = synthetic_projects_dir / "p"
    proj.mkdir()
    sid = "old"
    old_ts = (_now() - timedelta(days=10)).isoformat()
    records = [
        _user_record("오래된 요청", old_ts, sid),
        _assistant_record([{"type": "text", "text": "응답"}], old_ts, sid),
    ]
    _write_session(proj, sid, records)
    events = collect_claude_sessions(
        synthetic_projects_dir, lookback_hours=24, min_session_duration_minutes=1
    )
    assert events == []


def test_first_user_prompt_skips_slash_commands(synthetic_projects_dir: Path) -> None:
    """slash 커맨드/시스템 메시지는 첫 자연어 prompt 후보에서 제외."""
    proj = synthetic_projects_dir / "p"
    proj.mkdir()
    sid = "skip"
    records = [
        _user_record("<command-message>fewer-permission-prompts</command-message>", _ts(0), sid),
        _user_record("<local-command-caveat>Caveat: ...</local-command-caveat>", _ts(2), sid),
        _user_record("진짜 첫 자연어 요청입니다", _ts(5), sid),
        _assistant_record([{"type": "text", "text": "ok"}], _ts(20), sid),
    ]
    _write_session(proj, sid, records)
    events = collect_claude_sessions(synthetic_projects_dir, min_session_duration_minutes=1)
    assert len(events) == 1
    assert "진짜 첫 자연어" in events[0].title


def test_user_rejection_not_counted_as_stuck(synthetic_projects_dir: Path) -> None:
    """사용자가 도구 호출 거절한 건 '막힌 지점' 아님."""
    proj = synthetic_projects_dir / "p"
    proj.mkdir()
    sid = "rejected"
    records = [
        _user_record("뭔가 해줘", _ts(0), sid),
        _assistant_record(
            [{"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}}],
            _ts(1),
            sid,
        ),
        # 거절 메시지는 user role tool_result 로 들어감
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "is_error": True,
                        "content": "The user doesn't want to proceed with this tool use.",
                    },
                    {
                        "type": "tool_result",
                        "is_error": True,
                        "content": "Exit code 128 fatal: not a git repository",
                    },
                ],
            },
            "timestamp": _ts(2),
            "sessionId": sid,
            "cwd": "C:/p",
        },
        _assistant_record([{"type": "text", "text": "알겠어요"}], _ts(20), sid),
    ]
    _write_session(proj, sid, records)
    events = collect_claude_sessions(synthetic_projects_dir, min_session_duration_minutes=1)
    assert len(events) == 1
    # rejection 1건 제외, 진짜 에러 1건만 남아야
    assert events[0].metadata["errors_count"] == 1
    assert "git repository" in events[0].metadata["errors_sample"][0]


def test_include_exclude_project_globs(synthetic_projects_dir: Path) -> None:
    for name in ["foo-A", "foo-B", "bar-secret-1", "qux"]:
        d = synthetic_projects_dir / name
        d.mkdir()
        # cwd 도 프로젝트 이름과 일치시킴 (실제 ~/.claude/projects 구조와 동일)
        records = [
            _user_record(f"하기 {name}", _ts(0), name, cwd=f"C:/{name}"),
            _assistant_record([{"type": "text", "text": "ok"}], _ts(10), name, cwd=f"C:/{name}"),
        ]
        _write_session(d, name, records)

    events = collect_claude_sessions(
        synthetic_projects_dir,
        include_projects=["foo-*"],
        exclude_projects=["*-secret-*"],
        min_session_duration_minutes=1,
    )
    assert len(events) == 2  # foo-A + foo-B; bar-secret-1 (excluded), qux (not included)
    project_names = sorted(Path(ev.metadata["project_cwd"]).name for ev in events)
    assert project_names == ["foo-A", "foo-B"]


def test_extract_user_text_handles_string_and_blocks() -> None:
    assert _extract_user_text({"content": "plain"}) == "plain"
    assert (
        _extract_user_text(
            {"content": [{"type": "text", "text": "a"}, {"type": "tool_result", "content": "ignored"}, {"type": "text", "text": "b"}]}
        )
        == "a\nb"
    )
    assert _extract_user_text({}) == ""
    assert _extract_user_text("not a dict") == ""  # type: ignore[arg-type]


def test_is_natural_prompt() -> None:
    assert _is_natural_prompt("정상 요청")
    assert not _is_natural_prompt("")
    assert not _is_natural_prompt("<command-message>foo</command-message>")
    assert not _is_natural_prompt("<system-reminder>...")
    assert not _is_natural_prompt("<local-command-caveat>Caveat: ...")


def test_extract_library_hints() -> None:
    cmds = [
        "pip install httpx pydantic",
        "npm install --save-dev typescript",
        "go get github.com/foo/bar",
        "echo hi",  # 매칭 없음
    ]
    libs = _extract_library_hints(cmds)
    assert "httpx" in libs
    assert "pydantic" in libs
    assert "typescript" in libs
    assert "github.com/foo/bar" in libs


def test_idempotent_id_for_same_session_same_day(synthetic_projects_dir: Path) -> None:
    """같은 세션을 두 번 collect 해도 같은 id."""
    proj = synthetic_projects_dir / "p"
    proj.mkdir()
    sid = "stable"
    records = [
        _user_record("요청", _ts(0), sid),
        _assistant_record([{"type": "text", "text": "응답"}], _ts(20), sid),
    ]
    _write_session(proj, sid, records)
    a = collect_claude_sessions(synthetic_projects_dir, min_session_duration_minutes=1)
    b = collect_claude_sessions(synthetic_projects_dir, min_session_duration_minutes=1)
    assert a[0].id == b[0].id
