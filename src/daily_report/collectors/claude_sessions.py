"""Claude Code 세션 로그 collector.

~/.claude/projects/<project>/<session-uuid>.jsonl 을 읽어,
지정된 lookback 시간 안에 활동이 있던 세션을 (session, day) 단위 Event 로 변환.

각 Event 는 다음 4가지 신호를 metadata 로 노출:
  - tool_call_counts : 어떤 작업을 했는가
  - files_touched    : 어떤 파일을 만졌는가
  - errors           : 막혔던 지점 (tool_result.is_error=true)
  - library_hints    : 어떤 라이브러리를 썼는가
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..schemas import Event, SourceType

# slash 커맨드/시스템 메시지 등 자연어가 아닌 user 메시지는 first_prompt 후보에서 제외
_NON_NATURAL_PREFIXES = (
    "<command-message>",
    "<command-name>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "<system-reminder>",
    "[Request interrupted",
)

# tool_result 가 is_error=true 여도 사용자 의도적 취소·거절은 "막힌 지점" 으로 보지 않음
_REJECTED_NOT_STUCK_MARKERS = (
    "The user doesn't want to proceed",
    "Cancelled: parallel tool call",
    "Tool execution cancelled by user",
)

# 라이브러리 힌트 추출 — 패키지 매니저 + install 서브커맨드 패턴
# 매칭 시 그 뒤의 모든 비-옵션 토큰을 패키지로 간주 (multi-arg `pip install A B C` 지원)
_PKG_INSTALL_TRIGGERS = (
    re.compile(r"\bpip\s+install\b", re.IGNORECASE),
    re.compile(r"\bpip3\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+(?:install|i|add)\b", re.IGNORECASE),
    re.compile(r"\bpnpm\s+(?:install|i|add)\b", re.IGNORECASE),
    re.compile(r"\byarn\s+add\b", re.IGNORECASE),
    re.compile(r"\bgo\s+get\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+add\b", re.IGNORECASE),
    re.compile(r"\buv\s+add\b", re.IGNORECASE),
)

# install 명령 뒤의 인자 영역을 자르는 셸 stop 토큰
_SHELL_STOP_TOKENS = (";", "&&", "||", "|", "\n")


def collect_claude_sessions(
    projects_dir: str | Path,
    lookback_hours: int = 24,
    include_projects: Optional[list[str]] = None,
    exclude_projects: Optional[list[str]] = None,
    min_session_duration_minutes: int = 5,
) -> list[Event]:
    """projects_dir 아래 모든 세션 jsonl 을 훑어 lookback 윈도우 안의 활동을 Event 로 변환."""
    projects_root = Path(projects_dir)
    if not projects_root.is_dir():
        return []

    include_patterns = include_projects or ["*"]
    exclude_patterns = exclude_projects or []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    events: list[Event] = []
    for proj_dir in sorted(projects_root.iterdir()):
        if not proj_dir.is_dir():
            continue
        name = proj_dir.name
        if not any(fnmatch.fnmatch(name, p) for p in include_patterns):
            continue
        if any(fnmatch.fnmatch(name, p) for p in exclude_patterns):
            continue

        for session_file in sorted(proj_dir.glob("*.jsonl")):
            ev = _build_event_for_session(
                session_file, cutoff, min_session_duration_minutes
            )
            if ev is not None:
                events.append(ev)
    return events


# ─────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────


def _parse_iso(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _record_ts(rec: dict) -> Optional[datetime]:
    return _parse_iso(rec.get("timestamp"))


def _read_jsonl(path: Path) -> Iterable[dict]:
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _extract_user_text(message: Any) -> str:
    """user 메시지의 자연어 텍스트만 추출.

    - content 가 str → 그대로
    - content 가 list[block] → text 블록의 text 만 (tool_result 등 스킵)
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()
    return ""


def _is_natural_prompt(text: str) -> bool:
    if not text:
        return False
    for prefix in _NON_NATURAL_PREFIXES:
        if text.lstrip().startswith(prefix):
            return False
    return True


