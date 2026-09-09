# 피치코치 FE

## 시작하기

```bash
nvm use 24                 # .nvmrc → Node 24. 다르면 npm ci가 멈춥니다 (.npmrc engine-strict)
npm ci                     # install 아님 — lock을 갱신하면 두 사람 트리가 갈립니다
cp .env.example .env.local # PowerShell: copy .env.example .env.local
npm run doctor             # 세팅이 끝났는지 한 번에 확인
npm run dev                # http://localhost:5173
```

`npm run doctor`가 실패 0개로 나와야 세팅이 끝난 것입니다. `.env.local`을 안 만들면
그것만 FAIL로 잡아줍니다. **경고 1개(에디터 TS)는 정상** — 아래 "알아둘 것" 참고.

> nvm-windows는 `.nvmrc`를 항상 읽지 않아서 버전을 직접 적습니다.

MSW 워커 파일은 **커밋되어 있고** `package.json`의 `msw.workerDirectory` 덕에
`npm ci`가 알아서 갱신합니다. `npx msw init`을 따로 돌리지 않습니다.

`npm run build` 통과, `npm run test` 통과 상태로 넘겨드립니다.

> **모노레포 루트가 아니라 `frontend/` 에서 실행하세요.** 위 명령은 전부 이 폴더 기준입니다.

## 명령어

| | |
| --- | --- |
| `npm run dev` | 개발 서버 (MSW 목 자동 시작) |
| `npm run dev:lan` | **HTTPS + LAN 공개.** 다른 기기에서 카메라를 테스트할 때 (`--mode lan`, Windows에서도 동작) |
| `npm run build` | 타입 체크 + 프로덕션 빌드 |
| `npm run preview` | 빌드 결과를 띄워 봅니다. dev와 같은 COOP/COEP가 걸려 있습니다 |
| `npm run doctor` | 세팅 점검 — Node·TS·의존성·`.env.local`·줄바꿈·COOP/COEP·확장 |

**PR 통과 조건은 이 넷입니다.** 레포 루트의 `.github/workflows/frontend-ci.yml`이 같은 걸 돌립니다.

| | |
| --- | --- |
| `npm run typecheck` | 타입만 확인 |
| `npm run lint` | oxlint (`--max-warnings=0` — 경고도 실패입니다) |
| `npm run test` | 순수 로직 테스트 |
| `npm run format:check` | Prettier 확인. 고칠 때는 `npm run format` |

실서버로 붙일 때 (W8): `.env.local`에 `VITE_USE_MOCK=false`

## 지금 들어 있는 것

**설정** — Vite 8 · React 18 · TS 6 · Tailwind 4 · React Router 7 · TanStack Query 5 · Zustand 5 · MSW 2 · Vitest 4 · oxlint · `@/` 경로 별칭 · COOP/COEP 헤더

**디자인 토큰** (`src/index.css` `@theme`) — 시안 v5의 팔레트와 시선 3색, 그리고 **계측 조건인 고정 높이 네 값**이 있습니다. 밝은 지면(홈·리포트)과 어두운 지면(리허설)이 나뉘어 있습니다. **색을 새로 만들지 말고 여기 있는 것만 쓰세요.**

**라우팅 21개** (`src/app/routes.tsx`) — 전부 Stub이지만 이동은 됩니다. P 번호는 화면 목록 문서와 같습니다.

**API 타입** (`src/types/api.ts`) — 명세 8-4를 옮긴 것. 시선 3값, 시간 필드 셋, `null` 규칙이 들어 있습니다.

**MSW 목** (`src/mocks/handlers.ts`) — 홈·리포트·분석 상태·complete. **측정 제외 케이스(`t2`)를 일부러 넣어뒀습니다** — 이 화면을 나중에 만들면 W9에 몰립니다.

**빈 자리** (`src/features/auth` · `pitch` · `report`) — `.gitkeep`만 있습니다. Track B가 여기 채웁니다.

**아직 없는 것** — 시선 파이프라인(워커 · `shared/lib` · `features/rehearsal`)은
**다음 PR로 따로 들어옵니다.** 그 밖에 웹소켓 · 공용 컴포넌트 · 실제 화면이 없습니다.

**테스트가 0개입니다.** 순수 로직 테스트 셋이 전부 `shared/lib` 과 `workers` 에 있어
파이프라인과 함께 들어옵니다. 그동안 `npm run test` 는 `--passWithNoTests` 로 통과합니다 —
파이프라인 PR 에서 이 플래그를 지우세요.

