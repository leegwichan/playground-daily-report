"""결정론적 PR 클러스터러 — weekly writer 의 LLM 산출 면적 축소 (E3).

알고리즘:
  1. event subject 의 prefix (e.g. `feat:`, `fix:`, `refactor:`) 를 정규화 (lowercase, 공백 제거).
     prefix 가 없으면 'other'.
  2. event 의 `changed_files` 중 top-level dir (`src/`, `tests/`, `docs/`) 추출.
     동률이면 알파벳 순 첫 dir. 비어있으면 'root'.
  3. 그룹 키 = (subject_prefix_normalized_lowercase, top_level_dir).
  4. 각 그룹의 `cluster_id` = `sha256(prefix + ":" + top_dir).hexdigest()[:8]` — 동일 입력 → 동일 ID.
  5. 반환: list[PrCluster] — 각 cluster 는 `cluster_id`, `title`, `event_shas: list[str]`.

LLM 은 cluster_id 별 STAR 텍스트 + 면접 질문 3개만 채운다 (R1 mitigation).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Iterable

from ..schemas import Event, PrCluster, SourceType


_PREFIX_PATTERN = re.compile(r"^([a-zA-Z][\w-]*)\s*:")


def _normalize_prefix(subject: str) -> str:
    """conventional commit 의 prefix 추출 → lowercase. 없으면 'other'."""
    if not subject:
        return "other"
    m = _PREFIX_PATTERN.match(subject.strip())
    if not m:
        return "other"
    return m.group(1).strip().lower()


def _top_level_dir(changed_files: list[str]) -> str:
    """변경 파일들의 top-level dir 다수파 추출. 비어있으면 'root', 동률은 알파벳 순 첫."""
    if not changed_files:
        return "root"
    counts: Counter[str] = Counter()
    for fp in changed_files:
        # 경로 정규화: 백슬래시 → 슬래시
        norm = fp.replace("\\", "/").lstrip("/")
        if "/" in norm:
            top = norm.split("/", 1)[0]
        else:
            top = "root"
        if top:
            counts[top] += 1
    if not counts:
        return "root"
    # 다수파 → 알파벳 순 (안정적 tie-breaking)
    max_count = max(counts.values())
    tied = sorted(d for d, c in counts.items() if c == max_count)
    return tied[0]


def _cluster_id(prefix: str, top_dir: str) -> str:
    h = hashlib.sha256(f"{prefix}:{top_dir}".encode("utf-8")).hexdigest()
    return h[:8]


def cluster_pr_events(events: Iterable[Event]) -> list[PrCluster]:
    """git 이벤트들을 (prefix, top_level_dir) 기준으로 결정론적 클러스터링.

    Args:
        events: git collector 가 만든 Event 리스트 (file_category 무관 — caller 가 사전 필터).

    Returns:
        cluster_id 알파벳 정렬된 PrCluster 리스트. 동일 입력 → 동일 출력.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    titles: dict[tuple[str, str], str] = {}
    for ev in events:
        if ev.source != SourceType.GIT:
            continue
        prefix = _normalize_prefix(ev.title)
        changed = ev.metadata.get("changed_files") or []
        if not isinstance(changed, list):
            changed = []
        top_dir = _top_level_dir([str(c) for c in changed])
        key = (prefix, top_dir)
        sha = ev.metadata.get("sha")
        if not isinstance(sha, str) or not sha:
            sha = ev.id  # fallback
        groups.setdefault(key, []).append(sha)
        titles.setdefault(key, f"{prefix}:{top_dir}")

    clusters: list[PrCluster] = []
    for (prefix, top_dir), shas in groups.items():
        cid = _cluster_id(prefix, top_dir)
        clusters.append(
            PrCluster(
                cluster_id=cid,
                title=titles[(prefix, top_dir)],
                event_shas=shas,
            )
        )
    # cluster_id 정렬 → run 간 동일 순서
    clusters.sort(key=lambda c: c.cluster_id)
    return clusters
