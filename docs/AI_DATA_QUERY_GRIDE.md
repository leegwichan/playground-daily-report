네, 채팅에 직접 붙여드릴게요. 푸시는 막혀 있지만 가이드 내용 자체는 여기서 바로 보실 수 있습니다.

---

# AI 데이터 질의 가이드

> 리포트가 마음에 들지 않을 때 어디를 만져야 하는지를 위한 안내서.
> "내 데이터가 어떻게 모여서, AI 에게 정확히 어떻게 물어봐서, 어떻게 리포트가 되는가" 를 코드 한 줄까지 따라간다.

핵심 한 줄: **collectors 가 만든 `Event[]` → writer 가 system / user 프롬프트로 조립 → Gemini 가 JSON 으로 답 → `Report` 모델로 파싱 → Discord 발송.**

---

## 1. 한 장 요약

```
[내 활동]    →   [정규화]   →   [프롬프트 조립]      →   [LLM 호출]   →   [후처리]
git/notion/        Event[]       system + 프로필         Gemini           Report
claude/web/                      user + Event 블록      2.5 Flash        + JSON 파싱
pdf                                                                       ↓
                                                                       Discord embed
```

---

## 2. Stage 1 — 내 데이터를 `Event` 로 정규화

`src/daily_report/collectors/` 아래 5 개 모듈이 각자 자기 소스를 읽어서 **모두 같은 모양** 의 `Event` 로 만든다.

### 2-1. `Event` 의 모양 (`schemas.py:30`)

```python
class Event(BaseModel):
    id: str          # stable hash. 재실행 시 중복 방지 (예: "git:<sha>")
    source: SourceType   # notion | git | claude_session | web | pdf
    occurred_at: datetime
    collected_at: datetime
    title: str
    summary: str     # 2-4 문장. ★ AI 가 실제로 읽는 핵심 텍스트
    body: str | None
    url: HttpUrl | None
    tags: list[str]
    metadata: dict   # source-specific (sha, page_id, tool_call_counts, ...)
```

**핵심**: AI 는 `body` 전문을 보지 않는다. **`title` + `summary` + 일부 `metadata`** 만 읽는다 (토큰 절약). 따라서 collector 가 `summary` 를 어떻게 짜주느냐가 결과물의 절반을 결정한다.

### 2-2. 어떤 collector 가 어떤 신호를 뽑는가

| Collector | 무엇을 읽나 | summary 에 담는 것 | 추가 metadata |
|---|---|---|---|
| **git_log** | `git log --since=24h` | 커밋 subject + body 200자 | `files_stat` |
| **claude_sessions** | `~/.claude/projects/**/*.jsonl` | 첫 자연어 프롬프트 + 작업 요약 | `tool_call_counts`, `library_hints`, `errors` |
| **web_pages** | RSS feed + one-off URL | trafilatura 본문 요약 | `label` |
| **notion** | Notion API page/database | 페이지 제목 + 첫 블록 | `page_label` |
| **pdf_summary** | `inbox/pdf/*.pdf` | 추출 텍스트 앞부분 | `file_name` |

`main.py:54` 의 `_collect_all_events()` 가 `sources.yaml` 에서 `enabled: true` 인 것만 돌려서 모은다.

> **튜닝 포인트 ①** — 결과물의 "재료" 부족이면 `config/sources.yaml` 에서 collector 활성화 / lookback 시간 / repo 경로 / RSS 피드 점검.

---

## 3. Stage 2 — `Event[]` 를 LLM 프롬프트 텍스트로 변환

### 3-1. `render_events()` (`writers/_common.py:38`)

```python
def render_events(events: list[Event]) -> str:
    blocks = []
    for ev in events:
        meta_hint = _source_specific_hint(ev)
        blocks.append(
            f"- [{ev.source.value}] {ev.title}\n"
            f"  {ev.summary[:200]}{meta_hint}"
        )
    return "\n\n".join(blocks)
```

LLM 에게 가는 1 개 이벤트 예시:

```
- [git] feat: Redis 캐시 레이어 리팩토링
  GET /api/users 응답 캐싱을 추가. TTL 60초, key 는 user_id 기반...
  파일변경: 3 files changed, 87 insertions(+), 12 deletions(-)
```

**source 별 metadata 힌트** (`_source_specific_hint()`, `_common.py:56`):

| Source | 추가되는 한 줄 |
|---|---|
| (공통 prefix) | `분류: backend (backend×3, agent×0)` — file_category 노출 |
| git | `파일변경: <files_stat 마지막 줄>` |
| claude_session | `도구: Edit×12, Bash×7 / 라이브러리: pydantic, pytest` |
| web | `소스: <피드 라벨>` |
| notion | `Notion: <페이지 라벨>` |
| pdf | `PDF: <파일명>` |

