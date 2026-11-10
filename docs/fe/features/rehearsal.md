# 장치 점검 · 리허설 (FE)

> **기준 범위**: 장치 점검과 리허설 화면의 FE 기준 문서입니다. 지켜야 할 결정은 [frontend/AGENTS.md](../../../frontend/AGENTS.md) 1~9번, WebSocket 메시지 · Take 상태 · 재연결 · 종료의 서버 규칙은 [realtime-stt.md](../../be/realtime-stt.md) · [take-lifecycle.md](../../be/take-lifecycle.md), 1초 기록의 모양은 [AI architecture.md](../../ai/architecture.md) 6-2, 시선 엔진은 [gaze-tracking.md](../../ai/features/gaze-tracking.md)가 기준입니다. 이 문서에는 FE가 그 규칙을 화면에 붙인 방식만 적습니다.
>
> **한 줄 요약**: 장치 점검에서 Take를 만들고, 발표 중에는 WebSocket 하나로 음성과 1초 기록을 보내며, 끊겨도 발표는 이어 가고 끝내기는 WS `stop`과 종료 시각 `PUT`뿐입니다.
>
> **기준값**: 재연결 · 포기 시간은 `lib/sttSocket.ts`, 시작 조건은 `stage/startGate.ts`, 음성 형식은 `media/pcmCapture.ts`, 분석 프레임 속도는 `media/frameClock.ts`의 값이 기준입니다. 6-3 · 8절의 숫자는 그 값을 옮긴 것입니다.

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

- **장치 점검**에서 카메라 · 마이크 · 시선 기준점을 확인하고, 시작 버튼으로 Take를 만듭니다. Take는 여기서만 생깁니다.
- **리허설**에서는 WebSocket 하나로 음성(PCM)과 1초 기록(`metrics`)을 보내고, 실시간 전사와 코치 한마디를 받습니다.
- **끝내기**는 WS `stop`과 종료 시각 기록(`PUT`)뿐입니다. 기록을 한꺼번에 올리는 단계는 없습니다.
- **브라우저 밖으로 나가지 않는 것**: 카메라 영상, 시선 기준점. 음성은 보내기만 하고 로컬에 남기지 않습니다. IndexedDB의 기록은 새로고침 · 재시도를 위한 사본입니다.

| 화면 | 주소 |
|---|---|
| 장치 점검 | `/pitch/:pitchId/device-check` |
| 리허설 | `/takes/:takeId/rehearsal`(코칭) · `/takes/:takeId/exam`(실전) |

