# 장치 점검 · 리허설 (FE)

Take(연습 한 번)를 만들고, 발표하는 동안 음성 · 시선 · 슬라이드 기록을 서버로 보내고, 끝내는 화면입니다. 카메라 · 마이크 · 시선 엔진 · 실시간 연결이 모두 여기에 모입니다.

| 절 | 내용 |
|---|---|
| 0 | 한눈에 보기 |
| 1 | 왜 필요한가 · 용어 |
| 2 | 화면 흐름 |
| 3 | 상태를 어디에 두나 |
| 4 | 폴더 · 주요 파일 |
| 5 | BE · AI 계약 |
| 6 | 처리 흐름 |
| 7 | 로딩 · 빈 · 오류 · 부분 실패 |
| 8 | 시간 · 성능 제약 |
| 9 | 테스트 |
| 10 | 열린 질문 · 알려진 한계 |

---

## 0. 한눈에 보기

- **장치 점검**(`/pitch/:pitchId/device-check`)에서 카메라 · 마이크 · 시선 기준점을 확인하고, 시작 버튼으로 Take를 만듭니다. Take는 여기서만 생깁니다.
- **리허설**(`/takes/:takeId/rehearsal` 코칭 · `/exam` 실전)에서는 WebSocket 하나로 음성(PCM)과 1초 기록(`metrics`: 시선 · 음량 · 슬라이드 번호)을 보내고, 실시간 전사 · 코치 한마디를 받습니다.
- 끝내기는 WS `stop`과 종료 시각 기록(`PUT /takes/{id}`)입니다. 기록을 한꺼번에 올리는 단계는 없습니다.
- 카메라 영상 · 시선 기준점은 브라우저 밖으로 나가지 않습니다. 음성은 로컬에 남기지 않습니다. 브라우저의 IndexedDB 기록은 새로고침 · 재시도를 위한 사본입니다.

지켜야 할 결정(시선 3값 · 1초 주기 · 시간 필드 규칙 · 연결이 끊겨도 발표는 계속 등)은 [frontend/AGENTS.md](../../../frontend/AGENTS.md) 1~9번이 기준입니다.

## 1. 왜 필요한가 · 용어

| 용어 | 뜻 |
|---|---|
| Take | 연습 한 번. 슬라이드 버전 · 대본 버전 · 연습 모드 · 대본 표시 방식을 보내 만들고(`client_session_id`가 멱등 키라 다시 보내도 하나만 생김), 제한 시간은 BE가 피치의 지금 설정으로 기록합니다 |
| 연습 조합 | 피치 수정에서 넘겨받은 버전 묶음(`PracticeCombo`). 없으면 서버 `GET /prepare`가 고른 짝 |
| 대본 표시 방식 | 발표 중 대본을 어떻게 띄울지. 끄면(`OFF`) 실전 모드, 그 밖은 코칭 모드. 시작하면 못 바꿉니다 |
| 시선 기준점(Calibration) | 이 기기 · 이 배치에서 "카메라를 본다"의 기준. 탭 메모리에만 둡니다 |
| 1초 기록 | 1초마다 만드는 시선 · 음량 기록. WS `metrics`로 보냅니다 |
| 시선 3값 | `CAMERA` · `BOTTOM` · `UNCERTAIN`. 화면은 이 셋만 압니다 |
| 평소 목소리 | 발표 전에 잰 기준 음량(`base_level_db`). 선택 단계라 시작을 막지 않습니다 |

## 2. 화면 흐름