## 다음에 할 일 (W4 남은 분량)

**둘이 함께**
- [ ] 공용 컴포넌트 — 버튼 · 모달 · 토스트 · 빈 상태 · 스켈레톤 · 에러 바운더리
- [ ] `401` 무음 갱신 인터셉터
- [ ] Pretendard 폰트 셀프 호스팅 (`public/fonts/`)

**Track A** — 별도 PR (`chore/fe-dev-environment` 다음)
- [ ] 시선 파이프라인 — 워커 · 백프레셔 · 프레임 예산 계기판
- [ ] 녹음(`MediaRecorder` → IndexedDB 5초 조각) · 음량(`AnalyserNode` RMS)
- [ ] IndexedDB 스키마 · 하트비트
- [ ] 실모델 껍데기 + 실패 경로(`ENGINE_UNAVAILABLE` → 측정 제외)

> **MediaPipe(`@mediapipe/tasks-vision`)를 붙이지 않습니다.** AI팀이 전처리까지 합니다.
> 의존성에는 남아 있지만(제거하면 lock 이 갱신됩니다) 코드에서 쓰지 않습니다.

**Track B**
- [ ] 구글 로그인 리다이렉트 · 토큰 저장/갱신
- [ ] 보호 라우트 · 온보딩 분기

## 저장소 — 팀 모노레포의 `frontend/`

여기는 팀 레포 `ktc4-pusan-2` 의 `frontend/` 입니다.

CI 는 레포 루트의 `.github/workflows/frontend-ci.yml` 에 있습니다 — GitHub 는 루트의
`.github/` 만 읽어서, `frontend/` 안에 두면 **에러 없이 그냥 안 돕니다.**

> **이 브랜치의 기준점.** 백엔드·인프라가 들어오기 전 시점(`a493804`)에서 떴습니다.
> 그래서 `backend/` · `infra/` · `frontend/Dockerfile` 이 이 트리에 없습니다.
> `develop` 에 머지되면 함께 놓입니다 — `Dockerfile` 과 `develop` push 자동배포는 그쪽에 있습니다.
> 도커로 확인해야 하면 `develop` 을 머지해 오세요.

### 아직 남아 있는 것 셋

| | 무엇 | 왜 |
| --- | --- | --- |
| 1 | 브랜치 보호 → Require status checks → **`frontend`** | 안 켜면 CI 가 돌기만 하고 실패한 PR 도 머지됩니다 |
| 2 | `Dockerfile` 을 `node:24-alpine` 으로, `COPY` 에 `.npmrc` 추가 | ↓ 아래 주의 |
| 3 | VS Code 로 **`frontend/` 폴더를 연다** | ↓ 아래 주의 |

### ★ Dockerfile 과 engines 는 같은 PR 로

```
engines >=24   +   COPY .npmrc   +   node:20-alpine   →   npm ci 실패
```

`engines` 는 이미 `>=24` 이고 `.npmrc` 의 `engine-strict=true` 는 이걸 강제합니다.
**이 PR 은 `Dockerfile` 을 건드리지 않습니다** (이 브랜치에 없습니다). `develop` 의 것은
`.npmrc` 를 COPY 하지 않아 검사가 안 돌아서 조용히 빌드됩니다. `COPY .npmrc` 를 넣는
순간 `node:20-alpine` 에서 `npm ci` 가 실패하니, **둘을 같은 PR 에서 함께 고치세요.**

지금 `node:20-alpine` 에서 조용히 빌드되는 이유는 `.npmrc` 를 COPY 하지 않아
`engines` 검사가 아예 돌지 않기 때문입니다. 즉 **배포 이미지가 개발 환경과
다른 Node 메이저에서 돌고 있습니다.**

`.dockerignore` 는 `develop` 의 `frontend/` 에 있습니다 (이 PR 은 건드리지 않습니다).
**빌드 컨텍스트를 `frontend/` 로 주세요** —
레포 루트를 컨텍스트로 주면(`docker build -f frontend/Dockerfile .`)
루트의 `.dockerignore` 가 쓰이고 이 파일은 무시됩니다. 그러면 `.env.local` 이
이미지에 들어갈 수 있습니다.

