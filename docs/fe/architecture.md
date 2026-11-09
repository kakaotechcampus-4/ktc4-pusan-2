# FE 구조

피치코치 프론트엔드가 어떤 화면을 어떤 순서로 보이고, 상태를 어디에 두고, 다른 파트(BE · AI)와 무엇을 주고받는지 한 장으로 정리합니다. 기능 하나의 자세한 흐름은 기능 문서(`features/`)에 둡니다.

다른 문서에서 "`architecture.md` 3절"처럼 절 번호로 가리킵니다. 절 번호는 바꾸지 않습니다.

| 절 | 내용 |
|---|---|
| 0 | 한눈에 보기 |
| 1 | 기술 스택 |
| 2 | 화면 흐름 |
| 3 | 상태를 어디에 두나 |
| 4 | 폴더와 의존 방향 |
| 5 | 다른 파트와 주고받는 것 |
| 6 | 시선 파이프라인 |
| 7 | 실행 환경과 배포 |
| 8 | 열린 질문 |

---

## 0. 한눈에 보기

- 사용자는 **피치(발표 하나)** 를 만들고, 슬라이드 · 대본을 올려 매핑한 뒤, **Take(연습 한 번)** 를 반복합니다. 모든 화면의 기준은 점수가 아니라 Take 사이의 변화입니다.
- 카메라 영상은 브라우저 밖으로 나가지 않습니다. 시선은 브라우저 안(워커)에서 1초 단위로 판정하고, 판정 결과만 서버로 보냅니다. 음성은 PCM으로 WebSocket에 보내고 로컬에 남기지 않습니다.
- 서버에서 온 데이터는 TanStack Query, 화면 상태는 Zustand, 리허설 기록은 IndexedDB에 둡니다(3절).
- 코치 판단 · 문구는 서버(AI)에서만 옵니다. FE에는 판정 규칙 · 코칭 문구를 두지 않습니다.

지켜야 할 결정은 [frontend/AGENTS.md](../../frontend/AGENTS.md) "반드시 지켜야 할 결정 사항"에 있습니다. 이 문서는 그 결정을 바꾸지 않고 구조만 설명합니다.

## 1. 기술 스택

Vite 8 · React 18 · TypeScript 6 · Tailwind 4 · React Router 7 · TanStack Query 5 · Zustand 5 · MSW 2(개발 목) · Vitest 4 · oxlint · Prettier 3. 경로 별칭은 `@/` = `frontend/src/`입니다. 정확한 버전은 `frontend/package.json`이 기준입니다.

- `SharedArrayBuffer`를 쓰려면 페이지가 격리(cross-origin isolated)되어야 해서 COOP/COEP 헤더를 겁니다(`vite.config.ts`, dev · preview 둘 다). 헤더 오타는 오류를 내지 않으므로, 개발 서버에서 `crossOriginIsolated`가 `true`인지 확인합니다.
- 디자인 토큰은 `src/index.css`의 `@theme`에만 둡니다. 밝은 지면(홈 · 리포트)과 어두운 지면(리허설)이 나뉩니다.

## 2. 화면 흐름

라우트는 [src/app/routes.tsx](../../frontend/src/app/routes.tsx) 한 곳에 있습니다.

```
/welcome · /login
      │
      ▼
/  홈 ──▶ /pitch/new · /pitch/:id/edit   피치 만들기 · 수정 (슬라이드 · 대본 매핑 · 평가 기준)
              │  매핑을 저장하면 연습 조합(PracticeCombo)을 들고 이동
              ▼
        /pitch/:pitchId/device-check       장치 점검 (카메라 · 마이크 · 시선 기준점) → Take 생성
              │
              ▼
        /takes/:takeId/rehearsal | /exam   리허설 (실시간 코칭 / 실전)
              │  끝내기
              ▼
        /takes/:takeId/processing          분석 대기 ──▶ /takes/:takeId/confirm  대본 확인
              │                                    └──▶ /takes/:takeId/retry    업로드 실패 · 재시도
              ▼
        /takes/:takeId                     Take 리포트 ──▶ 다음 Take: 장치 점검으로
```

