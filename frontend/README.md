# 피치코치 FE

## 코드 컨벤션

[프론트엔드 코드 컨벤션](./FRONTEND_CONVENTION.md)을 따릅니다.

## 시작하기

```bash
nvm use 24                 # .nvmrc → Node 24. 다르면 npm ci가 멈춥니다 (.npmrc engine-strict)
npm ci                     # install 아님 — lock을 갱신하면 두 사람 트리가 갈립니다
cp .env.example .env.local # PowerShell: copy .env.example .env.local
npm run doctor             # 세팅이 끝났는지 한 번에 확인
npm run dev                # http://localhost:3000
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

**PR 전에 돌릴 것은 이 넷입니다.** 레포 루트의 `.github/workflows/frontend-ci.yml`이 이 넷과 build, COOP/COEP 헤더 확인을 돌립니다([architecture.md](../docs/fe/architecture.md) 7절).

| | |
| --- | --- |
| `npm run typecheck` | 타입만 확인 |
| `npm run lint` | oxlint (`--max-warnings=0` — 경고도 실패입니다) |
| `npm run test` | 순수 로직 테스트 |
| `npm run format:check` | Prettier 확인. 고칠 때는 `npm run format` |

`npm run test:e2e`(Playwright, 목 서버를 3100 포트에 띄워 대화 화면을 끝까지 돌림)는 CI에서 돌지 않습니다. 대화 화면 · `src/mocks/` · 대화 통신 계층을 고쳤으면 직접 돌립니다.

실서버로 붙일 때 (W8): `.env.local`에 `VITE_USE_MOCK=false`

## 구조와 기능별 설계

[docs/fe/](../docs/fe/README.md)에 있습니다. 전체 구조(폴더와 의존 방향 · 상태 위치 · 다른 파트와 주고받는 것 · 시선 파이프라인)는 [architecture.md](../docs/fe/architecture.md), 기능별 흐름은 [features/](../docs/fe/features/)입니다.

## 저장소 — 팀 모노레포의 `frontend/`

여기는 팀 레포 `ktc4-pusan-2` 의 `frontend/` 입니다.

CI 는 레포 루트의 `.github/workflows/frontend-ci.yml` 에 있습니다 — GitHub 는 루트의
`.github/` 만 읽어서, `frontend/` 안에 두면 **에러 없이 그냥 안 돕니다.**

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
지금 `frontend/Dockerfile` 은
`.npmrc` 를 COPY 하지 않아 검사가 안 돌아서 조용히 빌드됩니다. `COPY .npmrc` 를 넣는
순간 `node:20-alpine` 에서 `npm ci` 가 실패하니, **둘을 같은 PR 에서 함께 고치세요.**

지금 `node:20-alpine` 에서 조용히 빌드되는 이유는 `.npmrc` 를 COPY 하지 않아
`engines` 검사가 아예 돌지 않기 때문입니다. 즉 **배포 이미지가 개발 환경과
다른 Node 메이저에서 돌고 있습니다.**

`.dockerignore` 는 `frontend/` 에 있습니다.
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
`feat/*` · `fix/*` 는 `develop-next` 에서 따고 `develop-next` 로 PR 을 보냅니다.
`develop` 은 멘토 리뷰용이라 리뷰 반영(`refactor/*` → `develop`)만 보냅니다.
`develop-next/...` 처럼 기본 브랜치 이름 아래로 브랜치를 만들 수는 없습니다(Git 이 거부).

```
feat/fe-gaze-pipeline     Track A · 시선 · 음성    src/features/rehearsal/ · workers/
feat/fe-login-page        Track B · 진입 · 화면    src/app/ · features/auth · pitch · report
refactor/fe-*             멘토 리뷰 반영
```

기능 단위 1~3일, 각자 끝나는 대로 PR. 끝에 한 번 합치지 않습니다.
두 트랙이 폴더가 갈려 있어 대체로 안 부딪히지만
`types/api.ts` 와 `app/routes.tsx` 는 둘 다 건드립니다 — 여기만 조심하면 됩니다.

## 읽어야 할 것

`AGENTS.md`에 **바꾸면 안 되는 결정 9가지**가 정리돼 있습니다.
카메라 온디바이스 · 시선 2분할(3값) · 1초 판정 주기 · 끊겨도 발표는 계속 ·
시간 필드 관계식 · `null` 규칙 · 스타일 경계 · Take 생성 시점 · `void` 금지.
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


## 발표 준비의 사용자 확인

PDF는 업로드 시 이미 서버에 저장된다. 업로드 직후 PDF 확인 창을 열고, `확인 완료`를 눌러야 이번 작성에서 해당 버전의 확인이 끝난다. 닫기·Esc·자료 버전 열람은 확인으로 처리하지 않는다. PDF를 불러오지 못하면 확인 버튼을 막고 다시 열기를 제공한다.

PDF·평가기준 확인 상태는 FE 작성 스토어의 메모리에만 둔다. 현재 화면에서 새로 업로드·정리한 버전은 확인이 필요하고, 수정 화면에서 서버로부터 불러온 기존 버전은 확인 완료로 본다. **확인 전에 새로고침한 뒤 서버 버전을 다시 불러오면 확인 완료가 되는 한계가 있다.** 확인만을 위한 BE 필드나 저장 API는 추가하지 않는다.

대본 매핑은 기존처럼 확인 창의 `저장`이 필요하다. 평가기준 정리와 수정 저장은 AI가 반환한 목록을 다시 확인해야 한다. 미정리 원문·미저장 수정·미확인 결과가 있으면 해당 항목으로 안내한다. 필수 항목을 마치면 `최종 확인` 창에서 실제 자료 조합·제목·시간·평가기준을 보고 `확인하고 시작 전 점검`으로 이동한다. 이 화면에서 Take를 생성하지 않는다.