### ★ VS Code 는 `frontend/` 를 열어야 합니다

모노레포 루트를 열면 `frontend/.vscode/settings.json` 이 **무시됩니다.**
포맷터·워크스페이스 TypeScript·Tailwind 자동완성·파일 접기가 전부 죽고,
`.ts` 파일에 한 사람 화면만 빨간 줄이 뜨는 상태로 돌아갑니다 (SETUP §1-2 와 같은 함정).

백엔드도 같이 보고 싶으면 멀티루트 워크스페이스를 쓰세요 —
레포 루트에 `ktc4.code-workspace` 를 만들고 `folders` 에 `frontend` 와 `backend` 를
각각 넣으면, 각 폴더의 `.vscode/settings.json` 이 그대로 적용됩니다.

### 브랜치

**팀 컨벤션을 따릅니다** — 루트 `README.md` 의 `타입/영역-작업명`. FE 는 영역이 `fe` 입니다.
`develop` 에서 따고 `develop` 으로 PR 을 보냅니다. `main` 은 멘토 리뷰용입니다.

```
feat/fe-gaze-pipeline     Track A · 시선 · 음성    src/features/rehearsal/ · workers/
feat/fe-login-page        Track B · 진입 · 화면    src/app/ · features/auth · pitch · report
refactor/fe-*             멘토 리뷰 반영
```

기능 단위 1~3일, 각자 끝나는 대로 PR. 끝에 한 번 합치지 않습니다.
두 트랙이 폴더가 갈려 있어 대체로 안 부딪히지만
`types/api.ts` 와 `app/routes.tsx` 는 둘 다 건드립니다 — 여기만 조심하면 됩니다.

## 읽어야 할 것

`CLAUDE.md`에 **바꾸면 안 되는 결정 8가지**가 정리돼 있습니다.
카메라 온디바이스 · 시선 2분할(3값) · 1초 판정 주기 · **서버는 덤이고 브라우저가 원본** ·
시간 필드 관계식 · `null` 규칙 · 스타일 경계 · Take 생성 시점.
이걸 모르고 짜면 나중에 다시 만듭니다.

## 알아둘 것

**React 18입니다.** FE 문서의 결정을 따랐습니다. React Router는 v8이 React 19를 요구해서
v7로 맞췄습니다 — import 경로는 v8과 같아서 코드는 그대로입니다.

**`public/models/`가 비어 있습니다.** MediaPipe WASM과 모델 가중치를 여기 넣습니다.
CDN에서 받지 마세요 — 시연장 네트워크가 느리면 발표가 안 됩니다.

**개발 브라우저는 Chrome으로 고정합니다.** 워커·WASM 프로파일링과 카메라 상태 확인
(`chrome://media-internals`)이 Chrome에서만 제대로 됩니다. 배포 대상은 Chrome/Edge 최신 2개.

**다른 기기에서 열 때는 `npm run dev:lan`.** `--host`만 쓰면 `http://192.168.x.x`가 되고
**카메라가 아예 안 잡힙니다** — `getUserMedia`는 secure context를 요구하는데
`localhost`만 예외이기 때문입니다. 자체 서명 인증서 경고는 "고급 → 계속"으로 넘기면 됩니다.

**`.vscode/`가 레포에 커밋돼 있습니다.** 두 사람의 포맷터·린트 설정이 자동으로 같아집니다.
권장 확장은 VS Code가 알아서 안내합니다. 루트의 설정 파일들은 `package.json`·
`tsconfig.json` 아래로 **접혀서** 보입니다 (file nesting) — 파일은 원래 자리에 있습니다.

**에디터 TypeScript는 한 번 직접 허용해야 합니다.** VS Code 내장은 5.9,
이 프로젝트는 6.0입니다. `.ts` 파일을 열고
`Ctrl+Shift+P` → `TypeScript: Select TypeScript Version` → **Use Workspace Version**
→ `Developer: Reload Window`. 상태 바가 `6.0.x`가 되면 됩니다.

`npm run doctor`가 이걸 경고로 알려주지만 **승인 여부는 명령으로 확인할 수 없습니다**
(VS Code 내부 상태). 보안상 사람이 눌러야 적용됩니다. 안 눌러도 지금은 빨간 줄이
안 뜨지만, TS 6 전용 문법을 쓰는 순간 한 사람 화면에만 에러가 뜨고 빌드는 통과합니다.