def _shorten(text: str, max_len: int = 100) -> str:
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _extract_library_hints(commands: list[str]) -> list[str]:
    """`pip install A B C`, `npm i foo`, `go get x` 등에서 패키지 토큰만 추출."""
    found: set[str] = set()
    for cmd in commands:
        for trigger in _PKG_INSTALL_TRIGGERS:
            for m in trigger.finditer(cmd):
                tail = cmd[m.end() :]
                # 다음 셸 stop 토큰까지만 자름
                for stop in _SHELL_STOP_TOKENS:
                    idx = tail.find(stop)
                    if idx != -1:
                        tail = tail[:idx]
                for token in tail.split():
                    # 옵션 (`-q`, `--save-dev`) 제외
                    if token.startswith("-"):
                        continue
                    # 버전 핀/extras 제거: `pkg==1.0` `pkg[extra]` `pkg>=2`
                    pkg = token
                    for sep in ("==", ">=", "<=", "~=", ">", "<", "[", ","):
                        pkg = pkg.split(sep)[0]
                    pkg = pkg.strip().strip(",")
                    if pkg and not pkg.startswith("$"):
                        found.add(pkg)
    return sorted(found)


def _assistant_blocks(rec: dict) -> list[dict]:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _user_tool_results(rec: dict) -> list[dict]:
    """user 메시지 안의 tool_result 블록들 (assistant 도구 호출 응답)."""
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
    return []


def _summarize_window(records: list[dict]) -> dict:
    """lookback 윈도우 안의 records 에서 통계/시그널 추출."""
    tool_calls: Counter[str] = Counter()
    files_touched: set[str] = set()
    bash_commands: list[str] = []
    first_user_prompt: Optional[str] = None
    user_msg_count = 0
    assistant_msg_count = 0
    error_snippets: list[str] = []

    for r in records:
        t = r.get("type")

        if t == "user":
            # 첫 자연어 prompt
            text = _extract_user_text(r.get("message"))
            if first_user_prompt is None and _is_natural_prompt(text):
                first_user_prompt = text
            if text:
                user_msg_count += 1
            # tool_result 안의 에러 (사용자 거절은 막힌 지점이 아니므로 제외)
            for tr in _user_tool_results(r):
                if not tr.get("is_error"):
                    continue
                snippet = tr.get("content", "")
                if isinstance(snippet, list):
                    snippet = " ".join(
                        b.get("text", "") for b in snippet if isinstance(b, dict)
                    )
                snippet = str(snippet)
                if not snippet:
                    continue
                if any(marker in snippet for marker in _REJECTED_NOT_STUCK_MARKERS):
                    continue
                error_snippets.append(_shorten(snippet, 160))

        elif t == "assistant":
            assistant_msg_count += 1
            for block in _assistant_blocks(r):
                btype = block.get("type")
                if btype == "tool_use":
                    name = block.get("name", "?")
                    tool_calls[name] += 1
                    inp = block.get("input") or {}
                    if name in ("Edit", "Write", "NotebookEdit"):
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp:
                            files_touched.add(str(fp))
                    elif name == "Bash":
                        cmd = inp.get("command", "")
                        if isinstance(cmd, str) and cmd:
                            bash_commands.append(cmd[:300])

    return {
        "tool_call_counts": dict(tool_calls),
        "tool_call_total": sum(tool_calls.values()),
        "files_touched": sorted(files_touched)[:30],
        "files_touched_count": len(files_touched),
        "bash_commands_sample": bash_commands[:5],
        "bash_commands_count": len(bash_commands),
        "first_user_prompt": first_user_prompt,
        "user_msg_count": user_msg_count,
        "assistant_msg_count": assistant_msg_count,
        "errors_sample": error_snippets[:5],
        "errors_count": len(error_snippets),
        "library_hints": _extract_library_hints(bash_commands),
    }


