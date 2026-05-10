"""Git log collector — 로컬 repo 의 커밋을 Event[] 로 변환."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..schemas import Event, SourceType

# git log 한 줄을 명확하게 split 하기 위해 ASCII 제어문자 사용
_FIELD_SEP = "\x1f"   # Unit Separator
_RECORD_SEP = "\x1e"  # Record Separator


def collect_git_log(
    repo_path: str | Path,
    since_hours: int = 24,
    author_email: Optional[str] = None,
    branches: Optional[list[str]] = None,
) -> list[Event]:
    """지난 since_hours 시간 동안의 커밋을 Event[] 로 반환.

    .git 가 없거나 git 명령이 실패하면 빈 리스트.
    """
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists():
        return []

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
