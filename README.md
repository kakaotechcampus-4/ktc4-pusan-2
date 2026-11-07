# ktc4-team-12
카카오테크 캠퍼스 4기 2단계 팀 프로젝트 — 부산대 2팀

## 프로젝트 소개

사용자의 실제 발표 연습을 카메라와 마이크로 분석해 말하기·시선·대본 의존·시간 관리를 코칭하고, 여러 번의 Take를 통해 발표를 개선하는 서비스 피치코치입니다.

## 목적

대본과 발표자료는 LLM의 도움으로 만드는 것이 쉬워졌지만, 그 내용을 잘 전달하는 발표 과정을 객관적으로 확인하고 무엇을 개선할지 판별하는 것은 아직 어렵습니다. 

그래서, 반복 연습을 설계하고 실제 발표 행동을 측정하며 그 결과로 사용자의 발표 특성을 파악해 지도, 다음 연습에도 반영되도록 하는 피치코치 서비스를 기획하게 되었습니다.

## 전체 핵심 기능
- 실제 발표 행동 측정
    - 목소리 음량, 편차
    - 군더더기, 발화 사이 침묵 길이와 빈도
    - 시간 내로 마쳤는지, 얼마나 근접하게 맞췄는지
- 대본 의존도 파악
    - 시선 분석
    - 발화 - 대본 간 유사도 분석
- Take 데이터를 바탕으로 다음 Take의 미션 제시
- 발표 맞춤 대화형 Agent
    - 이전 발표에 대한 확인
    - 특정 조건이 변화한 발표에 대한 재평가
    - 연습 화면 이동

## 시스템 아키텍처 구조도

## 폴더 구조

```
pitch-coach/
├── .github/              # 이슈 · PR 템플릿, 워크플로
├── ai/
│   ├── service/          # AI 서버 (FastAPI)
│   ├── research/         # 기능별 연구 · 실험 (코치, 시선, 대본 파싱 등)
│   └── archive/          # 지난 작업 보관
├── backend/
│   ├── src/              # BE 서버 (FastAPI)
│   ├── alembic/          # DB 마이그레이션
│   ├── tests/            # 테스트
│   └── dev/              # 로컬 확인용 도구 (OAuth · STT 테스트)
├── frontend/
│   ├── src/              # FE 앱
│   ├── public/           # 정적 파일
│   └── scripts/          # 개발 보조 스크립트
├── docs/
│   └── ai/               # AI 설계 문서 (아키텍처, 기능별 명세)
└── infra/                # docker-compose, Caddy 설정

```

## 실행 방법
0. 공통 준비
uv: BE는 Python 3.14, AI는 3.12를 씁니다. 버전은 각 폴더의 .python-version대로 uv가 받습니다.
Node 24(nvm use 24). 버전이 다르면 .npmrc의 engine-strict 때문에 npm ci가 멈춥니다.
Docker. 로컬 DB를 띄울 때 씁니다.
환경 파일 세 개를 만듭니다.
backend/.env ← backend/.env.example. JWT_SECRET_KEY는 openssl rand -hex 32로 만듭니다.
ai/.env ← ai/.env.example
frontend/.env.local ← frontend/.env.example

2. DB (PostgreSQL)

cd infra
docker compose up -d db
db 서비스는 backend/.env의 POSTGRES_* 값을 읽습니다. 그래서 backend/.env를 먼저 만들어 두셔야 합니다.

2. AI 서버 (포트 8001)

cd ai/service
uv sync --locked
uv run python -m uvicorn pitch_coach_ai.main:app --reload --port 8001
/health가 뜨는 데는 LLM 키가 필요 없습니다. 실제 LLM 기능을 부를 때만 ai/.env에 키를 채우면 됩니다.

3. BE (포트 8000)

cd backend
uv sync
uv run alembic upgrade head          # DB가 떠 있어야 합니다
uv run fastapi dev src/pitch_coach_backend/main.py
AI_BASE_URL 기본값은 http://localhost:8001이라 2번과 맞습니다. Deepgram 키, S3 버킷, 구글 OAuth 값이 비어 있으면 서버는 뜨지만 실시간 STT, 업로드, 로그인은 동작하지 않습니다.

4. FE (포트 3000)

cd frontend
npm ci
npm run doctor     # 실패 0개면 세팅이 끝난 것입니다
npm run dev
FE는 기본이 MSW 목 서버입니다. 실제 BE에 붙이려면 .env.local에 VITE_USE_MOCK=false를 넣어야 합니다.

## 직군별 README.md

| 구분 | 파일 위치 |
|---|---|
| FE | /fe/README.md |
| BE | /be/README.md |
| AI | /ai/README.md |
| Infra | /infra/README.md |

## Git 컨벤션

같은 작업이 세 곳에서 각각 이렇게 작성됩니다.

| 위치 | 형식 | 예 |
|---|---|---|
| 브랜치 | `타입/영역-작업명` | `feat/be-login-api` |
| PR 제목 | `[영역] 타입: 작업 내용` | `[BE] feat: 로그인 API 구현` |
| 커밋 메시지 | `타입(영역): 작업 내용` | `feat(be): 로그인 API 구현` |

**PR 제목의 영역만 대문자**(`[BE]`)이고, 브랜치와 커밋은 소문자(`be`)입니다.

### 타입

| 타입 | 쓰는 때 |
|---|---|
| `feat` | 기능 추가 |
| `fix` | 버그 수정 |
| `refactor` | 동작을 바꾸지 않는 구조 개선 |
| `test` | 테스트 추가·수정 |
| `docs` | 문서 |
| `chore` | 설정·의존성·빌드 등 그 외 |

### 영역

| 코드 | 범위 |
|---|---|
| `fe` | 프론트엔드 |
| `be` | 백엔드 |
| `ai` | AI |

### 브랜치 — `타입/영역-작업명`

```
feat/fe-login-page
feat/be-login-api
feat/ai-feedback
```

작업명은 소문자와 하이픈으로 작성합니다. 
팀 내부 작업은 `develop`에서 분기시키고 `develop`으로 PR 을 보냅니다.
`main` 은 멘토 리뷰용으로 사용합니다.

### PR 제목 — `[영역] 타입: 작업 내용`

```
[BE] feat: 로그인 API 구현
```

### 커밋 메시지 — `타입(영역): 작업 내용`

```
feat(be): 로그인 API 구현
```

## 역할 구성

| 구분 | 파일 위치 |
|---|---|
| PM | 김제원 |
| FE | 김준수(리드), 안정환 |
| BE | 정유진(리드), 문여원 |
| AI | 이서진(리드), 김제원 |
| Infra | 정유진 |