def _format_readable_summary(stats: dict, duration_min: float, project_label: str) -> str:
    lines: list[str] = []
    if stats["first_user_prompt"]:
        lines.append(f'요청: "{_shorten(stats["first_user_prompt"], 140)}"')
    lines.append(
        f"활동: {stats['user_msg_count']} user / {stats['assistant_msg_count']} assistant 메시지, "
        f"{round(duration_min)}분 소요 (project: {project_label})"
    )
    if stats["tool_call_total"]:
        top_tools = sorted(
            stats["tool_call_counts"].items(), key=lambda x: -x[1]
        )[:5]
        lines.append(
            "도구 호출: " + ", ".join(f"{n}×{c}" for n, c in top_tools)
        )
    if stats["files_touched_count"]:
        sample = ", ".join(Path(p).name for p in stats["files_touched"][:4])
        more = stats["files_touched_count"] - 4
        if more > 0:
            sample += f" 외 {more}개"
        lines.append(f"파일 변경: {sample}")
    if stats["library_hints"]:
        lines.append(f"라이브러리 사용: {', '.join(stats['library_hints'])}")
    if stats["errors_count"]:
        lines.append(f"막힌 지점 {stats['errors_count']}건 (예: {stats['errors_sample'][0]})")
    return "\n".join(lines)


def _build_event_for_session(
    session_file: Path,
    cutoff: datetime,
    min_session_duration_minutes: int,
) -> Optional[Event]:
    records = list(_read_jsonl(session_file))
    if not records:
        return None

    # lookback 윈도우 안의 records 만
    in_window = [r for r in records if (ts := _record_ts(r)) is not None and ts >= cutoff]
    if not in_window:
        return None

    timestamps = [t for t in (_record_ts(r) for r in in_window) if t is not None]
    if not timestamps:
        return None
    first_ts = min(timestamps)
    last_ts = max(timestamps)
    duration_min = (last_ts - first_ts).total_seconds() / 60

    if duration_min < min_session_duration_minutes:
        return None

    # 세션 메타 — 모든 records 에서 첫 등장 값
    session_id = next(
        (r.get("sessionId") for r in records if r.get("sessionId")),
        session_file.stem,
    )
    cwd = next((r.get("cwd") for r in records if r.get("cwd")), "")
    git_branch = next((r.get("gitBranch") for r in records if r.get("gitBranch")), "")
    project_label = Path(cwd).name if cwd else session_file.parent.name

    stats = _summarize_window(in_window)
    readable = _format_readable_summary(stats, duration_min, project_label)

    title = (
        _shorten(stats["first_user_prompt"] or f"세션 작업 ({project_label})", 100)
    )

    # id: session-day. 같은 세션이 며칠에 걸쳐 이어져도 day 단위로 분리
    day_key = last_ts.date().isoformat()
    event_id = f"claude_session:{session_id}:{day_key}"

    return Event(
        id=event_id,
        source=SourceType.CLAUDE_SESSION,
        occurred_at=last_ts,
        collected_at=datetime.now(timezone.utc),
        title=title,
        summary=readable,
        body=None,
        url=None,
        tags=[project_label, *stats["library_hints"]],
        metadata={
            "session_id": session_id,
            "session_file": str(session_file),
            "project_cwd": cwd,
            "git_branch": git_branch,
            "duration_minutes": round(duration_min, 1),
            "tool_call_counts": stats["tool_call_counts"],
            "files_touched": stats["files_touched"],
            "files_touched_count": stats["files_touched_count"],
            "bash_commands_sample": stats["bash_commands_sample"],
            "errors_sample": stats["errors_sample"],
            "errors_count": stats["errors_count"],
            "library_hints": stats["library_hints"],
            "first_user_prompt": stats["first_user_prompt"] or "",
            "user_msg_count": stats["user_msg_count"],
            "assistant_msg_count": stats["assistant_msg_count"],
        },
    )
