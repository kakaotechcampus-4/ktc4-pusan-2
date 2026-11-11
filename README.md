# ktc4-team-12
카카오테크 캠퍼스 4기 2단계 팀 프로젝트 — 부산대 2팀

## 프로젝트 소개

사용자의 실제 발표 연습을 카메라와 마이크로 분석해 말하기·시선·대본 의존·시간 관리를 코칭하고, 여러 번의 Take를 통해 발표를 개선하는 서비스 피치코치입니다.

## 목적

대본과 발표자료는 LLM의 도움으로 만드는 것이 쉬워졌지만, 그 내용을 잘 전달하는 발표 과정을 객관적으로 확인하고 무엇을 개선할지 판별하는 것은 아직 어렵습니다.

그래서 반복 연습을 설계하고 실제 발표 행동을 측정하며, 그 결과로 사용자의 발표 특성을 파악해 코칭하고 다음 연습에도 반영되도록 하는 피치코치 서비스를 기획하게 되었습니다.

## 기술 스택

| 구분 | 기술 |
|---|---|
| FE | ![React](https://img.shields.io/badge/React_18-61DAFB?style=flat-square&logo=react&logoColor=black) ![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white) ![Vite](https://img.shields.io/badge/Vite-646CFF?style=flat-square&logo=vite&logoColor=white) ![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white) ![React Router](https://img.shields.io/badge/React_Router-CA4245?style=flat-square&logo=reactrouter&logoColor=white) ![TanStack Query](https://img.shields.io/badge/TanStack_Query-FF4154?style=flat-square&logo=reactquery&logoColor=white) ![Zustand](https://img.shields.io/badge/Zustand-443E38?style=flat-square&logoColor=white) ![MediaPipe](https://img.shields.io/badge/MediaPipe-0097A7?style=flat-square&logo=google&logoColor=white) ![MSW](https://img.shields.io/badge/MSW-FF6A33?style=flat-square&logo=mockserviceworker&logoColor=white) ![Vitest](https://img.shields.io/badge/Vitest-6E9F18?style=flat-square&logo=vitest&logoColor=white) ![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=flat-square&logo=playwright&logoColor=white) |
| BE | ![Python](https://img.shields.io/badge/Python_3.14-3776AB?style=flat-square&logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white) ![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?style=flat-square&logo=sqlalchemy&logoColor=white) ![Alembic](https://img.shields.io/badge/Alembic-6BA81E?style=flat-square&logoColor=white) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL_17-4169E1?style=flat-square&logo=postgresql&logoColor=white) ![Deepgram](https://img.shields.io/badge/Deepgram_STT-13EF93?style=flat-square&logoColor=black) ![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white) ![uv](https://img.shields.io/badge/uv-DE5FE9?style=flat-square&logo=uv&logoColor=white) |
| AI | ![Python](https://img.shields.io/badge/Python_3.12-3776AB?style=flat-square&logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white) ![LangChain](https://img.shields.io/badge/LangChain-1C3C3C?style=flat-square&logo=langchain&logoColor=white) ![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=flat-square&logo=openai&logoColor=white) ![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?style=flat-square&logo=scikitlearn&logoColor=white) ![Kiwi](https://img.shields.io/badge/Kiwi_(kiwipiepy)-4CAF50?style=flat-square&logoColor=white) ![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white) ![uv](https://img.shields.io/badge/uv-DE5FE9?style=flat-square&logo=uv&logoColor=white) |
| Infra | ![Docker](https://img.shields.io/badge/Docker_Compose-2496ED?style=flat-square&logo=docker&logoColor=white) ![Caddy](https://img.shields.io/badge/Caddy-1F88C0?style=flat-square&logo=caddy&logoColor=white) ![Amazon EC2](https://img.shields.io/badge/Amazon_EC2-FF9900?style=flat-square&logo=amazonec2&logoColor=white) ![Amazon ECR](https://img.shields.io/badge/Amazon_ECR-FF9900?style=flat-square&logo=amazonaws&logoColor=white) ![Amazon S3](https://img.shields.io/badge/Amazon_S3-569A31?style=flat-square&logo=amazons3&logoColor=white) ![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white) |

## 전체 핵심 기능

### 1. 실제 발표 행동 측정
카메라와 마이크로 받은 Take를 분석해 발표 습관을 수치로 보여 줍니다.
- **목소리**: 발표 동안의 평균 음량과 음량 편차를 재서, 목소리가 작거나 일정하지 않은 구간을 짚어 줍니다.
- **말버릇**: 실시간 STT로 받아 적은 발화에서 "음", "어" 같은 군더더기 말을 찾습니다. 발화 사이 침묵이 얼마나 길고 자주 생기는지도 셉니다.
- **시간 관리**: 정해진 발표 시간 안에 마쳤는지, 목표 시간에 얼마나 가깝게 맞췄는지 확인합니다.

### 2. 대본 의존도 파악
대본을 얼마나 보고 읽는지 두 가지 방향에서 확인합니다.
- **시선 분석**: 브라우저에서 얼굴과 시선을 추적해(MediaPipe), 청중 대신 화면이나 대본을 보는 시간을 잽니다.
- **발화–대본 유사도**: 실제로 한 말과 대본을 비교합니다. 대본을 그대로 읽었는지, 내용을 빠뜨렸는지, 자기 말로 풀어 전달했는지 봅니다.

### 3. Take 기반 다음 연습 미션
- Take가 끝나면 AI가 측정 결과와 사용자가 고른 평가 기준을 바탕으로 결과 보고서를 만듭니다.
- 가장 먼저 고치면 좋을 점을 골라 다음 Take에서 수행할 미션으로 제시합니다.
- 반복 연습하면서 이전 Take보다 나아졌는지 비교할 수 있습니다.

### 4. 발표 맞춤 대화형 에이전트
지금까지의 발표 기록을 아는 상태에서 대화로 코칭합니다.
- **이전 발표 확인**: "지난 Take에서 뭐가 부족했어?"처럼 지난 결과를 물어볼 수 있습니다.
- **조건을 바꾼 재평가**: 발표 시간이나 평가 기준 같은 조건을 바꾸면 결과가 어떻게 달라지는지 다시 평가합니다.
- **연습 화면으로 이동**: 대화 중에 바로 다음 연습을 시작할 수 있도록 연습 화면으로 안내합니다.


## 시스템 아키텍처 구조도
<img width="100%" alt="pitch-coach-fe-be-ai-develop-next" src="https://github.com/user-attachments/assets/1994491c-676e-44a2-9fe0-dc3d5393eb43" />

## AI Agent 구조도
<img width="2608" height="4032" alt="ai-3" src="https://github.com/user-attachments/assets/483ad8f1-4b5e-4c37-b0f1-beba93ce8a5a" />

## 폴더 구조

```
pitch-coach/
├── .github/              # 이슈 · PR 템플릿, CODEOWNERS, 워크플로 (CI · 배포 · 알림)
├── ai/
│   ├── service/          # AI 서버 (FastAPI)
│   ├── research/         # 기능별 연구 · 실험 (코치, 리뷰, 대화, 시선, 대본 파싱 등)
│   ├── tools/            # 개발 보조 도구 (문서 점검 docs-check)
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
│   ├── ai/               # AI 설계 문서 (아키텍처, 기능별 명세)
│   ├── be/               # BE 설계 문서 (Take 생명주기, 리뷰, 실시간 STT · 코치, 배포 등)
│   └── fe/               # FE 문서 · 디자인 시안 (design/)
└── infra/                # docker-compose, Caddy 설정
```

## 로컬 개발 환경 세팅

### 0. 공통 준비

| 도구 | 버전 | 비고 |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | BE: Python 3.14 / AI: Python 3.12 | 각 폴더의 `.python-version`에 적힌 버전을 uv가 받아 옵니다 |
| Node | 24 (`nvm use 24`) | 버전이 다르면 `.npmrc`의 `engine-strict` 때문에 `npm ci`가 멈춥니다 |
| Docker | — | 로컬 DB를 띄울 때 씁니다 |

#### 환경변수 파일 만들기

각 폴더의 `.env.example`을 복사해 만듭니다.

```bash
cp backend/.env.example backend/.env
cp ai/.env.example ai/.env
cp frontend/.env.example frontend/.env.local
```

> [!TIP]
> `backend/.env`에는 JWT 인증을 위한 `JWT_SECRET_KEY`가 있습니다.
> 이는 `openssl rand -hex 32`로 만들면 됩니다.

#### 실행 순서

```
1. DB  →  2. AI (8001)  →  3. BE (8000)  →  4. FE (3000)
```

---

### 1. DB (PostgreSQL)

```bash
cd infra
docker compose up -d db
```

> [!WARNING]
> `db` 서비스는 `backend/.env`의 `POSTGRES_*` 값을 읽습니다. **`backend/.env`를 먼저** 만들어 두세요.

### 2. AI 서버 (포트 8001)

```bash
cd ai/service
uv sync --locked
uv run python -m uvicorn pitch_coach_ai.main:app --reload --port 8001
```

> [!NOTE]
> `/health`는 LLM 키 없이도 뜹니다. 실제 LLM 기능을 부를 때만 `ai/.env`에 키를 채우면 됩니다.

### 3. BE (포트 8000)

```bash
cd backend
uv sync
uv run alembic upgrade head   # DB가 떠 있어야 합니다
uv run fastapi dev src/pitch_coach_backend/main.py
```

`AI_BASE_URL` 기본값이 `http://localhost:8001`이라 2번과 그대로 맞습니다.

> [!NOTE]
> 아래 값이 비어 있어도 서버는 뜨지만, 해당 기능은 동작하지 않습니다.
>
> | 비어 있는 값 | 동작하지 않는 기능 | 해결방책 |
> |---|---|---|
> | Deepgram 키 | 실시간 STT | API Key 발급 후 환경변수에 삽입 |
> | S3 버킷 | 업로드 | S3 버킷 생성 후 이름과 리전을 환경변수에 삽입(Presigned URL) |
> | 구글 OAuth | 로그인 | 리다이렉션 URL 지정, Google OAuth API Client ID와 Secret 발급 후 환경변수에 삽입|

### 4. FE (포트 3000)

```bash
cd frontend
npm ci
npm run doctor   # 실패 0개면 세팅 완료
npm run dev
```

> [!IMPORTANT]
> FE는 기본으로 **MSW 목 서버**를 씁니다. 실제 BE에 붙이려면 `frontend/.env.local`에서 `VITE_USE_MOCK=false`로 바꾸세요.

## 사용 예시
1. 피치코치 사이트(https://pitch-coach-release.duckdns.org) 에 접속을 완료했다면 시작하기 버튼을 눌러 로그인을 진행합니다.
<img width="2880" height="1532" alt="홈화면" src="https://github.com/user-attachments/assets/750600f7-fcaf-4c77-8c89-c712d493dc04" />

<img width="2880" height="1534" alt="피치 코치 로그인" src="https://github.com/user-attachments/assets/ac89a2cd-5e4e-4ae5-9cb4-9eb6ff3856e4" />

---

2. 로그인 후 홈 화면에서 "새 피치 만들기"를 눌러 발표를 생성합니다.
<img width="2880" height="1530" alt="홈화면" src="https://github.com/user-attachments/assets/f71d6648-af81-4b6c-9a2b-0db9d8c8afb3" />

---

3. 피치 생성 화면에서 발표 자료, 대본, 중요하게 생각하는 평가기준을 넣은 후 장치 검증 화면으로 이동합니다.

<img width="2880" height="1530" alt="스크린샷 2026-11-08 135354" src="https://github.com/user-attachments/assets/a8967220-20ae-40f8-b3b2-da5f03836222" />
<img width="2880" height="1532" alt="발표 준비 2" src="https://github.com/user-attachments/assets/261cc3df-a2e3-4b39-b494-93f9ce5cfef7" />
<img width="2878" height="1532" alt="스크린샷 2026-11-08 140129" src="https://github.com/user-attachments/assets/726a9ca0-6f69-42af-8791-c2bf497d76b2" />

---

4. 장치 검증이 끝나면, Take 연습을 진행합니다.
<img width="2880" height="1530" alt="스크린샷 2026-11-08 140316" src="https://github.com/user-attachments/assets/a606d753-4dcf-4ee9-b9dd-17b5ba3e88aa" />

---

5. Take 연습이 끝나고 잠시 기다리면, AI가 평가를 진행해 결과 보고서와 다음 발표 연습에 수행하면 좋을 미션을 제시합니다.
<img width="2880" height="1532" alt="스크린샷 2026-11-08 140640" src="https://github.com/user-attachments/assets/53279190-c104-46de-8aca-26de2afcc149" />
<img width="2880" height="1532" alt="review" src="https://github.com/user-attachments/assets/414a16b2-5da9-4a34-b9fc-c305ae076e26" />

---

> [!TIP]
> 대화형 에이전트를 이용해 이전 발표가 어땠는지, 조건을 다르게 하면 어떤 결과가 나오는지를 확인할 수 있어요.

<img width="2880" height="1530" alt="스크린샷 2026-11-08 143059" src="https://github.com/user-attachments/assets/f2f95b83-b95a-4def-b11c-e918b943eaca" />


## 직군별 README.md

| 구분 | 파일 위치 |
|---|---|
| FE | [frontend/README.md](frontend/README.md) |
| BE | [backend/README.md](backend/README.md) |
| AI | [ai/README.md](ai/README.md) |
| Infra | [infra/README.md](infra/README.md) |

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

<table>
  <tr>
    <td align="center"><img src="https://github.com/JWK0395.png" width="100" alt="김제원" /></td>
    <td align="center"><img src="https://github.com/Kjun03.png" width="100" alt="김준수" /></td>
    <td align="center"><img src="https://github.com/anjh8370.png" width="100" alt="안정환" /></td>
    <td align="center"><img src="https://github.com/scholar-star.png" width="100" alt="정유진" /></td>
    <td align="center"><img src="https://github.com/myeowon.png" width="100" alt="문여원" /></td>
    <td align="center"><img src="https://github.com/jinn-ie.png" width="100" alt="이서진" /></td>
  </tr>
  <tr>
    <td align="center"><a href="https://github.com/JWK0395">@JWK0395</a></td>
    <td align="center"><a href="https://github.com/Kjun03">@Kjun03</a></td>
    <td align="center"><a href="https://github.com/anjh8370">@anjh8370</a></td>
    <td align="center"><a href="https://github.com/scholar-star">@scholar-star</a></td>
    <td align="center"><a href="https://github.com/myeowon">@myeowon</a></td>
    <td align="center"><a href="https://github.com/jinn-ie">@jinn-ie</a></td>
  </tr>
  <tr>
    <td align="center">김제원<br/><sub>PM · AI</sub></td>
    <td align="center">김준수<br/><sub>FE 리드</sub></td>
    <td align="center">안정환<br/><sub>FE</sub></td>
    <td align="center">정유진<br/><sub>BE 리드 · Infra</sub></td>
    <td align="center">문여원<br/><sub>BE</sub></td>
    <td align="center">이서진<br/><sub>AI 리드</sub></td>
  </tr>
</table>

