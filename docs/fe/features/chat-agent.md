# 대화형 에이전트 (FE)

대화형 코치 에이전트의 **화면 쪽** 문서입니다. 무엇을 할 수 있는 에이전트인지, AI · BE가 무엇을 하는지는 [docs/ai/features/chat-agent.md](../../ai/features/chat-agent.md)(이하 "AI 문서")가 기준이고, 이 문서는 FE가 그것을 어떻게 화면에 붙였는지만 씁니다. AI 문서의 절은 "AI 12-4 F3"처럼 가리킵니다.

| 절 | 내용 |
|---|---|
| 0 | 한눈에 보기 |
| 1 | 왜 필요한가 · 용어 |
| 2 | 화면 흐름 |
| 3 | 상태를 어디에 두나 |
| 4 | 폴더 · 주요 파일 |
| 5 | BE 계약 |
| 6 | 처리 흐름 |
| 7 | 로딩 · 빈 · 오류 · 부분 실패 |
| 8 | 시간 · 성능 제약 |
| 9 | 테스트 |
| 10 | 열린 질문 · 알려진 한계 |

---

## 0. 한눈에 보기

- 화면 오른쪽 아래의 코치 버튼을 누르면 팝업 대화창이 열립니다. 화면을 옮겨도 대화가 이어집니다.
- FE는 **질문을 보내고, 실행 상태를 1초마다 조회해 답을 그리는 일**만 합니다. 판단 · 문구 · 도구 실행은 BE · AI가 합니다.
- 버튼을 띄울지는 BE가 정합니다(`GET /api/chat/availability`). FE에는 빌드 스위치가 없습니다(#1073).
- 받은 응답은 그리기 전에 모양을 검사하고(`chatResponse.ts`), 검사를 통과하지 못한 부분은 그리지 않습니다.

## 1. 왜 필요한가 · 용어

사용자가 리포트 여러 장을 직접 비교하지 않아도, "계속 막히는 데 찾아 줘" 같은 질문으로 자기 연습 기록을 근거와 함께 볼 수 있게 합니다. 목적 · 범위는 AI 문서 0 · 1절을 봅니다.

| 용어 | 뜻 |
|---|---|
| 대화(conversation) | 질문 · 답이 쌓이는 묶음. 범위(전체 / 피치 하나)를 가집니다 |
| 실행(run) | 질문 하나에 대한 처리 한 번. `QUEUED` → `RUNNING` → (`WAITING_APPROVAL`) → 종결(`DONE` · `PARTIAL` · `REFUSED` · `FAILED` · `CANCELLED` · `EXPIRED`) |
| 범위(scope) | 에이전트가 볼 기록. 전체, 또는 피치 하나 |
| 화면 상태(screen) | 피치 만들기 · 수정 · 장치 점검 화면에서 입력 중인 값. 질문과 함께 보냅니다 |
| 카드 | 답 아래의 버튼 묶음. 목표 저장(되돌리기), 연습 준비, 승인 · 거절 확인 등 |
| 근거 칩 | 답 문장마다 붙는 출처 표시. 내 기록 · 화면 값 · 자료 |

## 2. 화면 흐름

- **보이는 화면**: 허용 목록(`chatRoutes.ts`)에 있는 화면만입니다. 홈 · 목록 · 피치 만들기 · 수정 · 장치 점검 · Take 리포트 등이고, 발표 중(리허설) 화면과 로그인 전 화면은 뺍니다. 새 화면을 더할 때는 목록에 직접 넣어야 합니다.
- **대화창 안의 세 화면**: 메시지(기본) · 대화 목록 · 기억 설정. 머리줄 버튼으로 바꿉니다. 기억 설정에서는 입력칸을 숨기고, 쓰던 글은 남깁니다.
- **첫 화면**: 대화가 비어 있으면 첫 인사를 보입니다.
- **닫기**: 닫기 버튼 · Esc. 닫아도 대화 상태는 스토어에 남아 다시 열면 이어집니다. 로그아웃하면 지웁니다.

> **작성 필요** — 화면 그림(버튼 · 대화창 · 카드 위치). `docs/fe/design/`의 화면 설계서에 시안이 생기면 링크합니다.

## 3. 상태를 어디에 두나

| 무엇 | 위치 | 이유 |
|---|---|---|
| 열림 · 대화 ID · 진행 중 실행 · 보내는 중인 말 · 받은 메시지 | Zustand `chatStore.ts` | 화면을 옮겨도 대화가 이어져야 해서 라우트 밖에 둡니다 |
| 사용 가능 여부 | TanStack Query (`chatAvailability.ts`) | 서버 값. 복귀 · 새 세션 때 다시 묻습니다 |
| 화면 상태(입력 중인 값) | `shared/lib/chatScreen.ts` 에 화면이 등록 | 피치 · 장치 점검 화면이 기능 경계를 넘지 않고 값을 내놓게 합니다(FE 구조 문서 4-1) |
| 기억 첫 안내를 봤는지 | localStorage (사용자별) | BE 응답에 칸이 없어서 브라우저에 둡니다(10절) |

## 4. 폴더 · 주요 파일

모두 `frontend/src/features/chat/` 아래입니다.

| 묶음 | 파일 | 하는 일 |
|---|---|---|
| 틀 | `ChatAgentHost.tsx` | 버튼 · 팝업 · 메시지 목록. 라우터 최상위 레이아웃(`app/routes.tsx`)에 하나만 둡니다 |
| | `ChatErrorBoundary.tsx` | 대화창이 그리다 오류를 내면 대화만 닫고 안내를 보입니다. 다른 화면은 그대로 둡니다 |
| | `chatRoutes.ts` | 보이는 화면 허용 목록, 버튼 높이 |
| 세션 · 통신 | `useChatSession.ts` | 대화창 한 세션(로그인 사용자 하나)의 연결 |
| | `chatTransport.ts` | 보내기 · 실행 조회 · 취소 · 승인/거절 · 되돌리기. 조회 간격 · 재시도 규칙(8절) |
| | `chatApi.ts` · `shared/api/chatPaths.ts` | 서버 호출과 경로. 목(`mocks/chat.ts`)도 같은 경로 파일을 봅니다 |
| | `chatAvailability.ts` | 사용 가능 여부 조회 · 재조회 |
| 응답 | `chatResponse.ts` | 응답 모양 검사, 근거 칩으로 바꾸기 |
| | `chatBasis.ts` | 근거 표시 · 최신 아님 |
| | `chatTyping.ts` | 글자 효과(받은 문장을 큐로) |
| 그리기 | `ChatAnswer.tsx` · `ChatChips.tsx` · `ChatCards.tsx` · `chatCardRows.ts` | 답 · 근거 칩 · 카드 |
| | `ChatPanelParts.tsx` · `chatConversations.ts` | 범위 칩 · 대화 목록 |
| | `ChatMemorySettings.tsx` · `ChatMemoryParts.tsx` · `chatMemory.ts` · `useChatMemory.ts` | 기억 설정 · 첫 안내 |
| 안내 · 잠금 | `chatNotices.ts` · `chatLock.ts` · `useCanSend.ts` · `LockedReason.tsx` | 안내 문구 고르기, 지금 보낼 수 있는지, 잠긴 이유 |

## 5. BE 계약

경로는 `shared/api/chatPaths.ts` 한 곳에 있고, BE `module/chat/controller.py`에 맞춥니다. 모두 `/api/chat` 아래입니다.

| 경로 | 쓰는 곳 |
|---|---|
| `GET /availability` | 버튼을 띄울지. `{enabled: false}` 또는 `AGENT_DISABLED`면 숨깁니다 |
| `POST /messages` | 질문 보내기(새 대화 포함). 응답으로 실행 ID를 받습니다 |
| `GET /conversations` · `PATCH /conversations/{id}/scope` | 대화 목록(커서로 이어 읽기) · 범위 바꾸기 |
| `GET /conversations/{id}/messages` · `GET /conversations/{id}/run` | 메시지 목록 · 그 대화의 진행 중 실행(복원) |
| `GET /runs/{id}` · `POST /runs/{id}/cancel` | 실행 상태 조회 · 취소 |
| `POST /runs/{id}/actions/{actionId}/decision` · `.../undo` | 승인 · 거절, 되돌리기 |
| `GET · PUT /memory` · `DELETE /memory/items` · `DELETE /memory/items/{id}` | 기억 설정 · 항목 지우기 |

- 요청 · 응답의 필드와 오류 코드는 AI 문서 12-2 · 12-3이 기준입니다. FE 타입은 `src/types/chat.ts`입니다.
- BE에 아직 없는 것은 코드에 `(BE 미구현)`으로 표시하고 목만 흉내 냅니다. BE가 생기면 이 표시를 찾아 고칩니다. 지금 남은 것은 10절에 적습니다.

## 6. 처리 흐름

```
[보내기]  POST /messages ──▶ 실행 ID
              │  접수를 15초 안에 확인하지 못하면 "다시 확인"(같은 키로 다시 보냄)
              ▼
[조회]    GET /runs/{id}  — RUNNING 이면 1초, 그 밖은 2초마다
              │  답 문장(claim)이 오면 글자 효과 큐에 넣어 그립니다
              │  WAITING_APPROVAL 이면 확인 카드 → [승인] · [거절]
              ▼
[종결]    메시지 목록을 다시 읽어 저장된 답으로 바꿉니다
              │  상태별 안내를 붙입니다(7절)
              ▼
[복원]    새로고침 · 화면 이동 뒤에는 대화 ID로 진행 중 실행을 다시 찾고, 있으면 조회를 이어 갑니다
```

- 보내는 동안 · 실행 중에는 입력칸과 답 아래 버튼을 잠그고, 잠긴 이유를 화면 낭독기가 읽습니다.
- 범위를 다시 골라야 하면(피치가 지워짐 등) 범위를 고를 때까지 보내지 않습니다.

## 7. 로딩 · 빈 · 오류 · 부분 실패

| 상황 | 화면 |
|---|---|
| 사용 가능 여부 조회 중 | 버튼을 숨깁니다. 5초 안에 답이 없으면 오류로 두고 2 · 4 · 8초 뒤 다시 묻습니다 |
| 꺼짐(`enabled: false` · `AGENT_DISABLED`) | 버튼을 숨깁니다 |
| 이전 대화 불러오는 중 · 실패 | "이전 대화를 불러오고 있어요." · "대화를 불러오지 못했으니 다시 시도해 주세요." + [다시 시도] |
| 빈 대화 | 첫 인사 |
| 중간까지만 답함(`PARTIAL`) | "여기까지 확인했어요. 남은 일: …" + [이어서 확인] |
| 거절(`REFUSED`) | AI의 고정 문장이 답입니다. FE는 안내를 덧붙이지 않습니다(AI 12-4 F8은 "할 수 있는 질문 예시"를 정함 — 10절) |
| 실패 · 만료 · 취소 | 상태별 안내(`chatNotices.ts`, AI 7-9 · 7-10 문구). 다시 보낼 수 있으면 [다시 보내기] |
| 오래 걸림 | 130초가 지나면 "늦어지고 있어요" 안내와 [상태 확인], 조회는 30초 간격으로 늦춥니다 |
| 대화창이 그리다 오류 | 대화만 닫고 안내 + [다시 시도] (`ChatErrorBoundary`) |

문구는 AI 문서 7-9 · 7-10 · 12-4 F8 표를 그대로 씁니다. 문서에 없어 FE가 정한 문구는 코드에 그렇다고 적어 둡니다.

## 8. 시간 · 성능 제약

| 값 | 크기 | 위치 |
|---|---|---|
| 사용 가능 여부 제한 · 재조회 | 5초 · 2/4/8초 | `chatAvailability.ts` |
| 접수 확인 대기 | 15초 | `chatTransport.ts` `SEND_UNCONFIRMED_MS` |
| 요청 하나의 제한 | 20초 | `REQUEST_TIMEOUT_MS` |
| 조회 간격 | 실행 중 1초 · 그 밖 2초 | `POLL_RUNNING_MS` · `POLL_IDLE_MS` |
| 조회 실패 뒤 간격 | 2 → 4 → 8 → 16 → 30초 | `FAILURE_STEPS_MS` |
| 늦어짐 판단 · 그 뒤 간격 | 130초 · 30초 | `SLOW_AFTER_MS` · `SLOW_POLL_MS` |
| 글자 효과 | 50ms마다, 최대 2초 뒤처짐 | `chatTyping.ts` |

- 글자 효과로 채워지는 요소에는 `aria-live`를 두지 않습니다(글자마다 읽힘). 완성된 문장만 따로 둔 `role="status"` 영역에 넣습니다.

## 9. 테스트

| 무엇 | 파일 |
|---|---|
| 화면 동작(보내기 · 조회 · 카드 · 안내 · 복원) | `ChatAgentHost.test.tsx` |
| 통신 규칙(간격 · 재시도 · 취소 · 승인) | `chatTransport.test.ts` |
| 응답 검사 · 근거 칩 | `chatResponse.test.ts` 등 |
| 목이 BE 계약과 같은지 | `mocks/chat.contract.test.ts` |

- 목 서버(`npm run dev`)에서는 `mocks/chat.ts` 맨 위 주석의 문장으로 상황을 고릅니다(예: "날씨" → 거절).
- 실제 BE · AI로는 dev 서버에서 확인합니다. dev BE · AI의 `AGENT_ENABLED`가 켜져 있어야 버튼이 보입니다.

> **작성 필요** — E2E(Playwright) 도입 뒤 시나리오 목록(AI 12-4 F9).

## 10. 열린 질문 · 알려진 한계

- **기억 첫 안내를 봤는지** — AI 문서(12-3)는 BE가 사용자별로 저장한다고 하지만 BE 응답에 칸이 없습니다. 지금은 브라우저에 두어 다른 기기에서는 한 번 더 보입니다. `chatMemory.ts`의 `(BE 미구현)` 표시.
- **거절 답 아래 질문 예시(AI 12-4 F8)** — 아직 없습니다. 첫 화면 · 거절 답 아래에 할 수 있는 일과 질문 예시를 붙이는 작업이 PR #1093에 있습니다. 머지되면 7절을 고칩니다.
- **긴 대화에서 대화창 전체가 스크롤되는 문제** — 메시지 영역 안의 화면 낭독기용 글자가 영역 밖으로 빠지는 문제입니다. `fix/fe-chat-panel-overflow`에서 고쳤고 머지 전입니다.
- **E2E** — Playwright 도입 전이라 화면 시험은 jsdom 단위 시험뿐입니다.