| 묶음 | 라우트 |
|---|---|
| 들어오기 | `/welcome` · `/login` · `/unsupported`(미지원 브라우저) |
| 홈 · 목록 | `/` · `/pitches` · `/takes`(Take 기록 · 비교) · `/pitch/:id/comparison`(옛 주소, `/takes?…`로 넘김) · `/me` |
| 피치 | `/pitch/new` · `/pitch/:id/edit` |
| 연습 | `/pitch/:pitchId/device-check` · `/pitch/:pitchId/prepare`(옛 주소, 장치 점검으로 넘김) · `/takes/:takeId/rehearsal` · `/takes/:takeId/exam` |
| 결과 | `/takes/:takeId/processing` · `/takes/:takeId/confirm` · `/takes/:takeId/retry` · `/takes/:takeId` |
| 안내 | `/about` · `/privacy` · `/terms` |
| 개발 검증용 | `/dev/stage` · `/dev/media` · `/dev/stt` (제품 화면 아님) |

- **Take는 장치 점검의 시작 버튼에서만 생깁니다.** 다른 화면의 연습 버튼(리포트의 [Take N 연습하기] 등)은 장치 점검으로 이동만 합니다. 홈에는 장치 점검으로 바로 가는 버튼이 없고, [발표 준비]는 피치 수정으로 갑니다.
- 장치 점검은 연습할 버전을 두 곳에서 압니다. 피치 수정에서 왔으면 라우터 state의 연습 조합, 그 밖(리포트의 [Take N 연습하기] · 분석 실패 화면의 [다시 연습하기] · 대화형 에이전트의 [연습 준비로] · 주소 직접 입력)이면 서버 `GET /pitches/{id}/prepare`가 고른 짝입니다. 서버가 가장 최근 자료 대신 지난 자료를 골랐으면(장 수가 다른 새 PDF를 올린 뒤 등) 장치 점검이 "가장 최근 자료(v2 · 11쪽)는 대본(10장)과 장 수가 달라 지난 자료(v1)로 연습하게 돼요"를 띄우고, 사용자가 확인하기 전에는 시작하지 않습니다(`prepareInfo.ts` `olderSlideNotice`, #1099). 자세한 것은 [features/rehearsal.md](features/rehearsal.md) 2절.
- 대화형 에이전트는 라우트가 아니라 최상위 레이아웃에 떠 있는 팝업이고, 정해진 화면에서만 보입니다(`features/chat/chatRoutes.ts`).

> **작성 필요** — 화면마다 로그인 필요 여부 · 출시 제한(`VITE_LAUNCH_GATE_ENABLED`)에 걸리는 화면 목록. 출시 제한 자체는 [launch-countdown.md](launch-countdown.md)에 있습니다.

## 3. 상태를 어디에 두나

| 무엇 | 도구 | 예 |
|---|---|---|
| 서버에서 온 것 | TanStack Query (`shared/api/*.ts`) | 홈 · 피치 · 리포트 · 장치 점검 준비 정보 |
| 화면이 가진 작업 중 상태 | Zustand | 피치 작성 초안(`features/pitch/create/createStore.ts`), 장치 점검(`features/rehearsal/prepare/prepareStore.ts`), 리허설 무대(`features/rehearsal/stage/rehearsalStore.ts`), 대화(`features/chat/chatStore.ts`) |
| 프레임 단위 고빈도 값 | `useRef` | 시선 원시 좌표 · 마이크 RMS. 상태로 두면 프레임마다 다시 그립니다 |
| 리허설 기록 | IndexedDB (`features/rehearsal/lib/db.ts`) | 세션 · 1초 시선 기록 · 슬라이드 전환. 서버로 가는 원본은 발표 중 WS `metrics`이고, 이것은 새로고침 이어 가기 · 종료 재시도를 위한 이 브라우저의 사본입니다 |
| 화면 사이에 넘기는 값 | 라우터 state | 피치 수정 → 장치 점검의 연습 조합(`practice`) |
| 이 브라우저에만 남기는 작은 값 | localStorage | 대화 기억 첫 안내를 봤는지 등 |

- 시선 기준점(Calibration)은 탭 메모리에만 둡니다. 서버로 보내지 않습니다.
- 라우터 state는 새로고침 · 앞으로 가기에도 남습니다. 장치 점검은 넘겨받은 조합보다 서버 최신 조합이 새 것이면 알리고 [최신으로]를 둡니다.

## 4. 폴더와 의존 방향

```
src/
  app/          라우팅 · 최상위 레이아웃
  features/     auth · chat · info · onboarding · pitch · rehearsal · report
  shared/       api/ 쿼리 훅 · lib/ 순수 함수 · ui/ 공용 컴포넌트
  workers/      시선 워커. DOM · React 없음
  vendor/gaze/  AI 시선 엔진 복사본(engine · worker, 고치지 않음) + FE 원본 카메라 화면(camera)
  mocks/        MSW 목
  types/        API 타입. 런타임 코드 없음
```

세 폴더는 책임이 아니라 **실행 환경**으로 갈립니다.

| 폴더 | 어디서 도나 | 쓸 수 있는 것 |
|---|---|---|
| `workers/` | 워커 스레드 | DOM · React **없음** |
| `features/rehearsal/` | 메인 스레드 | React · DOM · 워커 호출 |
| `shared/lib/` | 양쪽 | 순수 함수만 |

### 4-1. 의존 방향 — 화살표를 거스르지 않습니다

```
app/ ──▶ features/* ──▶ workers/ ──┐
                │                  ├──▶ types/       (런타임 코드 없음. 잎사귀)
                └──▶ shared/*  ────┘
```

| 규칙 | 어기면 |
|---|---|
| `workers/` → `features/` **금지** | 워커 번들에 React가 들어갑니다. 빌드는 통과하고 워커만 런타임에 죽습니다 |
| `features/a` → `features/b` **금지** | 기능끼리 서로를 기다리게 됩니다. 여러 기능이 쓰는 것은 `shared/`로 옮깁니다 |
| `types/`는 무엇도 참조하지 않음 | 잎사귀라서 순환이 생기지 않습니다 |

### 4-2. `shared/`에 둘지 판단하는 기준

순수 함수라서가 아니라, **여러 기능이 실제로 나눠 쓰는지**로 정합니다. 그래서 시선 전용 규칙(`gazeSegments` · `gazePayload` · `db`)은 `features/rehearsal/lib/`에 있습니다.

## 5. 다른 파트와 주고받는 것

| 무엇 | 방식 | FE 코드 | 기준 문서 |
|---|---|---|---|
| 피치 · 슬라이드 · 대본 · 평가 기준 | REST | `shared/api/pitch.ts` · `presentation.ts` · `script.ts` · `standards.ts` | [docs/be/script-parse.md](../be/script-parse.md) |
| 대본 나누기 결과 | REST 폴링(1초) | `features/pitch/create/scriptParse.ts` | [docs/be/script-parse.md](../be/script-parse.md) |
| 장치 점검 준비 정보 · Take 생성 | REST | `shared/api/prepare.ts` | [docs/fe/pitch-take-contract-570.md](pitch-take-contract-570.md) |
| 리허설 음성(PCM) · 1초 기록(`metrics`: 시선 · 음량 · 슬라이드) · 실시간 전사 · 코치 · 끝내기(`stop`) | WebSocket `/api/ws/takes/{id}` | `features/rehearsal/lib/sttSocket.ts` · `stage/useSttStream.ts` · `stage/useMetricsSender.ts` · `stage/useLiveCoach.ts` | [docs/be/realtime-stt.md](../be/realtime-stt.md) · [docs/be/take-lifecycle.md](../be/take-lifecycle.md) · [docs/ai/architecture.md](../ai/architecture.md) 1-3 · 1-4 · 6-2 |
| Take 종료 시각 · 중단 | REST `PUT /api/pitches/{id}/takes/{takeId}` · `POST .../abandon` | `features/rehearsal/stage/sendEnding.ts` · `shared/api/take.ts` | [docs/be/take-lifecycle.md](../be/take-lifecycle.md) |
| Take 결과 · 리포트 | REST | `shared/api/report.ts` · `takeHistory.ts` | [docs/be/realtime-coach.md](../be/realtime-coach.md) · [docs/be/review.md](../be/review.md) |
| 대화형 에이전트 | REST 폴링 | `features/chat/chatTransport.ts` · `shared/api/chatPaths.ts` | [docs/ai/features/chat-agent.md](../ai/features/chat-agent.md) 12절 |
| 설문 · 서비스 의견 | REST | `shared/api/takeSurvey.ts` · `serviceReview.ts` | [docs/be/take-survey.md](../be/take-survey.md) · [docs/be/service-feedback-plan-540.md](../be/service-feedback-plan-540.md) |
| 로그인 | Google OAuth(BE가 시작 · 콜백 처리) + 토큰 | `features/auth/` · `shared/api/tokenStore.ts` | [frontend/src/features/auth/README.md](../../frontend/src/features/auth/README.md) |

- FE와 BE의 경로 · 필드가 다르면 FE를 BE에 맞추는 것이 기본입니다.
- 개발 중에는 MSW 목(`src/mocks/`)이 같은 경로에 응답합니다. 실서버에 붙일 때는 `.env.local`에 `VITE_USE_MOCK=false`를 둡니다.

> **작성 필요** — 오류 응답 모양과 FE 공통 처리(`shared/api/client.ts` · `errorMessage.ts`), 토큰 갱신 흐름.

## 6. 시선 파이프라인

카메라 프레임이 워커로 가서 시선 엔진이 판정하고, 1초 결과만 메인 스레드로 돌아옵니다. 프레임은 판정 직후 버리고 저장하지 않습니다.

- 엔진: `vendor/gaze/engine` · `worker`(AI 복사본, 여기서 고치지 않음). 판이 바뀌면 AI 쪽에서 고치고 다시 복사합니다 — [vendor/gaze/README.md](../../frontend/src/vendor/gaze/README.md)
- 화면 쪽 진입점: `features/rehearsal/media/useGazeWorker.ts`(프레임 펌프 · 백프레셔), 장치 점검의 기준점 `prepare/useGazeSetup.ts`, 리허설 중 `stage/useLiveGaze.ts`
- 기록: 1초 기록 → WS `metrics`로 서버에(`stage/useMetricsSender.ts`), 같은 기록을 IndexedDB(`lib/db.ts`)에 사본으로. 끝낼 때 기록을 한꺼번에 올리는 단계는 없습니다(BE #134). `lib/gazePayload.ts`는 `/dev/media` 확인용입니다
- 화면은 1초 판정 결과만 압니다. 엔진이 바뀌어도 화면 코드는 바뀌지 않게 둡니다.

지금 파일 기준의 호출 흐름 그림은 [features/rehearsal.md](features/rehearsal.md) 6-1에 있습니다.

## 7. 실행 환경과 배포

- 로컬: `npm run dev`(목 서버, http://localhost:3000). 실서버에 붙이는 법과 명령어는 [frontend/README.md](../../frontend/README.md)에 있습니다.
- 빌드 이미지: `frontend/Dockerfile` — 빌드 단계에서 시선 모델 자산을 받아 넣고, Caddy로 정적 파일을 냅니다. 빌드 때 정해지는 값은 `ARG`로 받습니다(예: `VITE_LAUNCH_GATE_ENABLED`).
- 배포: dev는 Actions → Deploy develop을 `develop-next`로 실행합니다. 환경 값은 배포 워크플로가 씁니다(BE 담당).
- CI: `frontend-ci.yml`이 typecheck · lint · test · format:check · build를 돌립니다. PR에서는 화면 설계서와의 차이(`design-drift`)를 알려 줍니다. 바뀐 것이 있어도 실패하지 않고, 비교할 커밋을 찾지 못할 때만 실패합니다.

## 8. 열린 질문

- **기능 문서가 아직 없는 화면** — 홈 대시보드(`/`), 피치 목록(`/pitches`), 마이페이지(`/me`: 탈퇴 · 서비스 의견), 들어오기(`/welcome` · `/login` · 온보딩), 안내(`/about` · `/privacy` · `/terms`), 미지원 브라우저(`/unsupported`). 지금은 이 문서 2절의 라우트 표에만 있습니다.
- **공통 처리 문서가 없음** — 공용 컴포넌트(`shared/ui/`, [README](../../frontend/src/shared/ui/README.md)), MSW 목 작성 규칙, 오류 · 토큰 갱신(5절 `작성 필요`).

> **작성 필요** — 아직 정하지 않은 항목(`frontend/AGENTS.md`의 I-03 · I-17 · I-18 · I-19).