```
/pitch/:pitchId/device-check   장치 점검
   │  장비 준비(카메라 · 마이크) → 시선 기준점 → (평소 목소리, 선택) → 대본 표시 방식
   │  조합 없이 왔는데 서버가 가장 최근 자료 대신 지난 자료를 골랐으면, 확인받은 뒤에 시작 (#1099)
   │  다른 창 · 기기에서 발표 중인 Take 가 있으면 끝낼지 묻습니다
   │  [Take N 시작하기] → POST /takes → 시선 보정 요약 POST /takes/{id}/calibration
   │      (보정 요약 · 평소 목소리 같은 참고값이라, 보내지 못해도 연습은 엽니다)
   ▼
/takes/:takeId/rehearsal | /exam   리허설
   │  마이크 + 첫 ready 뒤에 시계 시작
   │  슬라이드 넘기기 · 대본 · 시선 테두리 · 코치 한마디
   │  [끝내기] (확인 한 번 더)
   ▼
 WS stop + PUT /takes/{id} (종료 시각)
   ├─ 성공 → /takes/:takeId/processing  분석 대기
   └─ 실패 → /takes/:takeId/retry       재시도 (IndexedDB 의 같은 세션으로 다시 보냄)
```

- **새로고침은 이어 가고, 창을 닫으면 이어 가지 않습니다.** 같은 탭인지는 장치 점검이 넘긴 라우터 state(`clientSessionId`)로 알고, 발표 시계는 sessionStorage에서 꺼내 발표 중이던 시각부터 다시 잽니다(`stage/resume.ts`). sessionStorage를 쓰는 이유는 `pagehide` 때 IndexedDB 쓰기가 끝나기 전에 탭이 사라질 수 있어서입니다. 이어 가는 Take는 `ready`를 기다리지 않습니다(서버에서 이미 시작함).
- 리허설 주소를 다른 창에서 다시 열면 무대를 열지 않고 abandon으로 실패(`CLOSED`) 처리한 뒤 안내 화면을 띄웁니다. 다른 창이 아직 발표 중이면(하트비트 10초 안 · 서버 409 `TAKE_IN_PROGRESS`) 아무것도 바꾸지 않습니다. 전체 조건은 [docs/be/take-lifecycle.md](../../be/take-lifecycle.md).
- 재시도 화면 · 실패한 리허설에서 장치 점검으로 돌아갈 때는 처음 고른 연습 조합을 다시 넘깁니다(`prepare/deviceCheckLink.ts`).
- **지난 자료 확인** — 리포트의 [Take N 연습하기] · 분석 실패 화면의 [다시 연습하기] · 대화의 [연습 준비로]는 피치 수정을 거치지 않고 조합 없이 들어옵니다. 이때 `GET /prepare`는 대본과 장 수가 맞는 가장 최근 자료를 고르므로, 장 수가 다른 새 PDF를 올린 뒤면 지난 자료가 골라집니다. 장치 점검은 최신 자료를 따로 받아(`shared/api/prepare.ts` `useLatestSlide`) 견주고, 지난 자료면 안내를 띄운 뒤 사용자가 고를 때까지 시작하지 않습니다. 막아 버리지는 않습니다 — 일부러 지난 자료로 연습할 수 있게 둡니다. 조합을 넘겨받았어도 그 뒤 다른 탭에서 새 PDF를 올렸을 수 있어 같은 비교를 합니다.

## 3. 상태를 어디에 두나

| 무엇 | 위치 | 비고 |
|---|---|---|
| 장치 점검 선택(대본 표시 방식 등) | Zustand `prepare/prepareStore.ts` | |
| 무대 단계(`RUNNING` → `ENDING` → `ENDED`) · 슬라이드 · 코치 문구 | Zustand `stage/rehearsalStore.ts` | 단계는 한 방향으로만 갑니다 |
| 시선 원시 좌표 · 마이크 RMS · 시계 숫자 | `useRef` · DOM 직접 갱신 | 프레임마다 React를 다시 그리지 않습니다. 시선 테두리는 `dataset.gaze`로 바꿉니다 |
| 세션 · 1초 시선 기록 · 슬라이드 전환 · 시선 성능 | IndexedDB `lib/db.ts` | 키는 `clientSessionId`. 새로고침 이어 가기 · 재시도용 사본 |
| 시선 기준점 | 탭 메모리 `lib/zoneRefMemory.ts` | 서버 · 저장소에 두지 않습니다. 배치가 같은지는 `layoutSignature`로 봅니다 |
| 준비 정보 · Take 생성 | TanStack Query `shared/api/prepare.ts` | |

