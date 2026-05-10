# daily-report

> 매일 아침 8시(KST), 어제의 학습·작업을 자동 수집해서 디스코드로 발송하는 개인용 봇.
> CS·도구 기본기 보강(#cs-foundations)을 오늘 작업과 연결지어 같이 큐레이션.

## 무엇을 해주나요

- **#daily** — 어제 24h 활동 요약 (매일 KST 08:00)
- **#weekly** — 지난 7일 회고 + 다음 주 1-2 우선순위 (월요일 KST 08:00)
- **#monthly** — 지난 30일 회고 + 다음 달 학습 방향 (1일 KST 08:00)
- **#cs-foundations** — 오늘 작업과 연관된 CS / 도구 내부지식. 사용자 프로필이 `[backend_jobseeker, backend_junior]` 같이 복수면 두 관점 모두 통과시킴

수집 소스: **git** / **Notion** / **Claude Code 세션** / **임의 웹 URL** / **PDF**

## 5분 안에 띄우기 (로컬 dry-run)

```powershell
# 1. clone & 의존성 설치
git clone <your-fork-url>
cd playground-daily-report
pip install -e .

# 2. 외부 의존성 0 으로 풀체인 검증 (Discord 미발송, LLM 미호출)
$env:PYTHONPATH = "src"
python -m daily_report.main --dry-run --mock-llm --repo "C:/path/to/your/real/repo"
```

콘솔에 Discord 로 보낼 embed JSON 이 두 개 (daily + deep_dive) 출력되면 성공.

## 진짜로 발송하려면

### Step 1: Discord webhook 4 개 만들기

각 채널마다 **Server Settings → Integrations → Webhooks → New Webhook** 으로 URL 4개 (`#daily`/`#weekly`/`#monthly`/`#cs-foundations`).

### Step 2: `.env` 채우기

```powershell
copy .env.example .env
notepad .env
```

```env
# Gemini 무료 티어 (https://aistudio.google.com/app/apikey)
GOOGLE_API_KEY=AIza...
DISCORD_WEBHOOK_DAILY=https://discord.com/api/webhooks/.../...
DISCORD_WEBHOOK_WEEKLY=...
DISCORD_WEBHOOK_MONTHLY=...
DISCORD_WEBHOOK_CS=...
NOTION_API_TOKEN=secret_...   # Notion 안 쓰면 비워둬도 OK
GIT_AUTHOR_EMAIL=you@example.com
```

> 💡 **무료로 운영 가능**. Gemini 2.5 Flash 무료 티어 = 15 RPM / 1500 RPD. 우리는 하루 2-3회만 호출하므로 부담 없이 무료. Discord webhook 도 무제한 무료. 즉 0원으로 운영.

> ⚠️ **`.env` 는 gitignore 됨.** 절대 commit 하지 말 것. 채팅·공개 메시지에도 절대 붙여넣지 말 것.

### Step 3: `profile.yaml` / `sources.yaml` 본인 값으로

```powershell
copy config\profile.example.yaml config\profile.yaml
copy config\sources.example.yaml config\sources.yaml
```

`profile.yaml` 에서 자기 레벨·관심 도구·학습 목표·CS fallback 토픽 풀 조정.
`sources.yaml` 에서 실제 git repo 경로, Notion 페이지 ID, 스크래핑할 URL 등록.

### Step 4: 실제 1회 발송

```powershell
python -m daily_report.main --kind daily
```

## GitHub Actions 로 자동화

### Step 1: repo 를 GitHub 에 push

### Step 2: Repository Settings → Secrets and variables → Actions → "New repository secret"

다음 secrets 등록:
| Secret 이름 | 값 |
|---|---|
| `GOOGLE_API_KEY` | `AIza...` (Google AI Studio 발급) |
| `DISCORD_WEBHOOK_DAILY` | webhook URL |
| `DISCORD_WEBHOOK_WEEKLY` | webhook URL |
| `DISCORD_WEBHOOK_MONTHLY` | webhook URL |
| `DISCORD_WEBHOOK_CS` | webhook URL |
| `NOTION_API_TOKEN` | (선택) Notion 통합 토큰 |
| `GIT_AUTHOR_EMAIL` | 본인 git 커밋 이메일 |
| `PERSONAL_GITHUB_TOKEN` | (선택) PR/이슈 추적용 PAT |

### Step 3: 첫 번째 commit 으로 `data/state.db` 생성

```powershell
python -m daily_report.main --dry-run --mock-llm --repo "."
git add data\state.db
git commit -m "chore: initial state.db"
git push
```

(state.db 는 회차 간 멱등성을 위해 GitHub Actions 가 자동으로 commit 하며 갱신함.)

### Step 4: 자동 트리거 확인

`.github/workflows/report.yml` 의 cron:
- **daily**: 매일 UTC 23:00 = **KST 08:00**
- **weekly**: 매주 일요일 UTC 23:00 = **KST 월요일 08:00**
- **monthly**: 매월 마지막날 UTC 23:00 = **KST 1일 08:00** (28-31 트리거 + "내일이 1일" 가드)

수동 트리거: **Actions → Daily / Weekly / Monthly Report → Run workflow → kind 선택**.

## 로컬 전용 소스 (Cloud 에선 자동 비활성)

GitHub Actions 러너는 사용자의 로컬 디렉토리에 접근 불가. 다음은 로컬에서만 의미 있음:

- **claude_sessions**: `~/.claude/projects/**/*.jsonl` 읽음 → CI 에선 빈 결과로 스킵
- **pdfs**: `inbox/pdf/` watch → CI 에선 빈 디렉토리

**이 소스들도 활용하려면**: 로컬 PC 에서 Windows Task Scheduler / Mac launchd / Linux cron 으로 같은 명령을 KST 08:00 에 돌리면 됨. 별도 webhook 으로 분리해도 좋고, 같은 채널로 보내도 됨.

## CLI 옵션

```
python -m daily_report.main [옵션]

--profile PATH         profile.yaml 경로 (default: config/profile.yaml, 없으면 example 사용)
--sources PATH         sources.yaml 경로
--db PATH              SQLite 파일 경로 (default: data/state.db)
--repo PATH            단일 git repo 강제 (sources.yaml 의 git.local_repos 무시)
--kind {daily,weekly,monthly}  발송할 리포트 종류 (default: daily)
--dry-run              Discord POST 안 함, payload 만 stdout
--mock-llm             Gemini API 호출 없이 캔드 응답 사용
--skip-deep-dive       #cs-foundations 발송 건너뛰기 (daily 만 해당)
```

## 아키텍처 한 장

```
[KST 08:00] GitHub Actions trigger
   │
   ▼
┌──────────────┐  Event[]    ┌──────────────┐  Report      ┌──────────────┐
│  collectors  │ ──────────▶ │   writers    │ ───────────▶ │  publishers  │
│ (5종)        │             │ (4종)        │              │  (Discord)   │
└──────────────┘             └──────────────┘              └──────────────┘
       │                            │                              │
       └─ events ── DB ── 멱등 ─────┴─ reports / cs_concepts ─────┘
                       (data/state.db)
```

자세한 데이터 흐름 / 디렉토리 구조 / Stage 간 계약은 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 참고.

## 테스트

```powershell
pytest tests/ -q
```

73 cases. 외부 의존성 (Gemini API, Discord, Notion API) 모두 의존성 주입 / mock 으로 우회.

## 주요 설계 결정

- **멱등성**: `events.PRIMARY KEY (source, id)` + ON CONFLICT UPDATE. 같은 commit / 같은 페이지 재수집 시 row 1개 유지
- **Mock-first 검증**: `--mock-llm` + `--dry-run` 으로 외부 의존성 0 으로 풀체인 회귀 가능
- **재현성**: 모든 `Report` 에 그 시점 `profile_snapshot` 동봉. 프로필을 바꿔도 과거 리포트 컨텍스트 유지
- **CS 보강 반복 방지**: `cs_concepts_covered` 테이블에 다룬 개념 기록, `min_days_between_repeat` (default 14) 안엔 같은 개념 안 골라줌
- **다중 레벨 통과**: `profile.levels` 가 복수면 `CSFoundationsBlock.perspectives` 에 레벨별 관점 분리 (예: `backend_jobseeker` vs `backend_junior`)

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `ModuleNotFoundError: No module named 'daily_report'` | `pip install -e .` 또는 `$env:PYTHONPATH = "src"` 설정 |
| Discord 발송됐는데 한글이 깨져 보임 | Discord 클라이언트 문제 가능성. embed JSON 자체는 UTF-8. 데스크탑 앱 재시작 |
| GitHub Actions 가 monthly 를 매일 트리거 | cron `0 23 28-31 * *` 는 매일 28-31 발화하지만 워크플로우 안 가드 (`내일이 1일`) 가 그 외 날짜는 skip 처리 |
| state.db 가 너무 커짐 | 1년에 ~10MB 정도 예상. 부담스러우면 오래된 events 정리 후 commit |
| Notion 페이지가 수집 안 됨 | Notion 통합에 페이지 access 부여했는지 확인 (페이지 → 우상단 ⋯ → Connections) |

## 라이센스

개인 사용. 본인 작업물.
