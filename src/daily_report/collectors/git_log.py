"""Git log collector — 로컬 repo 의 커밋을 Event[] 로 변환.

커밋당 변경 파일 경로를 매칭해 `file_category` 를 Event.metadata 에 push (Option A).
classification 룰:
  - 모든 경로는 `.lower()` 정규화 후 fnmatch 매칭 (Windows / 대소문자 혼합 대응).
  - **agent_path_patterns 가 backend 보다 우선** — 같은 파일이 두 룰에 매치되면 agent 로 분류.
  - dominant category (다수파 우선, 동률 → mixed, 매칭 0 → unknown) 를 반환.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..schemas import Event, FileCategory, METADATA_KEY_FILE_CATEGORY, SourceType

# git log 한 줄을 명확하게 split 하기 위해 ASCII 제어문자 사용
_FIELD_SEP = "\x1f"   # Unit Separator
_RECORD_SEP = "\x1e"  # Record Separator

_MAX_CHANGED_FILES_STORED = 30


@lru_cache(maxsize=512)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a glob pattern (with `**` semantics) to a compiled regex.

    Glob semantics:
      - `**`     : any number of directories (including zero); only at path-component boundary
      - `*`      : any chars within a single path component (no `/`)
      - `?`      : any single char (no `/`)
      - `[...]`  : character class
      - else     : literal

    Patterns are matched against lowercase paths normalized to forward slashes.
    """
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            # `**` handling — only if surrounded by `/` or at start/end
            if i + 1 < n and pattern[i + 1] == "*":
                # `**/` at start or middle  → zero or more dirs incl trailing slash
                if i + 2 < n and pattern[i + 2] == "/":
                    parts.append("(?:.*/)?")
                    i += 3
                    continue
                # `/**` at end or `**` alone at end → any chars across slashes
                parts.append(".*")
                i += 2
                continue
            # single `*` — within a path component
            parts.append("[^/]*")
            i += 1
            continue
        if c == "?":
            parts.append("[^/]")
            i += 1
            continue
        if c == "[":
            j = i + 1
            if j < n and pattern[j] in ("!", "^"):
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j < n:
                cls = pattern[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                parts.append(f"[{cls}]")
                i = j + 1
                continue
            # unmatched `[` — treat literally
            parts.append(re.escape(c))
            i += 1
            continue
        parts.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(parts) + "$")


def _matches_glob(path_lower: str, pattern_lower: str) -> bool:
    return _glob_to_regex(pattern_lower).match(path_lower) is not None


def _matches_any(path_lower: str, patterns: list[str]) -> bool:
    return any(_matches_glob(path_lower, p.lower()) for p in patterns)


def _classify_files(
    files: list[str],
    backend_patterns: list[str],
    agent_patterns: list[str],
) -> tuple[FileCategory, dict]:
    """변경 파일 리스트를 dominant FileCategory 로 분류.

    Returns:
        (category, counts) 튜플. counts 는 {'backend_file_count': int, 'agent_file_count': int}.

    Rules:
        - 모든 경로 `.lower()` 정규화 후 fnmatch
        - agent 가 먼저 매칭 (한 파일이 두 룰에 모두 걸리면 agent)
        - 동률 → 'mixed', 양쪽 매칭 0 → 'unknown'
    """
    backend_count = 0
    agent_count = 0
    for fp in files:
        path_lower = fp.lower().replace("\\", "/")
        # agent 우선
        if _matches_any(path_lower, agent_patterns):
            agent_count += 1
            continue
        if _matches_any(path_lower, backend_patterns):
            backend_count += 1
    counts = {
        "backend_file_count": backend_count,
        "agent_file_count": agent_count,
    }
    if backend_count == 0 and agent_count == 0:
        return ("unknown", counts)
    if backend_count == agent_count:
        return ("mixed", counts)
    if backend_count > agent_count:
        return ("backend", counts)
    return ("agent", counts)


def _changed_files_for_commit(repo: Path, sha: str) -> list[str]:
    """`git show --name-only --format= <sha>` 결과를 한 줄씩 파싱."""
    try:
        proc = subprocess.run(
            ["git", "show", "--name-only", "--format=", sha],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    if proc.returncode != 0:
        return []
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return files


def collect_git_log(
    repo_path: str | Path,
    since_hours: int = 24,
    author_email: Optional[str] = None,
    branches: Optional[list[str]] = None,
    backend_patterns: Optional[list[str]] = None,
    agent_patterns: Optional[list[str]] = None,
) -> list[Event]:
    """지난 since_hours 시간 동안의 커밋을 Event[] 로 반환.

    .git 가 없거나 git 명령이 실패하면 빈 리스트.

    backend_patterns / agent_patterns 가 주어지면 변경 파일을 분류해
    `Event.metadata[file_category]` 에 push. 미지정 시 기존 동작 유지 ('unknown' 저장).
    """
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists():
        return []

    bp = backend_patterns or []
    ap = agent_patterns or []

    pretty = _FIELD_SEP.join(["%H", "%aI", "%an", "%ae", "%s", "%b"]) + _RECORD_SEP
    cmd: list[str] = [
        "git",
        "log",
        f"--since={since_hours} hours ago",
        f"--pretty=format:{pretty}",
        "--no-merges",
    ]
    if author_email:
        cmd.append(f"--author={author_email}")
    if branches:
        cmd.extend(branches)

    try:
        result = subprocess.run(
            cmd,
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []

    events: list[Event] = []
    now = datetime.now(timezone.utc)
    repo_label = repo.name

    for record in result.stdout.split(_RECORD_SEP):
        record = record.strip()
        if not record:
            continue
        parts = record.split(_FIELD_SEP)
        if len(parts) < 5:
            continue
        sha = parts[0]
        iso_date = parts[1]
        author_name = parts[2]
        email = parts[3]
        subject = parts[4]
        body = parts[5].strip() if len(parts) > 5 else ""

        # 파일 변경 통계 (실패해도 무시)
        files_stat = ""
        try:
            files_proc = subprocess.run(
                ["git", "show", "--stat", "--format=", sha],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if files_proc.returncode == 0:
                files_stat = files_proc.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        # 변경 파일 리스트 → file_category 분류 (NEW)
        changed_files = _changed_files_for_commit(repo, sha)
        file_category, file_counts = _classify_files(changed_files, bp, ap)

        # summary: subject + body 첫 200자
        if body:
            summary = f"{subject}\n\n{body[:200]}"
        else:
            summary = subject

        events.append(
            Event(
                id=f"git:{sha}",
                source=SourceType.GIT,
                occurred_at=datetime.fromisoformat(iso_date),
                collected_at=now,
                title=subject[:300],
                summary=summary,
                body=body or None,
                url=None,
                tags=[repo_label],
                metadata={
                    "sha": sha,
                    "author_name": author_name,
                    "author_email": email,
                    "repo": repo_label,
                    "files_stat": files_stat[-1000:],  # 너무 길면 자름
                    "changed_files": changed_files[:_MAX_CHANGED_FILES_STORED],
                    METADATA_KEY_FILE_CATEGORY: file_category,
                    **file_counts,
                },
            )
        )
    return events


def stable_event_id(*parts: str) -> str:
    """다른 collector 들도 쓸 수 있는 stable hash 헬퍼."""
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]