- `/pitch/:pitchId/prepare`는 장치 점검으로 넘기는 옛 주소입니다(`prepare/PrepareRedirect.tsx`).
- 장치 점검 · 리허설 · 실전은 휴대폰 · 태블릿에서 열지 않고 `/unsupported`로 보냅니다(`app/routes.tsx`의 `practicePaths`를 `DesktopGate`가 감쌈, #710). 분석 대기 · 재시도는 휴대폰에서도 열립니다.

지켜야 할 결정(시선 3값 · 1초 주기 · 시간 필드 규칙 · 끊겨도 발표는 계속 등)은 [frontend/AGENTS.md](../../../frontend/AGENTS.md) 1~9번이 기준입니다.

## 1. 왜 필요한가 · 용어

| 용어 | 뜻 |
|---|---|
| 장치 점검 | 발표 전에 카메라 · 마이크 · 시선 기준점을 확인하는 화면. frontend/AGENTS.md의 "시작 전 세팅"과 같은 화면입니다 |
| Take | 연습 한 번. 아래 "Take를 만들 때 보내는 값" 참고 |
| 연습 조합 | 피치 수정에서 넘겨받은 버전 묶음(`PracticeCombo`). 없으면 서버 `GET /prepare`가 고른 짝을 씁니다 |
| 대본 표시 방식 | 발표 중 대본을 어떻게 띄울지. 끄면(`OFF`) 실전 모드, 그 밖은 코칭 모드입니다. 시작하면 못 바꿉니다 |
| 시선 기준점 | 이 기기 · 이 배치에서 "카메라를 본다"의 기준(Calibration). 탭 메모리에만 둡니다 |
| 1초 기록 | 1초마다 만드는 시선 · 음량 기록. WS `metrics`로 보냅니다 |
| 시선 3값 | `CAMERA` · `BOTTOM` · `UNCERTAIN`. 화면은 이 셋만 압니다 |
| 기준 목소리 | 장치 점검에서 잰 기준 음량(`base_level_db`). 목소리 크기는 이 값과의 차이(`voice_diff_db`, 화면 이름 "기준 대비 변화")로 판정합니다. **시작 조건입니다** — 이번에 재거나, 같은 환경(같은 탭 · 같은 마이크 · 자동 음량 조절 꺼짐 · 처음 잰 지 30분 안)이면 직전 값을 다시 쓰거나, 읽기에 두 번 실패하면 탈출구로 `null`을 보내 AI가 발표 첫 발화로 잡습니다 (#1179) |

**Take를 만들 때 보내는 값** — 슬라이드 버전, 대본 버전, 연습 모드, 대본 표시 방식, `client_session_id`.

- `client_session_id`가 멱등 키라서, 응답을 잃고 다시 보내도 Take는 하나만 생깁니다.
- 제한 시간은 보내지 않습니다. BE가 피치의 지금 설정으로 기록합니다.

## 2. 화면 흐름

```
/pitch/:pitchId/device-check   장치 점검
   │  장비 준비(카메라 · 마이크) → 시선 기준점 → 기준 목소리(재기 · 다시 쓰기 · 탈출구) → 대본 표시 방식
   │  · 서버가 지난 자료를 골랐으면 확인받은 뒤에 시작 (2-3)
   │  · 다른 창 · 기기에서 발표 중인 Take 가 있으면 끝낼지 묻기
   │
   │  [Take N 시작하기] (실전이면 [Take N 실전 모드로 시작]) → POST /takes → POST /takes/{id}/calibration (보정 요약)
   ▼
/takes/:takeId/rehearsal | /exam   리허설
   │  마이크가 열리고 첫 ready 를 받으면 시계 시작 (15초 넘게 못 받으면 [다시 시도])
   │  슬라이드 넘기기 · 대본 · 시선 테두리 · 코치 한마디
   │  [끝내기] → 한 번 더 확인   또는   서버가 auto_end 로 끝냄(말 없음 2분 · 1시간)
   ▼
WS stop + PUT /takes/{id} (종료 시각)
   ├─ 성공 → /takes/:takeId/processing   분석 대기
   └─ 실패 → /takes/:takeId/retry        재시도 (같은 세션으로 다시 보냄)
```

- 보정 요약 · 기준 목소리는 리허설로 넘어가기 전에 보내고, 실패하면 몇 번 다시 보냅니다(`lib/sendCalibration.ts`). 시선 보정 요약이 없어도 목소리 기준은 보냅니다. 끝내 응답을 못 받아도 연습은 열고, 목소리 기준을 보냈으면 무대 안내 칸에 "저장됐는지 확인하지 못했어요"를 띄웁니다.
- 재시도 화면이나 실패한 리허설에서 장치 점검으로 돌아갈 때는 처음 고른 연습 조합을 다시 넘깁니다(`prepare/deviceCheckLink.ts`).

### 2-1. 새로고침 · 창 닫기

| 상황 | 어떻게 되나 |
|---|---|
| 새로고침 | **이어 갑니다.** 발표 중이던 시각부터 다시 잽니다. 서버에서 이미 시작했으니 `ready`를 기다리지 않습니다 |
| 창을 닫음 | **이어 가지 않습니다** |
| 리허설 주소를 다른 창에서 다시 엶 | 무대를 열지 않고, abandon으로 실패(`CLOSED`) 처리한 뒤 안내 화면을 띄웁니다 |
| 다른 창이 아직 발표 중 | 아무것도 바꾸지 않습니다(서버 409 `TAKE_IN_PROGRESS`) |

이어 가기에는 세 가지를 씁니다(`stage/resume.ts`).

1. **라우터 state의 `clientSessionId`** — 장치 점검이 넘긴 값입니다. 같은 탭인지 이것으로 압니다.
2. **sessionStorage의 발표 시계 · 못 보낸 1초 기록** — `pagehide` 때 IndexedDB 쓰기는 끝나기 전에 탭이 사라질 수 있어서 sessionStorage에 둡니다.
3. **IndexedDB의 기록 사본**

규칙과 숫자(하트비트 간격 등)는 [frontend/AGENTS.md](../../../frontend/AGENTS.md) 4번과 [docs/be/take-lifecycle.md](../../be/take-lifecycle.md)가 기준입니다. 이 문서는 숫자를 8절 한 곳에만 옮깁니다.

### 2-2. 사용자당 연습은 하나

- 시작 전에 다른 창 · 기기에서 발표 중인 Take가 있는지 묻고(`GET /api/takes/active`), 있으면 끝낼지 확인합니다.
- 묻지 못하면(404 · 네트워크 오류 · 3초 넘게 늦음) 없는 것으로 보고 시작합니다. 미리 알리려는 것일 뿐이라 시작을 막지 않습니다.
- 그래도 겹치면 연결이 `TOO_MANY_STREAMS`(1008)로 거절되고 `BUSY`로 안내합니다.

### 2-3. 지난 자료 확인

**언제 생기나** — 리포트의 [Take N 연습하기], 분석 실패 화면의 [다시 연습하기], 대화의 [연습 준비로]는 피치 수정을 거치지 않고 연습 조합 없이 들어옵니다. 이때 `GET /prepare`는 "대본과 장 수가 맞는 가장 최근 자료"를 고릅니다. 그래서 장 수가 다른 새 PDF를 올린 뒤라면 지난 자료가 골라집니다.

**장치 점검이 하는 일**

- 최신 자료를 따로 받아(`shared/api/prepare.ts` `useLatestSlide`) 골라진 자료와 견줍니다.
- 지난 자료면 안내를 띄우고, 사용자가 고를 때까지 시작하지 않습니다.
- 막아 버리지는 않습니다. 일부러 지난 자료로 연습할 수도 있게 둡니다.
- 조합을 넘겨받았어도 같은 비교를 합니다. 넘겨받은 뒤 다른 탭에서 새 PDF를 올렸을 수 있기 때문입니다.

안내 문구는 7절에 있습니다.

## 3. 상태를 어디에 두나

| 무엇 | 위치 | 비고 |
|---|---|---|
| 장치 점검의 선택(대본 표시 방식 등) | Zustand `prepare/prepareStore.ts` | |
| 무대 단계 · 슬라이드 · 코치 문구 | Zustand `stage/rehearsalStore.ts` | 단계는 `RUNNING` → `ENDING` → `ENDED` 한 방향으로만 갑니다 |
| 프레임 펌프 상태 · 마이크 RMS · 시계 숫자 | `useRef` · DOM 직접 갱신 | 프레임마다 React를 다시 그리지 않습니다. 시선 테두리는 `dataset.gaze`로 바꿉니다 |
| 세션 · 1초 시선 기록 · 슬라이드 전환 · 시선 성능 | IndexedDB `lib/db.ts` | 키는 `clientSessionId`. 이어 가기 · 재시도용 사본입니다 |
| 발표 시계(이어 가기용) | sessionStorage `stage/resume.ts` | 2-1 참고 |
| 시선 기준점 | 탭 메모리 `lib/zoneRefMemory.ts` | 브라우저 저장소에는 두지 않고, 서버에는 품질 요약만 보냅니다. 배치가 같은지는 `lib/layoutSignature.ts`로 봅니다 |
| 준비 정보 · Take 생성 | TanStack Query `shared/api/prepare.ts` | |

## 4. 폴더 · 주요 파일

모두 `frontend/src/features/rehearsal/` 아래입니다. 시선 워커는 `src/workers/`, 시선 엔진은 `src/vendor/gaze/`에 있습니다. 표는 처음 읽을 때 볼 파일만 골랐습니다.

서버 호출 중 Take 만들기 · 끝내기 · 중단은 `api/take.ts`에 있습니다. 개발 검증 화면은 `src/dev/`에 있습니다(표 맨 아래).

**장치 점검**

| 파일 | 하는 일 |
|---|---|
| `prepare/DeviceCheckPage.tsx` | 점검 셋 · 시작 · 연습 조합 결정 |
| `prepare/CheckCard.tsx` · `ScreenFrame.tsx` · `ScriptModeChoice.tsx` · `MissionCard.tsx` | 점검 카드 · 화면 틀 · 대본 표시 방식 고르기 · 이번 Take 목표 |
| `prepare/PrepareRedirect.tsx` | 옛 주소 `/pitch/:id/prepare` → 장치 점검 |
| `prepare/useGazeSetup.ts` · `GazeSetupView.tsx` | 시선 기준점. 상자 안은 AI 카메라 화면 모듈(`vendor/gaze/camera`)이 그립니다 |
| `prepare/useBaseVoice.ts` · `media/baseVoice.ts` | 기준 목소리 재기 |
| `lib/baseVoiceMemory.ts` | 같은 환경에서 다시 쓸 기준 목소리. 탭 메모리에만 둡니다 |
| `lib/sendCalibration.ts` | 보정 요약 · 기준 목소리 다시 보내기 |
| `prepare/prepareInfo.ts` | 준비 정보 변환 · 시작할 수 없는 이유 · 연습 조합 고르기 · 지난 자료 안내 |
| `prepare/activeTake.ts` · `ReplaceTakeDialog.tsx` | 다른 곳에서 발표 중인 Take 확인 |

**리허설**

| 파일 | 하는 일 |
|---|---|
| `stage/RehearsalPage.tsx` | 무대 · 이어 가기 · 끝내기 · 자동 종료 |
| `stage/openDevices.ts` · `startGate.ts` · `useStageClock.ts` | 장치 여는 순서 · 시계를 돌려도 되나(첫 연결 15초) · 머리줄 시계 |
| `stage/StageLiveBadge.tsx` | 발표가 돌고 있을 때만 켜지는 "STAGE LIVE" 표시(`startGate.ts` `isRehearsalRunning`) |
| `stage/ScriptPane.tsx` · `useRehearsalMaterials.ts` · `lib/scriptMarks.ts` | 발표 중 대본 · 슬라이드 · 대본 자료 읽기 · 대본 표시 |
| `stage/useSlideDeck.ts` · `useSlideSender.ts` · `lib/slideTimeline.ts` | 슬라이드 넘기기 · WS `slide` · 그 시각의 슬라이드 |
| `stage/SlideClickArea.tsx` · `NextSlideButton.tsx` · `releaseFocus.ts` | 마우스로 넘기기 — 슬라이드 좌클릭은 다음 장, 우클릭은 이전 장(파워포인트와 같음), 다음 장 칸(썸네일 + 문구)을 누르면 다음 장입니다. 모두 키보드(← → · Space)와 같은 `go`를 불러 장 넘김 기록이 한 경로로 남습니다. 슬라이드 위에 투명하게 겹쳐 칸 크기 · 자리(시선 기준)는 바꾸지 않고, 발밑 안내 줄에 조작을 적습니다. 마우스로 넘기면 다른 버튼에 남은 초점을 풀어 그 뒤 Space · ← → 가 장을 넘깁니다. 소리 재개 안내는 "슬라이드 밖을 한 번 클릭"입니다(슬라이드를 누르면 장이 넘어갑니다) |
| `stage/useLiveGaze.ts` · `useMetricsSender.ts` · `lib/metricsClock.ts` | 시선 1초 기록 받기 · WS `metrics` 보내기 · 어느 1초로 보낼지 |
| `stage/useSttStream.ts` · `lib/sttSocket.ts` · `lib/sttProtocol.ts` | 실시간 연결(React를 모르는 상태기계) |
| `stage/useLiveCoach.ts` · `lib/coachIndicators.ts` · `CoachStrip.tsx` | 코치 한마디 · 상태 표시 |
| `stage/sendEnding.ts` · `canEnd.ts` · `useEndConfirm.ts` · `resume.ts` | 종료 시각 기록 · 끝내기 가능 여부 · 확인 · 이어 가기 |

**미디어 · 기록 · 그 밖**

| 파일 | 하는 일 |
|---|---|
| `media/useCameraStream.ts` · `useVideoStream.ts` | 권한 · 스트림 · 실제로 프레임이 흐르는지 |
| `media/pcmCapture.ts` | 마이크 → 16kHz Int16 100ms 프레임. 음성이 서버로 가는 유일한 경로 |
| `media/level.ts` · `loudness.ts` · `aWeighting.ts` · `voiceRecord.ts` · `useMicLevel.ts` | 음량 · 무음 경고 · 1초 음량 기록 |
| `media/useGazeWorker.ts` | 프레임 펌프 · 백프레셔(성능의 핵심) |
| `lib/db.ts` · `writeFailures.ts` | IndexedDB · 쓰기 실패 장부 |
| `lib/gazeExclusion.ts` · `gazeNote.ts` | Take 전체에서 시선을 뺄지 · 기록되지 않을 때의 안내 |
| `retry/RetryPage.tsx` | 종료를 못 보냈을 때 다시 보내기 |
| `src/dev/MediaDevPage.tsx` · `SttDevPage.tsx` · `StageDemo.tsx` | `/dev/media` · `/dev/stt` · `/dev/stage` (개발 검증용, 제품 화면 아님) |

## 5. BE · AI 계약

| 무엇 | 방식 · 경로 | 기준 문서 |
|---|---|---|
| 준비 정보 | `GET /api/pitches/{id}/prepare` | [AI architecture.md](../../ai/architecture.md) 1-2 · BE `module/pitch/dto.py` `PrepareDTO` |
| Take 생성 · 보정 요약 | `POST /api/pitches/{id}/takes/` · `POST .../takes/{takeId}/calibration` | [AI architecture.md](../../ai/architecture.md) 1-2 · BE `module/take/dto.py` `TakeInitRequestDTO` |
| 발표 중인 Take 묻기 · 끝내기 | `GET /api/takes/active` · `POST .../abandon?replace=true` | [take-lifecycle.md](../../be/take-lifecycle.md) (BE #718 · #719에서 추가) |
| 실시간 연결 | WebSocket `/api/ws/takes/{id}` | [realtime-stt.md](../../be/realtime-stt.md) · [take-lifecycle.md](../../be/take-lifecycle.md) · [AI architecture.md](../../ai/architecture.md) 1-3 · 1-4 |
| 1초 기록 모양 | WS `metrics` (시선 · 음량 · 슬라이드 번호) | [AI architecture.md](../../ai/architecture.md) 6-2 · [gaze-tracking.md](../../ai/features/gaze-tracking.md) · [volume.md](../../ai/features/volume.md) 5-2 |
| 종료 시각 · 중단 | `PUT /api/pitches/{id}/takes/{takeId}` · `POST .../abandon` | [take-lifecycle.md](../../be/take-lifecycle.md) |
| 코치 | WS로 받음 | [realtime-coach.md](../../be/realtime-coach.md) · [coach-agent.md](../../ai/features/coach-agent.md) |

**WebSocket 메시지**

| 방향 | 메시지 |
|---|---|
| FE → 서버 | `auth`(연결 직후 토큰) · PCM 음성 · `metrics` · `slide` · `stop` |
| 서버 → FE | `ready` · `transcript`(전사) · `stt_status`(전사 상태) · `coach` · `auto_end`(서버가 끝냄) · `error` |

- 메시지 모양은 [AI architecture.md](../../ai/architecture.md) 1-4가 기준이고, FE는 `lib/sttSocket.ts`에서 받습니다.
- 코치는 AI 코치 에이전트가 판단하고, FE는 띄우기만 합니다.
- 예전 계약의 `POST /takes/{id}/complete`(기록을 한꺼번에 올림)는 BE가 만들지 않기로 했습니다(#134). `lib/gazePayload.ts`는 `/dev/media` 확인용으로만 남아 있습니다.

## 6. 처리 흐름

### 6-1. 시선

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
        │  1초 기록(GazeSampleRecord)만 메인으로 보냄
        ▼
stage/useLiveGaze.ts            1초 기록 받기
        ├─▶ lib/db.ts appendGazeSamples      IndexedDB (사본)
        ├─▶ 시선 테두리 · 발밑 안내           RehearsalPage 가 dataset.gaze 를 바꿈 · lib/gazeNote.ts
        └─▶ stage/useMetricsSender.ts         음량 1초 기록 · 슬라이드 번호를 붙여서
                    │
                    ▼
            lib/sttSocket.ts sendMetrics      WS metrics (끊기면 쌓았다가 다시 붙으면 보냄)
```

- **기준점** — 장치 점검에서 `prepare/useGazeSetup.ts`가 `vendor/gaze/camera`의 카메라 화면으로 잡고, 탭 메모리(`lib/zoneRefMemory.ts`)에 둡니다. 서버에는 요약만 보냅니다(`lib/calibrationRequest.ts`).
- **슬라이드 번호** — 그 1초가 *시작한 시각*의 번호를 붙입니다. 1초 기록은 조금 늦게 나가서, 보내는 순간의 번호를 붙이면 넘길 때마다 직전 몇 초가 다음 슬라이드로 적히기 때문입니다.

### 6-2. 음성

```
media/pcmCapture.ts (AudioWorklet → 16kHz Int16 100ms 프레임) → lib/sttSocket.ts → WS
```

- 로컬에 저장하지 않습니다.
- 멈춘 AudioContext는 사용자의 첫 클릭 · 키 입력에서 다시 켭니다(`media/resumeOnGesture.ts`).

### 6-3. 시작과 끝

**시작 조건**

| 화면 | 시작 조건 |
|---|---|
| 장치 점검 | 카메라 · 마이크 · 시선 기준점 |
| 리허설 | **마이크와 서버 연결**(첫 `ready`). 카메라는 조건이 아닙니다 |

- 리허설 화면에서 카메라를 못 열면 마이크만으로 시작하고 시선만 뺍니다(#445 카메라만 못 열어도 시작이 멈추던 버그, `stage/openDevices.ts`).
- 서버 연결이 필요한 이유: 첫 연결이 서버에서 Take를 시작합니다(`READY` → `RUNNING`). 그 전에 시작하면 기록이 남지 않습니다(`stage/startGate.ts`, #381 마이크 · 실시간 연결 없이 시작하지 않게 함).
- 첫 `ready`를 15초 넘게 못 받으면 [다시 시도]를 보입니다(`startGate.ts` `CONNECT_SLOW_MS`). 누르면 소켓을 새로 만들고 처음부터 다시 셉니다. 끝내 붙지 않은 Take는 "이 연습은 시작하기 전에 끝났어" 화면으로 갑니다.
- 시작하지 않은 Take는 서버가 만든 지 10분 뒤 끝냅니다. 그 Take의 끝내기는 409 `TAKE_NOT_STARTED`로 거절됩니다.

**연결이 끊기면** — 발표는 멈추지 않습니다. 다만 잃는 것이 있습니다.

| 끊긴 동안 | 무엇 |
|---|---|
| 계속 됨 | 시선 테두리(워커 판정) · 시계 · 슬라이드 · 기록 쌓기 |
| 멈춤 | 코치 한마디 · 상태 표시(지우지 않고 흐리게 둠) · 말하기 분석 |
| 다시 붙으면 보냄 | 1초 기록(`metrics`, 300개 = 5분 치까지)과 장 넘김(`slide`, 상한 없음) |
| 잃음 | 끊긴 구간의 음성(버퍼는 2초 치). 그 구간의 말 속도 · 군더더기 분석이 빠집니다 |

- 다시 붙기: 지수 백오프 3회 → 그 뒤로는 횟수 제한 없이 4~6초(jitter)마다.
- 끊긴 지 5분 30초가 지나면 멈춥니다. 서버가 그때 끊긴 Take를 끝내기 때문입니다.
- 끊김의 기준은 소켓 연결입니다. 서버 STT가 `degraded`여도 소켓이 붙어 있으면 시선 · 음량 코칭은 계속됩니다.

**끝내기**

1. 마지막 1초 조각을 보냅니다.
2. WS `stop`을 보냅니다.
3. 종료 시각을 `PUT`합니다(`stage/sendEnding.ts`).
4. 성공하면 세션 행에 보냈다고 적고 분석 대기로, 실패하면 재시도 화면으로 갑니다.

**서버가 끝낼 때(자동 종료)** — 말이 없는 상태가 2분 넘게 이어지거나(정확한 조건은 `shared/lib/autoEnd.ts` 주석) 연습이 1시간에 닿으면 서버가 `auto_end`를 보냅니다.

- FE는 [끝내기]와 같은 순서(마지막 조각 → `stop` → `PUT`)를 밟습니다. 서버는 마지막 `metrics`를 2초만 기다립니다.
- 끝난 이유(`SILENCE` · `TIME_LIMIT`)와 서버 문구를 라우터 state `autoEnd`에 실어 분석 대기로 넘기고, 분석 대기가 안내합니다([report.md](report.md) 3절).
- 장치 점검이 미리 알립니다: "말이 없는 상태가 2분 넘게 이어지면 연습이 자동으로 끝날 수 있어요. 발표 목표 시간과는 상관없어요."(`SILENCE_END_NOTICE`)

## 7. 로딩 · 빈 · 오류 · 부분 실패

**장치 점검**

| 상황 | 화면 | 시작 |
|---|---|---|
| 준비 정보로 시작할 수 없음 | 발표자료 없음 · 대본 없음(또는 나누는 중) · 장 수 다름 → 사유 문구 | 막음 |
| 새 대본을 아직 못 씀 | 안내 ① | 됨 |
| 넘겨받은 조합보다 새 조합이 있음 | 안내 ② + [최신으로] | 됨 |
| 서버가 지난 자료를 고름 | 안내 ③ 또는 ④ → 안내 ⑤ | 고를 때까지 막음 |
| 최신 자료 목록을 못 받음 | 안내 ⑥ → 다시 확인할지, 이 자료로 연습할지 고름 | 고를 때까지 막음(받는 중 · 다시 받는 중에도) |
| 카메라 · 마이크 권한 · 장치 오류 | 사유별 문구(`media/useCameraStream.ts` `DEVICE_ERROR_MESSAGE`) | 막음 |
| 마이크는 열렸는데 입력이 0 | 무음 경고(이어폰 분리 · 다른 앱 점유 · 시스템 음소거). 평소 목소리를 읽는 동안만 셉니다 | — |
| 화면 아래 알림 줄 | 자동 종료 미리 안내(`SILENCE_END_NOTICE`, 6-3) | — |
| 시선 기준점 실패 | 사유마다 다른 재시도 안내(`useGazeSetup.ts`) | 막음 |

**리허설 · 끝내기**

| 상황 | 화면 |
|---|---|
| 카메라를 못 엶 | 시선 없이 시작(6-3) |
| 첫 연결이 15초 넘게 안 됨 | [다시 시도] (6-3) |
| 서버가 자동으로 끝냄 | 끝내기와 같은 순서로 분석 대기에 가서 이유를 안내(6-3) |
| 발표 중 시선이 기록되지 않음 | 발밑 안내 한 줄(`lib/gazeNote.ts`). 심하면 Take 전체에서 시선을 뺍니다(`lib/gazeExclusion.ts`) |
| 기록 쓰기 실패 | 쓰기 실패 장부(`lib/writeFailures.ts`)에 남깁니다 |
| 종료를 못 보냄 | 재시도 화면 |

**안내 문구**

1. "새 대본(vN)은 아직 나누는 중이라 지난 대본(vM)으로 연습해요"
2. "최신 조합이 있어요: 자료 vN · 대본 vM"
3. "가장 최근 자료(vN · M쪽)는 대본(K장)과 장 수가 달라 지난 자료(vJ)로 연습하게 돼요" — 장 수가 다를 때
4. "가장 최근 자료(vN) 대신 지난 자료(vJ)로 연습하게 돼요" — 그 밖
5. "지난 자료로 연습할지, 매핑을 다시 확인할지 골라 주세요."
6. "가장 최근 자료를 확인하지 못해 지금 자료(vN)가 최신인지 알 수 없어요"

## 8. 시간 · 성능 제약

| 값 | 크기 | 비고 |
|---|---|---|
| 판정 · 기록 주기 | 1초 | 최소 구간 1,000ms. 테두리는 초당 한 번만 바꿉니다 |
| 음성 프레임 | 16kHz Int16, 100ms | `media/pcmCapture.ts` |
| 분석 프레임 속도 | 초당 8장 (AI `analysis_fps`와 같은 값) | `media/frameClock.ts` `ANALYSIS_FPS` |
| 첫 연결 대기 | 15초 | `stage/startGate.ts` `CONNECT_SLOW_MS` |
| 자동 종료 | 말 없음 2분 · 최대 1시간 | 서버가 정함. FE 안내는 `shared/lib/autoEnd.ts` |
| 시계 | `performance.now()` 기준 | 가려진 탭에서도 정확합니다(`shared/lib/clock.ts`) |
| 다시 붙기 | 백오프 3회 → 4~6초 간격, 5분 30초에 멈춤 | 6-3 |
| 끊긴 동안 쌓는 양 | 1초 기록 300개(5분 치) · 음성 2초 치 · 장 넘김은 상한 없음 | 6-3 |

**값 규칙**

- 시선 구간 합계(`lib/gazeSegments.ts`가 만들고 검증): `measuredMs + uncertainMs === trackedMs`, `cameraMs + bottomMs === measuredMs`. 시선 퍼센트의 분모는 `measuredMs`입니다.
- 측정하지 못한 값은 `0`이 아니라 `null`입니다.
- 발표 중 서버로 가는 WS `metrics`의 모양은 [AI architecture.md](../../ai/architecture.md) 6-2를 따릅니다.

## 9. 테스트

| 무엇 | 위치 |
|---|---|
| 장치 점검 | `prepare/DeviceCheckPage.test.tsx` · `prepareInfo.test.ts` · `deviceCheckLink.test.ts` |
| 리허설 · 이어 가기 · 끝내기 | `stage/*.test.ts(x)` |
| 실시간 연결 | `lib/sttSocket.test.ts` · `lib/sttProtocol.test.ts` |
| 시선 워커 | `src/workers/gaze.worker.test.ts` · `aiAdapter.test.ts` · `calibrationModel.test.ts` |
| 장치 점검 브라우저 끝까지(E2E) | `frontend/e2e/device-check.e2e.ts` — 가짜 카메라 · 마이크로 기준 목소리 재기 · 그만두기 · 두 번 실패 뒤 탈출구. 시선 모델 파일이 없어 시선 기준은 '사용 불가'라 시작 · 다시 쓰기 · 보정 값 보내기는 보지 않습니다 |

**손으로 확인하는 페이지** (개발 검증용)

| 주소 | 무엇을 보나 |
|---|---|
| `/dev/media` | 프레임 예산 |
| `/dev/stt` | 실서버 연결(`VITE_USE_MOCK=false`, 3000 포트) |
| `/dev/stage` | 무대 레이아웃 · 테두리가 리렌더 없이 바뀌는지 |

## 10. 열린 질문 · 알려진 한계

- **BE `GET /prepare`의 짝 규칙** — 연습 조합 없이 장치 점검에 들어오면(리포트의 [Take N 연습하기] · 분석 실패 화면의 [다시 연습하기] · 대화의 [연습 준비로] · 주소 직접 입력) BE가 짝을 고릅니다. 이 문서의 기준 항목이고, [pitch-create.md](pitch-create.md) · [report.md](report.md) 10절은 여기를 가리킵니다.
  - BE 규칙은 아직 "장 수가 같은 가장 최근 발표자료"라, FE 피치 수정의 규칙(가장 최근 대본은 최신 슬라이드, [pitch-create.md](pitch-create.md) 6-3)과 다릅니다. 그래서 새 PDF를 올린 뒤에는 지난 자료가 골라질 수 있습니다.
  - 지금은 장치 점검이 지난 자료를 알리고 확인받은 뒤에만 시작해서, 말없이 지난 자료로 연습되지는 않습니다(2-3).
  - BE 규칙을 FE와 맞출지는 BE와 정할 안건입니다.
- **미정 항목**(I-03 · I-17 · I-18 · I-19) — [frontend/AGENTS.md](../../../frontend/AGENTS.md) "아직 안 정해진 것"을 봅니다.