## 4. 폴더 · 주요 파일

모두 `frontend/src/features/rehearsal/` 아래입니다(시선 워커는 `src/workers/`, 엔진은 `src/vendor/gaze/`).

| 묶음 | 파일 | 하는 일 |
|---|---|---|
| 장치 점검 | `prepare/DeviceCheckPage.tsx` | 점검 셋 · 시작 · 연습 조합 결정 |
| | `prepare/useGazeSetup.ts` · `GazeSetupView.tsx` | 시선 기준점. 상자 안은 AI 카메라 화면 모듈(`vendor/gaze/camera`)이 그립니다 |
| | `prepare/useBaseVoice.ts` · `media/baseVoice.ts` | 평소 목소리 재기 |
| | `prepare/prepareInfo.ts` | 준비 정보 변환 · 시작할 수 없는 이유 · 연습 조합 고르기 |
| | `prepare/activeTake.ts` · `ReplaceTakeDialog.tsx` | 다른 곳에서 발표 중인 Take 확인 |
| 리허설 | `stage/RehearsalPage.tsx` | 무대 · 이어 가기 · 끝내기 |
| | `stage/openDevices.ts` · `startGate.ts` · `useStageClock.ts` | 장치 여는 순서 · 시계를 돌려도 되나 · 머리줄 시계 |
| | `stage/useSlideDeck.ts` · `useSlideSender.ts` · `lib/slideTimeline.ts` | 슬라이드 넘기기 · WS `slide` · 그 시각의 슬라이드 |
| | `stage/useLiveGaze.ts` · `useMetricsSender.ts` · `lib/metricsClock.ts` | 시선 1초 기록 받기 · WS `metrics` 보내기 · 어느 1초로 보낼지 |
| | `stage/useSttStream.ts` · `lib/sttSocket.ts` · `lib/sttProtocol.ts` | 실시간 연결(React를 모르는 상태기계) |
| | `stage/useLiveCoach.ts` · `lib/coachIndicators.ts` · `CoachStrip.tsx` | 코치 한마디 · 상태 표시 |
| | `stage/sendEnding.ts` · `canEnd.ts` · `useEndConfirm.ts` · `resume.ts` | 종료 시각 기록 · 끝내기 가능 · 확인 · 새로고침 이어 가기 |
| 미디어 | `media/useCameraStream.ts` · `useVideoStream.ts` | 권한 · 스트림 · 실제로 프레임이 흐르는지 |
| | `media/pcmCapture.ts` | 마이크 → 16kHz Int16 100ms 프레임. 음성이 서버로 가는 유일한 경로 |
| | `media/level.ts` · `loudness.ts` · `aWeighting.ts` · `voiceRecord.ts` · `useMicLevel.ts` | 음량 · 무음 경고 · 1초 음량 기록 |
| | `media/useGazeWorker.ts` | 프레임 펌프 · 백프레셔(성능의 핵심) |
| 기록 | `lib/db.ts` · `writeFailures.ts` | IndexedDB · 쓰기 실패 장부 |
| | `lib/gazeExclusion.ts` · `gazeNote.ts` | 시선을 Take 전체에서 뺄지 · 기록되지 않을 때 안내 |
| 재시도 | `retry/RetryPage.tsx` | 종료를 못 보냈을 때 다시 보내기 |
| 개발 검증 | `media/MediaDevPage.tsx` · `SttDevPage.tsx` · `stage/StageDemo.tsx` | `/dev/media` · `/dev/stt` · `/dev/stage` (제품 화면 아님) |

## 5. BE · AI 계약