**file_category prefix** (백엔드 도메인 집중 모드, AC #10):
- 모든 source 에서 `분류: backend|agent|mixed` 한 줄을 LLM 에게 노출 (`unknown` 은 생략).
- LLM 이 시스템 프롬프트의 "agent 이벤트 본문 인용 금지" 룰을 인지하기 쉬워진다.
- deep_dive 의 `_render_events()` 도 동일 prefix 를 받음 (`_common._source_specific_hint` 재사용 — DRY).

> **튜닝 포인트 ②** — "내가 했던 어떤 신호" 가 빠진 것 같으면 `_source_specific_hint()` 에 metadata 노출 추가가 가장 빠르다.

### 3-2. 프로필을 system prompt 변수로 (`_common.py:91`)

```python
def format_profile_block(cfg: AppConfig) -> dict:
    p = cfg.profile
    return {
        "levels":              ", ".join(p.levels),
        "experience_years":    p.experience_years,
        "current_focus":       ", ".join(p.current_focus) or "(미지정)",
        "learning_goals":      "; ".join(p.learning_goals) or "(미지정)",
        "weak_areas":          ", ".join(p.weak_areas) or "(미지정)",
        "tone":                p.tone,
        "daily_read_minutes":  p.daily_read_minutes,
        "language":            p.language,
    }
```

> **튜닝 포인트 ③** — 톤·깊이·관점은 거의 항상 `config/profile.yaml` 만 손보면 된다. 프롬프트 수정 불필요 (예: `tone: morning_calm → concise`).

---

## 4. Stage 3 — 실제로 AI 에게 던지는 프롬프트

| Writer | 책임 |
|---|---|
| `daily` | 어제 24h 활동 요약 |
| `weekly` | 지난 7일 회고 + 다음 주 우선순위 |
| `monthly` | 지난 30일 메이저 토픽 + 역량 변화 |
| `deep_dive` | 오늘 작업과 연관된 CS / 도구 내부지식 1 개 |

### 4-1. daily writer 의 system prompt (`writers/daily.py:13`)

```
당신은 한 명의 개발자에게 매일 아침 8시에 전달되는 학습 리포트 작성자입니다.

독자 프로필:
- 레벨(복수 가능): {levels}
- 경력: {experience_years}년
- 현재 포커스: {current_focus}
- 학습 목표: {learning_goals}
- 약한 영역: {weak_areas}

작성 가이드:
- 톤: {tone} — 출근길 약 {daily_read_minutes}분에 차분히 읽기 좋게.
- 언어: {language}
- 독자가 여러 레벨이면 모든 관점이 자연스럽게 통과하도록 작성.
- 군더더기 없이 정보 밀도를 유지하되, 압박감은 주지 말 것.
- 코드 인용은 짧게, 핵심만.

응답 형식: 아래 JSON 스키마로만 답변하세요. 코드 펜스나 설명 텍스트 금지.
{
  "title": "Daily Report YYYY-MM-DD",
  "tldr": [...],
  "sections": [{"heading": "...", "body_md": "...", "sources": []}],
  "estimated_read_minutes": 정수
}
```

**user prompt**:

```
오늘({date}) 수집된 이벤트 {count}개:

{events_block}

위 이벤트를 바탕으로 일간 리포트를 작성해주세요.
```

조립 (`writers/daily.py:82`):

```python
system = _SYSTEM_PROMPT.format(**format_profile_block(cfg))
user   = _USER_PROMPT.format(
    date=now.strftime("%Y-%m-%d"),
    count=len(events),
    events_block=render_events(events),
)
raw = llm.complete(system, user, max_tokens=4000, mock_kind="daily", json_mode=True)
parsed = parse_json(raw)
```

### 4-2. deep_dive 만 다른 점

`writers/deep_dive.py` 의 user prompt 는 **두 가지 컨텍스트** 를 추가:

1. **`covered`** — 최근 `min_days_between_repeat` 일 안에 다룬 개념 (DB `cs_concepts_covered` 테이블). LLM 에게 "이건 피해라" 명시.
2. **`available_fallbacks`** — `profile.yaml > cs_foundations.fallback_topics` 풀 - covered. 오늘 이벤트와 연관 토픽 못 찾을 때만 쓰라고 지시.

```
오늘({date}) 수집된 이벤트 {count}개:
{events_block}

이미 최근 {min_days}일 안에 다뤄서 이번엔 *피해야 할* 개념:
{covered_block}

오늘 작업과 연관된 개념을 못 찾을 경우에만 이 폴백 풀에서 1개 선택:
{fallback_block}
```

> **튜닝 포인트 ④** — `#cs-foundations` 가 매번 비슷한 주제만? `fallback_topics` 풀을 20–30 개로 늘리고, `min_days_between_repeat` 을 14 → 30 으로.

---

## 5. Stage 4 — Gemini 호출 (`llm.py:183`)

```python
def complete(self, system, user, max_tokens=4000, *, mock_kind="daily", json_mode=False):
    if self.mock:
        return _MOCK_RESPONSES[mock_kind]   # --mock-llm 일 때

    config_kwargs = {
        "system_instruction": system,
        "max_output_tokens": max_tokens,
        "temperature": 0.4,                 # JSON 강제라 약간 낮게
    }
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"   # ★

    response = self._client.models.generate_content(
        model=self.model,                   # gemini-2.5-flash
        contents=user,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return response.text or ""
```

핵심:
- **`temperature=0.4`**: JSON 안정성 우선
- **`response_mime_type="application/json"`**: native structured output 강제 → 코드 펜스/잡담 차단
- **모델**: `profile.yaml > llm.writer_model` (기본 `gemini-2.5-flash`). 깊이 필요시 `gemini-2.5-pro`

> **튜닝 포인트 ⑤** — 결과물이 너무 단조롭다면 `temperature` 0.6–0.8. JSON 깨짐 잦으면 더 낮추거나 `max_tokens` 증가 (긴 답이 잘리면 닫는 `}` 사라져 파싱 실패).

---

## 6. Stage 5 — 응답 파싱과 `Report` 조립

### 6-1. `parse_json()` — 관용적 파서 (`_common.py:16`)

```python
def parse_json(raw: str) -> dict:
    s = raw.strip()
    if s.startswith("```"):              # 코드 펜스 제거
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        s = s[start : end + 1]
    return json.loads(s, strict=False)
```

### 6-2. `Report` 캐스팅 (`writers/daily.py:92`)

```python
return Report(
    kind="daily",
    period_start=period_start,
    period_end=period_end,
    title=parsed["title"],
    tldr=parsed["tldr"],
    sections=[ReportSection(**s) for s in parsed["sections"]],
    estimated_read_minutes=int(parsed["estimated_read_minutes"]),
    generated_at=datetime.now(timezone.utc),
    profile_snapshot=cfg.profile.model_dump(),   # ★ 재현성
)
```

`profile_snapshot` 이 통째 들어가는 게 포인트. 프로필을 나중에 바꿔도 과거 리포트는 그 시점 프로필을 그대로 들고 있음.

deep_dive 만 한 단계 더 — `CSFoundationsBlock` 캐스팅 후 (`writers/deep_dive.py:205`), Discord 가독성에 맞게 sections 로 풀어줌.

---

## 7. Stage 6 — publishers

`publishers/discord.py` 가 `Report` 를 Discord embed JSON 으로 직렬화 → webhook POST. AI 와 무관.

### 7-1. Footer dispatch (백엔드 도메인 집중 모드)

`report_to_embeds()` 가 `report.kind` 에 따라 footer 텍스트를 직접 부착 — LLM 협조 의존 제거 (AC #19):

```python
footer_text = f"~{report.estimated_read_minutes}분 읽기"
if report.kind in ("weekly", "monthly"):
    footer_text += " · 직접 골라 이력서에 옮길 후보"
```

→ weekly/monthly 의 모든 embed footer 에 자동으로 "직접 골라 이력서에 옮길 후보" 표기. daily/deep_dive 는 미부착.

---

## 7.5 백엔드 도메인 집중 모드 — 추가 파이프라인

### file_category (Event.metadata 의 새 키)

git collector 가 커밋당 변경 파일 경로를 분류해 `Event.metadata.file_category` 에 push.
값: `backend` / `agent` / `mixed` / `unknown`.

```python
# config/profile.yaml > event_classification
backend_path_patterns: []      # 사용자 *추가* 패턴 (디폴트와 합집합)
agent_path_patterns: []        # 사용자 *추가* 패턴
overlap_window_minutes: 30     # claude_session ↔ git 양보 윈도우
```

룰:
- 모든 경로는 lowercase 정규화 후 fnmatch + `**` 글로브 매칭.
- **agent 우선** — 한 파일이 두 룰에 모두 매치되면 agent (예: `.github/workflows/*.yml`).
- dominant (다수파) → 그 category, 동률 → `mixed`, 매칭 0 → `unknown`.

claude_session 이벤트는 `_resolve_collector_overlap()` (main.py) 가 같은 시간대 ±N분 git 이벤트로 양보 분류.

### writer 의 이벤트 필터 차이

| writer | 본문 인용 후보 | 분류 |
|---|---|---|
| daily | backend, mixed | 관대 |
| deep_dive | backend, mixed | 관대 |
| weekly | backend 만 | 엄격 (AC #13) |
| monthly | backend 만 | 엄격 (AC #16) |

### 결정론적 PR 클러스터러 (`processors/git_pr_clusterer.py`)

weekly 의 LLM 산출 면적 축소를 위한 사전 단계 (R1 mitigation):

```python
# 알고리즘:
# 1. subject prefix 정규화 (lowercase: "feat:", "fix:" → "feat", "fix")
# 2. changed_files 의 top_level_dir 다수파 (src/, tests/, docs/, root)
# 3. 그룹 키 = (prefix, top_dir)
# 4. cluster_id = sha256(prefix + ":" + top_dir).hexdigest()[:8]
```

→ 동일 입력 → 동일 cluster_id. LLM 은 클러스터당 STAR 1줄 + 면접 질문 3개만 채움.

### Weekly / Monthly 의 새 구조

**Weekly (2-section)**:
- 이력서 재료: `cluster_pr_events()` 결과 + LLM 의 STAR + 면접 질문 3개. `WeeklyResumeBundle.model_validate()` 로 검증, `result_summary` 빈 cluster 는 코드가 자체 drop (AC #18).
- 개념 누적 지표: 이번 주 `cs_concepts_covered.tier` join + 면접 답변 연습 prompt.

**Monthly (3-section)**:
- 메이저 테마 (재클러스터링) — 4주 클러스터 합산 → 5~7개 테마.
- 진도도 지표 — `first_seen_count` / `revisit_count` / `primary:interest` 카운트. **코드가 결정론적으로 계산**, LLM 은 narrative.
- 다음 달 학습 방향 — read-only audit. tier 분배 enforcement 는 daily/deep_dive 가 단독 책임.

### `cs_concepts_covered.tier` 칼럼

```sql
ALTER TABLE cs_concepts_covered ADD COLUMN tier TEXT
  CHECK(tier IN ('primary','interest')) DEFAULT NULL;
```

deep_dive 의 `_choose_tier_for_today()` 가 `db.tier_distribution(within_days=28)` 으로 80:20 분배에서 부족한 쪽을 추천. 이 추천은 deep_dive call 직전 1회만 enforce.

---

## 8. 결과물이 마음에 안 들 때 — 순서대로 시도

1. **프로필 먼저** (가장 효과 크고 안전, `config/profile.yaml`):
   - `tone`: `morning_calm` / `concise` / `playful`
   - `daily_read_minutes`: 분량 (5 → 짧게, 30 → 풍부)
   - `levels`: 관점. 안 어울리는 레벨은 빼라
   - `current_focus` / `learning_goals` / `weak_areas`: 깊이·우선순위 직접 영향
   - `cs_foundations.fallback_topics`: deep_dive 다양성

2. **데이터 부족인지 확인** — `python -m daily_report.main --dry-run --mock-llm` 로 events 블록 직접 보기. summary 빈약하면 collector 손보기.

3. **프롬프트 자체 수정** — `writers/daily.py` 등의 `_SYSTEM_PROMPT` 에 가이드 줄 추가:
   - "각 섹션은 반드시 코드 예제 1 개"
   - "이모지 금지"
   - "분량은 정확히 3 섹션"

4. **모델 / 온도** — `llm.writer_model` 을 `gemini-2.5-pro` 로, 또는 `llm.py:226` 의 `temperature` 조정.

5. **JSON 깨짐** — `max_tokens` 부족이 가장 흔함. `4000` → `6000–8000`.

---

## 9. 디버깅 한 줄 명령

외부 의존성 0 풀체인:
```bash
python -m daily_report.main --dry-run --mock-llm --repo "."
```

실제 LLM 호출, Discord 만 안 보냄:
```bash
python -m daily_report.main --dry-run --kind daily
```

특정 repo 의 어제 24h 만:
```bash
python -m daily_report.main --dry-run --repo "/path/to/repo" --skip-deep-dive
```

---

이게 전부입니다. 가장 자주 손볼 곳은 **`config/profile.yaml` (튜닝 ③)** 과 **각 writer 의 `_SYSTEM_PROMPT`** 두 곳입니다. 푸시 권한 풀리면 알려주세요. 다시 시도하겠습니다.

여전히 403 입니다. 같은 환경 권한 문제라 retry 로 풀리지 않습니다 — 핵 hook 은 환경 측 권한이 풀리거나 데스크톱에서 직접 push 하시기 전까지 매번 발화할 겁니다. 작업 자체는 로컬 커밋(`2c93cb4`)에 안전하게 보존되어 있고 손실 위험은 없습니다.