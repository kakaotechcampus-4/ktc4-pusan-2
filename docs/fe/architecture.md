# FE 구조

> **기준 범위**: FE 전체 구조(화면 흐름 · 상태 위치 · 폴더와 의존 방향 · 다른 파트와 주고받는 것)의 기준 문서입니다. 기능 하나의 흐름 · 문구 · 실패 처리는 `features/`의 기능 문서에, 지켜야 할 결정은 [frontend/AGENTS.md](../../frontend/AGENTS.md)에 있습니다. BE · AI와 주고받는 경로 · 필드 · 오류 코드는 각 팀 문서가 기준입니다.
>
> **한 줄 요약**: 피치를 만들고 Take를 반복하는 화면들이, 서버 값은 TanStack Query · 작업 중 상태는 Zustand · 리허설 기록 사본은 IndexedDB에 두고, BE와는 REST와 WebSocket으로 주고받습니다.
>
> **기준값**: 라우트는 `src/app/routes.tsx`, API 경로는 `src/shared/api/*.ts`, 의존성 버전은 `frontend/package.json`이 기준입니다. 이 문서의 표는 이해를 돕는 요약이라, 코드와 다르면 코드가 맞고 이 문서를 고칩니다.

다른 문서에서 "[architecture.md](architecture.md) 3절"처럼 링크와 절 번호로 가리킵니다. 절 번호는 바꾸지 않습니다. `docs/ai/architecture.md`(AI 문서)와 이름이 같으니, 그쪽을 가리킬 때는 `docs/ai/`를 붙여 씁니다.

글 속의 `#123`은 GitHub 이슈 · PR 번호이고, "AI 12-4 F9"처럼 쓴 것은 [AI 기능 문서](../ai/features/chat-agent.md)의 절 · 할 일 번호입니다.

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

Vite 8 · React 18 · TypeScript 6 · Tailwind 4 · React Router 7 · TanStack Query 5 · Zustand 5 · MSW 2(개발 목) · Vitest 4 · Playwright(E2E, `e2e/*.e2e.ts`) · oxlint · Prettier 3. 경로 별칭은 `@/` = `frontend/src/`입니다. 정확한 버전은 `frontend/package.json`이 기준입니다.

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
              │  끝내기          └──끝내기 실패──▶ /takes/:takeId/retry  종료 재시도
              ▼
        /takes/:takeId/processing          분석 대기 ──▶ /takes/:takeId/confirm  대본 확인
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

**Take가 생기는 곳** — 장치 점검의 시작 버튼 한 곳뿐입니다.

- 다른 화면의 연습 버튼(리포트의 [Take N 연습하기] 등)은 장치 점검으로 이동만 합니다.
- 홈에는 장치 점검으로 바로 가는 버튼이 없습니다. 홈의 [발표 준비]는 피치 수정으로 갑니다.

**장치 점검이 연습할 버전을 아는 법**

| 어디서 왔나 | 버전 |
|---|---|
| 피치 수정 | 라우터 state로 넘겨받은 연습 조합 |
| 리포트의 [Take N 연습하기] · 분석 실패 화면의 [다시 연습하기] · 대화형 에이전트의 [연습 준비로] · 주소 직접 입력 | 서버 `GET /pitches/{id}/prepare`가 고른 짝 |