| 무엇 | 방식 · 경로 | 기준 문서 |
|---|---|---|
| 준비 정보 | `GET /api/pitches/{id}/prepare` | [docs/fe/pitch-take-contract-570.md](../pitch-take-contract-570.md) |
| Take 생성 · 시선 보정 요약 | `POST /api/pitches/{id}/takes/` · `POST .../takes/{takeId}/calibration` | 같음 |
| 다른 곳에서 발표 중인 Take | `GET /api/takes/active` · `POST .../abandon?replace=true` | BE #718 · #719 |
| 실시간 연결 | WebSocket `/api/ws/takes/{id}` — 보냄: PCM · `metrics` · `slide` · `stop` / 받음: `ready` · 전사 · 코치 | [docs/be/realtime-stt.md](../../be/realtime-stt.md) · [docs/be/take-lifecycle.md](../../be/take-lifecycle.md) · [docs/ai/architecture.md](../../ai/architecture.md) 1-3 · 1-4 |
| 1초 기록 모양 | WS `metrics` (시선 · 음량 · 슬라이드 번호) | [docs/ai/architecture.md](../../ai/architecture.md) 6-2 · [gaze-tracking.md](../../ai/features/gaze-tracking.md) · [volume.md](../../ai/features/volume.md) 5-2 |
| 종료 시각 · 중단 | `PUT /api/pitches/{id}/takes/{takeId}` · `POST .../abandon` | [docs/be/take-lifecycle.md](../../be/take-lifecycle.md) |
| 코치 | WS로 받음. AI 코치 에이전트가 판단하고 FE는 띄우기만 | [docs/be/realtime-coach.md](../../be/realtime-coach.md) · [coach-agent.md](../../ai/features/coach-agent.md) |

