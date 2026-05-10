# Architecture

## Goal
매일 아침 8시(KST) Discord에 일/주/월 개발 학습 리포트를 자동 발송. 사용자 프로필(레벨·관심사·약점)에 맞춰 CS·이론 보강 콘텐츠도 함께 큐레이션한다.

## Stack
- **Language**: Python 3.12
- **Packaging**: `uv` (또는 `pip` + `pyproject.toml`)
- **Validation**: `pydantic` v2
- **Storage**: SQLite (단일 파일 `data/state.db`)
- **LLM**: Google Gemini API (`google-genai` SDK, 무료 티어 활용 — Gemini 2.5 Flash)
- **Scheduler**: GitHub Actions cron (`0 23 * * *` UTC = 08:00 KST 다음날)
- **Discord**: Webhook (봇 권한 불필요, 추후 슬래시 커맨드 필요해지면 봇 전환)

## Directory Layout
```
playground-daily-report/
├── .github/workflows/
│   └── daily-report.yml          # cron trigger
├── config/
│   ├── profile.example.yaml      # 사용자 프로필 (레벨·관심사·톤)
│   └── sources.example.yaml      # 어떤 소스에서 끌어올지
├── data/
│   ├── schema.sql                # SQLite DDL
│   ├── state.db                  # gitignored, 런타임 생성
│   └── reports/                  # 발송된 리포트 markdown 아카이브
├── docs/
│   └── ARCHITECTURE.md
├── inbox/
│   └── pdf/                      # PDF 요약 대기열 (gitignored)
├── src/daily_report/
│   ├── __init__.py
│   ├── main.py                   # 오케스트레이터 (4 stage 순차 실행)
│   ├── schemas.py                # Pydantic 데이터 모델
│   ├── db.py                     # SQLite 접근 계층
│   ├── llm.py                    # Claude 클라이언트 (caching 포함)
│   ├── config.py                 # profile/sources 로더
│   │
│   ├── collectors/               # [팀1] 수집
│   │   ├── notion.py
│   │   ├── git_log.py
│   │   ├── claude_sessions.py
│   │   ├── web_scraper.py        # trafilatura 기반
│   │   └── pdf_summary.py
│   │
│   ├── processors/               # [팀2] 정규화·중복제거·클러스터링
│   │   ├── dedupe.py
│   │   ├── cluster.py            # 토픽 클러스터링 (LLM)
│   │   └── score.py              # 중요도 점수
│   │
│   ├── writers/                  # [팀3] 리포트 생성
│   │   ├── daily.py
│   │   ├── weekly.py
│   │   ├── monthly.py
│   │   └── deep_dive.py          # CS 보강 섹션
│   │
│   └── publishers/               # [팀4] 디스코드 발송
│       └── discord.py
│
├── tests/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## Data Flow
```
[07:30 KST] GitHub Actions trigger
   │
   ▼
┌──────────────┐  Event[]    ┌──────────────┐  TopicCluster[]  ┌──────────────┐  Report
│  collectors  │ ──────────▶ │  processors  │ ───────────────▶ │   writers    │ ─────┐
└──────────────┘             └──────────────┘                  └──────────────┘      │
       │                            │                                  │              ▼
       └─ events 테이블 적재 ──────┴────── 중복 ID 차단 ─────────────┘     ┌──────────────┐
                                                                          │  publishers  │
                                                                          └──────────────┘
                                                                                 │
                                                                                 ▼
                                                                          Discord channels
                                                                          (#daily, #weekly, ...)
                                                                                 │
                                                                                 ▼
                                                                          reports 테이블 적재
```

## Discord Channel Mapping
| Channel | Trigger | Source |
|---|---|---|
| `#daily` | 매일 08:00 | writers/daily.py |
| `#weekly` | 월요일 08:00 | writers/weekly.py |
| `#monthly` | 매월 1일 08:00 | writers/monthly.py |
| `#cs-foundations` | daily 와 동시 (별도 메시지) | writers/deep_dive.py |

### `#cs-foundations` 콘텐츠 정책
- **포커스**: 취업용 CS 기본기 + 사용 도구 내부 동작 (Spring/Redis/PostgreSQL/JVM 등)
- **연관성 우선**: 가능한 한 그날의 events 와 직접 연결된 개념을 다룸 (`relate_to_today_work: true`)
- **폴백**: 연관 토픽이 없으면 `profile.yaml > cs_foundations.fallback_topics` 풀에서 1개 선택
- **반복 방지**: 같은 개념은 `min_days_between_repeat` 내에 다시 다루지 않음 (`cs_concepts_covered` 테이블로 추적)
- **다중 레벨**: `profile.levels` 가 여러 개면 `CSFoundationsBlock.perspectives` 에 레벨별 관점을 분리 (예: 취준생 관점 + 주니어 관점)

## Pipeline Contracts
- **collectors → processors**: `list[Event]` (schemas.py 참조). 각 collector는 자신의 `SourceType`으로 채워서 반환.
- **processors → writers**: `list[TopicCluster]` + 원본 `list[Event]`.
- **writers → publishers**: `Report` (kind별 1개).
- **publishers**: Discord Embed로 직렬화 후 webhook POST. 발송 후 `reports` 테이블에 payload + message_id 기록.

## Idempotency
- `events.id`는 collector가 stable hash로 생성 (예: `git:<commit_sha>`, `notion:<page_id>:<edited_at>`).
- 같은 ID 재수집 시 UPSERT로 처리, processors는 이미 본 이벤트는 무시.
- 같은 period의 report가 이미 publish됐으면 재발송하지 않음 (`reports` 유니크 체크).