- 서버가 가장 최근 자료 대신 지난 자료를 고를 수 있습니다(장 수가 다른 새 PDF를 올린 뒤 등). 그러면 장치 점검이 알리고, 사용자가 확인하기 전에는 시작하지 않습니다(#1099 지난 자료로 시작하기 전 확인). 자세한 것은 [features/rehearsal.md](features/rehearsal.md) 2-3.

**대화형 에이전트** — 별도 페이지가 아니라 앱 최상위의 `chat-layout`에 한 번만 배치합니다(`app/routes.tsx`). `features/chat/chatRoutes.ts`가 허용한 화면에서만 오른쪽 아래 런처를 보입니다. 열면 런처가 숨겨지고 화면 오른쪽에 **400px 채팅 패널**이 나타납니다. 닫기 또는 Esc로 패널을 닫으면 런처에 초점이 돌아옵니다. 뷰포트 너비 **1440px 이상**에서는 공통 헤더와 본문을 담은 `chat-layout-content`의 오른쪽에 패널 공간을 확보하고, **1440px 미만**에서는 본문 너비를 줄이지 않고 패널을 화면 위에 겹쳐 표시합니다(`index.css`). 허용 화면 사이에서는 열린 상태를 유지하며, 장치 점검·리허설·실전 등 비허용 경로에 들어가면 닫습니다. 이후 허용 화면으로 복귀해도 자동으로 다시 열지는 않습니다. 대본 매핑·슬라이드 확인 모달은 패널과 독립적인 뷰포트 기준 크기를 유지합니다([대화형 에이전트 기능 문서](features/chat-agent.md) 2절).

**로그인 · 출시 제한 · 기기 확인** — 화면마다 `routes.tsx`가 아래 세 겹을 바깥부터 차례로 씌웁니다.

| 겹 | 걸리는 화면 | 걸리면 |
|---|---|---|
| 1. 기기 확인(`DesktopGate`) | `practicePaths`: 장치 점검 · 리허설 · 실전 | 휴대폰 · 태블릿이면 `/unsupported`. 로그인보다 먼저 막아, 못 하는 연습 주소로 로그인부터 시키지 않습니다 |
| 2. 출시 제한(`LaunchGate`) | `/login`과 로그인이 필요한 모든 화면 | `VITE_LAUNCH_GATE_ENABLED=true`이고 공개 시각(`shared/api/launch.ts` `LAUNCH_AT`) 전이면 `/welcome` |
| 3. 로그인(`RequireSession`) | 아래 "공개 화면"을 뺀 모든 화면 | 확인하는 동안 "로그인 상태를 확인하고 있습니다.", 서버에 못 붙으면 [다시 시도]. 로그인이 안 돼 있으면 `/`는 `/welcome`, 그 밖은 `/login?next=<원래 주소>`. 로그인했어도 개인정보 처리 동의 전이면 같은 주소에서 화면 위에 동의 창(`PrivacyConsentDialog`)을 띄우고, 동의해야만 닫힙니다. 카메라 · 마이크를 켜는 연습 화면은 동의 전에 뒤에 그리지 않습니다([features/auth/README.md](../../frontend/src/features/auth/README.md)) |

- **공개 화면**(`publicPaths`, 로그인 없이 열림): `/welcome` · `/login` · `/about` · `/privacy` · `/terms` · `/unsupported` · `/dev/*` · 없는 주소(404). 이 중 출시 제한은 `/login`에만 걸립니다.
- **상단바**(`appHeaderPaths`): `/` · `/pitches` · `/takes` · `/me` · `/pitch/new` · `/pitch/:id/edit` · 장치 점검 · 재시도에만 홈과 같은 상단바를 얹습니다. 리허설 · 분석 대기 · 대본 확인은 집중 화면이라 각자의 틀을 씁니다.
- 로그인 화면이 Google에서 오류(`?auth_error=`)를 받아 오면 `/login`으로 돌려 보여 줍니다.
- 출시 제한 자체(카운트다운 · 공개 시각)는 [launch-countdown.md](launch-countdown.md)에 있습니다.

## 3. 상태를 어디에 두나

| 무엇 | 도구 | 예 |
|---|---|---|
| 서버에서 온 것 | TanStack Query (`shared/api/*.ts`) | 홈 · 피치 · 리포트 · 장치 점검 준비 정보 |
| 화면이 가진 작업 중 상태 | Zustand | 스토어 4개(아래 목록) |
| 프레임 단위 고빈도 값 | `useRef` | 프레임 펌프 상태 · 마이크 RMS. 상태로 두면 프레임마다 다시 그립니다 |
| 리허설 기록 | IndexedDB (`features/rehearsal/lib/db.ts`) | 세션 · 1초 시선 기록 · 슬라이드 전환. **사본**입니다(아래) |
| 화면 사이에 넘기는 값 | 라우터 state | 피치 수정 → 장치 점검의 연습 조합(`practice`) |
| 이 탭에만 남기는 값 | sessionStorage | 리허설 이어 가기 사본(`rehearsal/stage/resume.ts`) · 대화 복원 값(`chat/chatStore.ts`) |
| 이 브라우저에만 남기는 작은 값 | localStorage | 대화 기억 첫 안내를 봤는지 · 개인정보 처리 동의 기록(사용자별, `shared/lib/privacyConsent.ts`) 등 |
| 탭끼리 주고받는 신호 | BroadcastChannel | 로그아웃 신호(`shared/lib/sessionSignal.ts`). 다른 탭의 메모리 · sessionStorage 사본도 비우게 합니다 |

**Zustand 스토어 4개**

| 스토어 | 담는 것 |
|---|---|
| `features/pitch/create/createStore.ts` | 피치 작성 초안 |
| `features/rehearsal/prepare/prepareStore.ts` | 장치 점검의 선택 |
| `features/rehearsal/stage/rehearsalStore.ts` | 리허설 무대 |
| `features/chat/chatStore.ts` | 대화 |

**알아 둘 점**

- **IndexedDB는 사본입니다.** 서버로 가는 원본은 발표 중 WS `metrics`이고, IndexedDB는 새로고침 이어 가기 · 종료 재시도를 위해 이 브라우저에 남기는 것입니다.
- **시선 기준점**(Calibration) 자체는 탭 메모리에만 둡니다(`zoneRefMemory.ts`). 브라우저 저장소에는 두지 않고, 서버에는 품질 요약만 보냅니다(`POST /api/pitches/{id}/takes/{takeId}/calibration`).
- **라우터 state는 새로고침 · 앞으로 가기에도 남습니다.** 그래서 장치 점검은 넘겨받은 조합보다 서버 최신 조합이 새 것이면 알리고 [최신으로]를 둡니다.

## 4. 폴더와 의존 방향

```
src/
  app/          라우팅 · 최상위 레이아웃
  features/     auth · chat · info · onboarding · pitch · rehearsal · report
  shared/       api/ 쿼리 훅 · lib/ 순수 함수 · ui/ 공용 컴포넌트
  workers/      시선 워커. DOM · React 없음
  vendor/gaze/  AI 시선 엔진 복사본(engine · worker, 고치지 않음) + FE 원본 카메라 화면(camera)
  dev/          개발 검증 화면(/dev/*) 일부. 제품 화면 아님
  mocks/        MSW 목. 개발 서버에서만 켜짐
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
app/ ──▶ features/* ──▶ workers/ ──┬──▶ vendor/gaze/engine
                │                  ├──▶ types/       (런타임 코드 없음. 잎사귀)
                └──▶ shared/*  ────┘
```

`mocks/`는 개발 서버에서만 불러옵니다(`main.tsx`의 `import.meta.env.DEV`). `dev/`는 개발 검증 화면용이라, 지금은 `/dev/stage` 데모(`features/rehearsal/stage/StageDemo.tsx`)만 이 폴더를 씁니다.

| 규칙 | 어기면 |
|---|---|
| `workers/` → `features/` **금지** | 워커 번들에 React가 들어갑니다. 빌드는 통과하고 워커만 런타임에 죽습니다 |
| `features/a` → `features/b` **금지** | 기능끼리 서로를 기다리게 됩니다. 여러 기능이 쓰는 것은 `shared/`로 옮깁니다 |
| `types/`는 무엇도 참조하지 않음 | 잎사귀라서 순환이 생기지 않습니다 |

### 4-2. `shared/`에 둘지 판단하는 기준

순수 함수라서가 아니라, **여러 기능이 실제로 나눠 쓰는지**로 정합니다. 그래서 시선 전용 규칙(`gazeSegments` · `gazePayload` · `db`)은 `features/rehearsal/lib/`에 있습니다.

## 5. 다른 파트와 주고받는 것

> 아래 FE 코드 경로는 지금 `develop-next` 기준입니다. 파일 위치 정리 PR #1151이 합쳐지면 `shared/api/`의 일부(`take.ts` · `takeHistory.ts` · `standards.ts` · `serviceReview.ts` · `chatPaths.ts`)가 각 기능 폴더의 `api/`로 옮겨지니, 그때 이 표를 함께 고칩니다.

| 무엇 | 방식 | FE 코드 | 기준 문서 |
|---|---|---|---|
| 피치 · 슬라이드 · 대본 · 평가 기준 | REST | `shared/api/pitch.ts` · `presentation.ts` · `script.ts` · `standards.ts` | [docs/be/script-parse.md](../be/script-parse.md) |
| 대본 나누기 결과 | REST 폴링(1초) | `features/pitch/create/scriptParse.ts` | [docs/be/script-parse.md](../be/script-parse.md) |
| 장치 점검 준비 정보 · Take 생성 | REST | `shared/api/prepare.ts` | [docs/ai/architecture.md](../ai/architecture.md) 1-2 · BE `module/pitch/dto.py`(`PrepareDTO`) · `module/take/dto.py`(`TakeInitRequestDTO`) |
| 리허설 실시간 연결(음성 · 1초 기록 · 전사 · 코치 · 끝내기) | WebSocket `/api/ws/takes/{id}` | `features/rehearsal/lib/sttSocket.ts` 외 — [rehearsal.md](features/rehearsal.md) 5절 | [docs/be/realtime-stt.md](../be/realtime-stt.md) · [docs/be/take-lifecycle.md](../be/take-lifecycle.md) · [docs/ai/architecture.md](../ai/architecture.md) 1-3 · 1-4 · 6-2 |
| Take 종료 시각 · 중단 | REST `PUT /api/pitches/{id}/takes/{takeId}` · `POST .../abandon` | `features/rehearsal/stage/sendEnding.ts` · `shared/api/take.ts` | [docs/be/take-lifecycle.md](../be/take-lifecycle.md) |
| Take 결과 · 리포트 | REST | `shared/api/report.ts` · `takeHistory.ts` | [docs/be/realtime-coach.md](../be/realtime-coach.md) · [docs/be/review.md](../be/review.md) |
| 대화형 에이전트 | REST 폴링 | `features/chat/chatTransport.ts` · `shared/api/chatPaths.ts` | [docs/ai/features/chat-agent.md](../ai/features/chat-agent.md) 12절 |
| 설문 · 서비스 의견 | REST | `shared/api/takeSurvey.ts` · `serviceReview.ts` | [docs/be/take-survey.md](../be/take-survey.md) · [docs/be/service-feedback-plan-540.md](../be/service-feedback-plan-540.md) |
| 로그인 | Google OAuth(BE가 시작 · 콜백 처리) + 토큰 | `features/auth/` · `shared/api/tokenStore.ts` | [frontend/src/features/auth/README.md](../../frontend/src/features/auth/README.md) |

- FE와 BE의 경로 · 필드가 다르면 FE를 BE에 맞추는 것이 기본입니다.
- 개발 중에는 MSW 목(`src/mocks/`)이 같은 경로에 응답합니다. 실서버에 붙일 때는 `.env.local`에 `VITE_USE_MOCK=false`를 둡니다.

### 5-1. API 호출 · 오류 · 토큰

**호출** — 모든 REST 호출은 `shared/api/client.ts`의 `apiRequest`를 거칩니다.

- 경로는 `/api/`로 시작해야 합니다. 다른 주소로 토큰이 새지 않게 하려는 것입니다. 로그인 쿠키를 쓰는 `/api/auth/*`(갱신 · 로그아웃)만 `tokenStore.ts`의 `cookieRequest`로 따로 부릅니다.
- 액세스 토큰은 **메모리에만** 두고(`tokenStore.ts`) `Authorization: Bearer`로 보냅니다. 브라우저 저장소에 두지 않습니다.

**오류** — BE는 모든 오류를 `{code, message, detail}`로 보내고, FE는 **`code`로 갈라집니다**(`backend/README.md`의 오류 응답 형식).

- `apiRequest`는 실패를 `ApiFailure(status, code, retryAfterSec, message)`로 던집니다. 본문에 `code`가 없으면 `REQUEST_FAILED`입니다.
- 화면 문구는 `shared/api/errorMessage.ts`의 `toMessage`가 `code`로 고릅니다. 표에 없는 코드는 "요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요."입니다. 화면마다 문구를 따로 지으면 같은 상황에서 다른 말이 나오기 때문입니다.
- 서버 `message`는 화면에 그대로 보이지 않습니다. 같은 코드 안에서 사유를 가려야 할 때만 정해진 문구와 견줍니다.
- `Retry-After` 헤더가 있으면 초로 바꿔 `retryAfterSec`에 담습니다. 얼마나 기다릴지는 부른 쪽이 정합니다.

**토큰 갱신**

```
처음 열 때    useSession: 메모리에 토큰이 없으면 갱신 → GET /api/users/me
요청이 401   → 갱신(POST /api/auth/refresh, 쿠키 + X-CSRF-Token) → 새 토큰으로 한 번 다시 보냄
갱신도 실패  → 메모리 토큰을 비움 → 세션이 없어짐 → 로그인 화면(2절 "로그인")
```

- 동시에 여러 요청이 401을 받아도 갱신은 한 번만 합니다(진행 중인 갱신을 함께 기다림). 쿠키는 탭끼리 나눠 쓰므로, 탭 사이에서도 `navigator.locks`로 갱신을 한 줄로 세웁니다.
- 다른 탭에서 다른 계정으로 로그인해 사용자가 바뀌었으면 다시 보내지 않고 `SESSION_CHANGED`로 멈춥니다. 앞 사용자가 쓴 글이 새 계정으로 접수되지 않게 하려는 것입니다.
- 로그아웃은 다른 탭에도 알립니다(3절 BroadcastChannel).

## 6. 시선 파이프라인

카메라 프레임이 워커로 가서 시선 엔진이 판정하고, 1초 결과만 메인 스레드로 돌아옵니다. 프레임은 판정 직후 버리고 저장하지 않습니다.

- 엔진: `vendor/gaze/engine` · `worker`(AI 복사본, 여기서 고치지 않음). 판이 바뀌면 AI 쪽에서 고치고 다시 복사합니다 — [vendor/gaze/README.md](../../frontend/src/vendor/gaze/README.md)
- 화면 쪽 진입점: `features/rehearsal/media/useGazeWorker.ts`(프레임 펌프 · 백프레셔), 장치 점검의 기준점 `prepare/useGazeSetup.ts`, 리허설 중 `stage/useLiveGaze.ts`
- 기록: 1초 기록 → WS `metrics`로 서버에(`stage/useMetricsSender.ts`), 같은 기록을 IndexedDB(`lib/db.ts`)에 사본으로. 끝낼 때 기록을 한꺼번에 올리는 단계는 없습니다(BE #134에서 끝낼 때 몰아 보내던 단계를 없앰). `lib/gazePayload.ts`는 `/dev/media` 확인용입니다
- 화면은 1초 판정 결과만 압니다. 엔진이 바뀌어도 화면 코드는 바뀌지 않게 둡니다.

지금 파일 기준의 호출 흐름 그림은 [features/rehearsal.md](features/rehearsal.md) 6-1에 있습니다.

## 7. 실행 환경과 배포

- 로컬: `npm run dev`(목 서버, http://localhost:3000). 실서버에 붙이는 법과 명령어는 [frontend/README.md](../../frontend/README.md)에 있습니다.
- 빌드 이미지: `frontend/Dockerfile` — 빌드 단계에서 시선 모델 자산을 받아 넣고, Caddy로 정적 파일을 냅니다. 빌드 때 정해지는 값은 `ARG`로 받습니다(예: `VITE_LAUNCH_GATE_ENABLED`).
- 배포: dev는 Actions → Deploy develop을 `develop-next`로 실행합니다. 환경 값은 배포 워크플로가 씁니다(BE 담당).
- CI: `frontend-ci.yml`이 typecheck · lint · test · format:check · build를 돌리고, dev · preview 서버에 COOP/COEP 헤더가 붙는지 확인합니다. E2E(`npm run test:e2e`)는 따로 나눈 `frontend-e2e` job이 PR마다 돌립니다. 아직 필수 검사는 아니라 실패해도 머지가 막히지 않으니 결과를 직접 확인합니다. PR에서는 화면 설계서와의 차이(`design-drift`)를 알려 줍니다. 바뀐 것이 있어도 실패하지 않고, 비교할 커밋을 찾지 못할 때만 실패합니다.

## 8. 열린 질문

- **기능 문서가 아직 없는 화면** — 홈 대시보드(`/`), 피치 목록(`/pitches`), 마이페이지(`/me`: 탈퇴 · 서비스 의견), 들어오기(`/welcome` · `/login` · 온보딩), 안내(`/about` · `/privacy` · `/terms`), 미지원 브라우저(`/unsupported`). 지금은 이 문서 2절의 라우트 표에만 있습니다.
- **공통 처리 문서가 없음** — 공용 컴포넌트(`shared/ui/`, [README](../../frontend/src/shared/ui/README.md)), MSW 목 작성 규칙. 오류 · 토큰 갱신은 5-1절에 있습니다.

- **아직 정하지 않은 값** — [frontend/AGENTS.md](../../frontend/AGENTS.md) "아직 안 정해진 것" 표에 있습니다(I-03 시선 모델 · I-17 UNCERTAIN 임계값 · I-18 대본 유사도 · I-19 축소 판정).