- 예전 계약의 `POST /takes/{id}/complete`(기록을 한꺼번에 올림)는 BE가 만들지 않기로 했습니다(#134). `lib/gazePayload.ts`는 `/dev/media` 확인용으로만 남아 있습니다.

## 6. 처리 흐름

### 6-1. 시선 (장치 점검 → 리허설)

```
media/useCameraStream.ts        카메라 권한 · MediaStream
        │
        ▼
media/useGazeWorker.ts          프레임 펌프 — 앞 프레임 처리가 끝나야 다음 프레임을 보냄(백프레셔)
        │  postMessage(frame)
        ▼
workers/gaze.worker.ts          ── 여기부터 워커 스레드 ──
        │  workers/modelClassifier.ts → vendor/gaze/engine (AI 엔진 v1.1 복사본)
        │  프레임은 판정 직후 버림
        │  1초 기록(GazeSampleRecord)만 메인으로
        ▼
stage/useLiveGaze.ts            1초 기록 받기
        ├─▶ lib/db.ts appendGazeSamples        IndexedDB (사본)
        ├─▶ 시선 테두리 · 발밑 안내             RehearsalPage 가 dataset.gaze 를 바꿈 · lib/gazeNote.ts
        └─▶ stage/useMetricsSender.ts           음량 1초 기록 · 슬라이드 번호를 붙여
                    │
                    ▼
            lib/sttSocket.ts sendMetrics        WS metrics (끊기면 쌓아 두었다가 다시 붙을 때 보냄)
```

- 장치 점검의 기준점은 `prepare/useGazeSetup.ts`가 `vendor/gaze/camera`의 카메라 화면으로 잡고, 탭 메모리(`lib/zoneRefMemory.ts`)에 둡니다. 서버에는 요약만 보냅니다(`lib/calibrationRequest.ts`).
- 슬라이드 번호는 그 1초가 **시작한 시각**의 번호입니다. 1초 기록이 조금 늦게 나가서, 보내는 순간의 번호를 붙이면 넘길 때마다 직전 몇 초가 다음 슬라이드로 적힙니다.

### 6-2. 음성

`media/pcmCapture.ts`(AudioWorklet → 16kHz Int16 100ms 프레임) → `lib/sttSocket.ts` → WS. 로컬에 저장하지 않습니다. 멈춘 AudioContext는 사용자의 첫 입력에서 다시 켭니다(`media/resumeOnGesture.ts`).

### 6-3. 시작과 끝

- **시작 조건은 마이크와 서버 연결입니다.** 마이크가 열리고 WebSocket이 첫 `ready`를 받은 뒤 시계를 돌립니다(`stage/startGate.ts`, #381). 그 연결이 서버에서 Take를 시작하므로(`READY` → `RUNNING`), 그 전에 시작하면 기록이 남지 않습니다. 시작하지 않은 Take는 서버가 만든 지 10분 뒤 끝내고, 그 Take의 끝내기는 409 `TAKE_NOT_STARTED`로 거절됩니다.
- **카메라는 리허설의 시작 조건이 아닙니다.** 리허설 화면에서 카메라를 못 열면 마이크만으로 시작하고 시선만 뺍니다(#445, `stage/openDevices.ts`). 장치 점검에서는 카메라와 시선 기준점이 시작 조건이라, 두 화면이 다릅니다.
- **사용자당 연습은 하나입니다.** 시작 전에 다른 창 · 기기에서 발표 중인 Take를 묻고(`GET /api/takes/active`), 있으면 끝낼지 확인합니다. 묻지 못하면(404 · 네트워크 · 3초 넘게 늦음) 없는 것으로 보고 시작합니다. 그래도 겹치면 연결이 `TOO_MANY_STREAMS`(1008)로 거절되고 `BUSY`로 안내합니다.
- **연결이 끊겨도 발표는 멈추지 않습니다** — 다만 잃는 것이 있습니다.

  | 끊긴 동안 | 무엇 |
  |---|---|
  | 계속 됨 | 시선 테두리(워커 판정) · 시계 · 슬라이드 · 기록 쌓기 |
  | 멈춤 | 코치 한마디 · 상태 표시(지우지 않고 흐리게) · 말하기 분석 |
  | 다시 붙으면 보냄 | 시선 · 음량 1초 기록(`metrics`)과 장 넘김(`slide`) — 5분 치까지 쌓아 둠 |
  | 잃음 | 끊긴 구간의 음성(버퍼는 2초 치). 그 구간의 말 속도 · 군더더기 분석이 빠집니다 |

  다시 붙기: 지수 백오프 3회, 그 뒤로는 횟수로 포기하지 않고 4~6초(jitter)마다. 끊긴 지 5분 30초가 지나면 멈춥니다(서버가 그때 끊긴 Take를 끝냄). 끊김의 기준은 소켓 연결이고, 서버 STT가 `degraded`여도 소켓이 붙어 있으면 시선 · 음량 코칭은 계속됩니다.
- 끝내기: 마지막 1초 조각을 보내고 WS `stop` → 종료 시각 `PUT`(`stage/sendEnding.ts`) → 성공하면 세션 행에 보냈다고 적고 분석 대기로, 실패하면 재시도 화면으로.

## 7. 로딩 · 빈 · 오류 · 부분 실패

| 상황 | 화면 |
|---|---|
| 준비 정보로 시작할 수 없음 | 발표자료 없음 · 대본 없음(또는 나누는 중) · 장 수 다름 → 사유 문구, 시작 막음 |
| 새 대본을 아직 못 씀 | "새 대본(vN)은 아직 나누는 중이라 지난 대본(vM)으로 연습해요" |
| 넘겨받은 조합보다 새 조합이 있음 | "최신 조합이 있어요: 자료 vN · 대본 vM" + [최신으로] |
| 서버가 지난 자료를 고름 | "가장 최근 자료(vN · M쪽)는 대본(K장)과 장 수가 달라 지난 자료(vJ)로 연습하게 돼요"(장 수가 다를 때) 또는 "가장 최근 자료(vN) 대신 지난 자료(vJ)로 연습하게 돼요" → "지난 자료로 연습할지, 매핑을 다시 확인할지 골라 주세요." 고르기 전에는 시작 막음 |
| 최신 자료 목록을 못 받음 | "가장 최근 자료를 확인하지 못해 지금 자료(vN)가 최신인지 알 수 없어요" → 다시 확인할지, 이 자료로 연습할지 고르게 함. 받는 중 · 다시 받는 중에도 시작 막음 |
| 카메라 · 마이크 권한 · 장치 오류 | 사유별 문구(`media/useCameraStream.ts` `DEVICE_ERROR_MESSAGE`). 장치 점검에서는 시작을 막고, 리허설 화면에서 카메라만 못 열면 시선 없이 시작합니다(6-3) |
| 마이크는 열렸는데 입력이 0 | 무음 경고(이어폰 분리 · 다른 앱 점유 · 시스템 음소거). 평소 목소리를 읽는 동안만 셉니다 |
| 시선 기준점 실패 | 사유마다 다른 재시도 안내(`useGazeSetup.ts`) |
| 발표 중 시선이 기록되지 않음 | 발밑 안내 한 줄(`lib/gazeNote.ts`). 심하면 Take 전체에서 시선을 뺍니다(`lib/gazeExclusion.ts`) |
| 기록 쓰기 실패 | 쓰기 실패 장부(`lib/writeFailures.ts`)에 남깁니다 |
| 종료를 못 보냄 | 재시도 화면 |

## 8. 시간 · 성능 제약

| 값 | 크기 | 비고 |
|---|---|---|
| 판정 · 기록 주기 | 1초 | 최소 구간 1,000ms. 테두리는 초당 한 번만 바꿉니다 |
| 음성 프레임 | 16kHz Int16, 100ms | `media/pcmCapture.ts` |
| 분석 프레임 속도 | AI `analysis_fps`와 같은 값 | `media/frameClock.ts` |
| 시계 | `performance.now()` 기준 | 가려진 탭에서도 정확합니다(`shared/lib/clock.ts`) |

- 시선 구간 합계의 시간 필드(`lib/gazeSegments.ts`가 만들고 검증): `measuredMs + uncertainMs === trackedMs`, `cameraMs + bottomMs === measuredMs`. 시선 퍼센트의 분모는 `measuredMs`입니다. 발표 중 서버로 가는 WS `metrics`의 모양은 [docs/ai/architecture.md](../../ai/architecture.md) 6-2를 따릅니다.
- 측정 제외는 `0`이 아니라 `null`입니다.

- 재연결 · 쌓아 두는 범위는 6-3 표를 봅니다(백오프 3회 → 4~6초 간격, 5분 30초에 멈춤, 기록 5분 치, 음성 2초 치).

> **작성 필요** — `useGazeWorker`의 프레임 예산 · 백프레셔 수치(`/dev/media` 계기판 기준).

## 9. 테스트

| 무엇 | 위치 |
|---|---|
| 장치 점검 | `prepare/DeviceCheckPage.test.tsx` · `prepareInfo.test.ts` · `deviceCheckLink.test.ts` |
| 리허설 · 이어 가기 · 끝내기 | `stage/*.test.ts(x)` |
| 실시간 연결 | `lib/sttSocket` · `sttProtocol` 시험 |
| 시선 워커 | `src/workers/gaze.worker.test.ts` · `aiAdapter.test.ts` · `calibrationModel.test.ts` |
| 손으로 확인 | `/dev/media`(프레임 예산) · `/dev/stt`(실서버 연결, `VITE_USE_MOCK=false`로 3000 포트) · `/dev/stage`(무대 · 테두리가 리렌더 없이 바뀌는지) |

> **작성 필요** — 실제 카메라 · 마이크가 필요한 확인 절차(다른 기기는 `npm run dev:lan`).

## 10. 열린 질문 · 알려진 한계

- **BE `GET /prepare`의 짝 규칙** — 아직 "장 수가 같은 가장 최근 발표자료"라 FE 피치 수정의 규칙(가장 최근 대본은 최신 슬라이드)과 다릅니다. 지금은 장치 점검이 지난 자료를 알리고 확인받아 메웁니다(2절). BE 규칙을 맞출지는 BE와 정할 안건입니다([pitch-create.md](pitch-create.md) 10절).
> **작성 필요** — 발표 중 마이크 · 카메라가 끊겼을 때의 화면. 장치가 끊긴 것은 알아채지만(`media/useCameraStream.ts`의 트랙 `ended`), 사용자에게 보이는 안내 · 다시 연결 흐름을 정리해야 합니다.
- 미정 항목(I-03 · I-17 · I-18 · I-19)은 [frontend/AGENTS.md](../../../frontend/AGENTS.md) "아직 안 정해진 것"을 봅니다.
