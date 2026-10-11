# 입출력 — 시선 분석 (브라우저 엔진 + 서버 코어)

시선 기능이 무엇을 받아 무엇을 돌려주는지 정리합니다. FE · BE · AI가 각자 맡은 경계를 연결할 때 이 문서를 기준으로 삼습니다.

- **시선 판정은 사용자 브라우저 안에서 합니다.** 카메라 프레임 · 랜드마크 · 얼굴 측정값은 기기를 떠나지 않습니다. 서버로 가는 것은 **1초 기록**(필드 8개)뿐입니다.
- **서버 코어는 1초 기록만 읽습니다.** 코치 이슈 · 테이크 요약 · 이전 테이크 비교 · 피드백 효과를 만듭니다.
- **필드의 원본은 코드입니다.** 서버 쪽은 `src/gaze/schemas.py`(pydantic) · `src/gaze/core.py` · `src/gaze/config.py`, 브라우저 쪽은 `web/src/engine/`과 `web/src/worker/protocol.ts`입니다. 문서와 코드가 다르면 코드가 맞습니다.
- 아래 필드 표는 그 모델에서 뽑아 만들었고, 예시 JSON은 코어를 실제로 돌려 얻은 출력입니다(긴 목록은 일부만 남겼다고 적어 두었습니다).
- 경로 기준: 서버 코어 `src/gaze/`는 나중에 `ai/service`의 `features/gaze/`로, 브라우저 엔진 `web/src/engine/`은 `frontend/src/workers/gaze/engine/`으로 옮기는 것을 제안합니다([DEPLOY.md](DEPLOY.md)). 이 문서의 경로는 현재 위치(`ai/research/gaze-tracking/`) 기준입니다.
- `src/gaze_lab/`은 파이썬 기준 구현(연구 전용)입니다. 서비스가 부르는 코드가 아닙니다.

## 목차

1. [경계 한눈에 보기](#1-경계-한눈에-보기)
2. [브라우저 엔진](#2-브라우저-엔진)
3. [1초 기록 (전송 단위)](#3-1초-기록-전송-단위)
4. [서버 코어 진입점](#4-서버-코어-진입점)
5. [이슈 종류](#5-이슈-종류)
6. [출력 데이터 모델](#6-출력-데이터-모델)
7. [임계값](#7-임계값)
8. [버전과 호환](#8-버전과-호환)

---

## 1. 경계 한눈에 보기

```
[사용자 브라우저]                                              [서버]
카메라 ─ 프레임 ─▶ 엔진: 프레임 판정 ─▶ 1초 기록 ─▶ FE ─▶ BE(저장) ─▶ AI 서버 코어
                  (FrameDecision)      (8필드)                        parse_records
                       │                                              normalize_samples
                  CalibrationModel                                    ├ evaluate_gaze        (코치: 지금의 이슈)
                  (메모리 전용)                                        ├ take_summary         (리뷰: 테이크 통계)
                                                                      ├ compare_summaries    (리뷰: 이전 테이크 비교)
                                                                      └ intervention_outcome (코치: 피드백 효과)
```

| 데이터 | 어디에 있나 | 기기를 떠나나 |
|---|---|---|
| 카메라 프레임 · 얼굴 랜드마크 | 엔진이 프레임마다 쓰고 버립니다 | 아니오 |
| `CalibrationModel` (보정 모델, 얼굴 위치 · 크기 등 측정값 포함) | 세션 동안 메모리에만 | **아니오. 저장도 금지** |
| `FrameDecision` (프레임 판정, 머리 각도 · 확률 포함) | 프레임마다 만들고 1초 기록의 재료로만 씁니다 | 아니오 |
| **1초 기록** (`GazeSampleRecord`) | 1초마다 하나 | **예. 이것만 나갑니다** |

서버(코어)는 파일 · DB · 네트워크 · 환경변수를 쓰지 않습니다. 저장은 BE의 몫이고, 코어는 받은 기록을 읽기만 합니다.

---

## 2. 브라우저 엔진

위치는 `web/src/engine/`입니다(진입은 `index.ts`). DOM 없이 돌아서 Web Worker 안에서 씁니다.

### 2-1. 시작 `createClassifier`

```ts
createClassifier(opts: CreateOptions): Promise<GazeEngine<ImageBitmap>>
```

| 옵션 | 기본값 | 뜻 |
|---|---|---|
| `assetDir` | 필수 | `face_landmarker.task`와 MediaPipe wasm 파일을 서빙하는 경로. CDN이 아니라 호출한 쪽이 서빙합니다 |
| `delegate` | `'CPU'` | 운영은 CPU입니다(파이썬과의 일치를 CPU에서만 검증). GPU는 벤치마크용 |
| `initTimeoutMs` | `60000` (`DEFAULT_INIT_TIMEOUT_MS`) | 모델 · 런타임을 이 시간 안에 못 불러오면 포기. 첫 로드는 약 16 MB |
| `otherAs` | `'UNCERTAIN'` | `OTHER` 판정을 FE 구역(`CAMERA`/`BOTTOM`/`UNCERTAIN`)의 어디로 보낼지. `'BOTTOM'`도 가능 |
| `config` | 내장 기본값 | 구역별 부분 덮어쓰기(`ConfigOverrides`). 바꾸면 보정 모델과 안 맞을 수 있습니다 |
| `reuseScreenFromPlacement` | `true` | 카메라 위치 점검의 화면 중앙 프레임을 `SCREEN` 보정 자료로 재사용 |

`checkSupport()`는 카메라를 요청하기 전에 불러도 될 만큼 가볍고 `{ ok, missing }`을 돌려줍니다. 필수는 `WebAssembly` · `createImageBitmap`이고, `OffscreenCanvas`가 없으면 밝기 검사만 건너뜁니다(`Worker`는 호출하는 쪽이 확인).

**실패 처리**: 시작하지 못하면 `EngineInitError`를 던지고 `reason`이 붙습니다. 호출하는 쪽은 어느 이유든 "시선 분석 불가"로 처리하고 발표는 계속합니다(발표가 시선을 기다리지 않습니다). 이유는 로그용입니다.

| `reason` | 언제 |
|---|---|
| `UNSUPPORTED_BROWSER` | `checkSupport()`가 실패 (`missing`에 이름이 들어감) |
| `TIMEOUT` | `initTimeoutMs` 안에 모델 · 런타임을 못 불러옴 |
| `INIT_FAILED` | 불러오다 오류가 남 (파일을 못 받음 등) |

### 2-2. `GazeEngine` 메서드

**FE 계약(보정 → 판정)** — FE의 `GazeClassifier`가 부르는 것들입니다. 입력과 출력은 파이썬 엔진 `to_dict()`와 같은 snake_case 키입니다.

| 메서드 | 한 줄 설명 |
|---|---|
| `checkPlacement(camera, screen)` | 카메라 렌즈를 본 프레임들과 화면 중앙을 본 프레임들로 카메라 위치를 점검 (`PlacementResultDict`) |
| `fitCalibration(camera, bottom)` | 렌즈 · 대본 영역을 본 프레임들로 보정. `{ quality, model }` (`model`은 만들지 못하면 `null`) |
| `calibrate(model)` | 이번 세션에서 만든 모델을 받아 판정을 시작. 모델 모양이 아니면 `false` |
| `classify(frame, tMs)` | 프레임 하나를 판정해 `FrameDecision`을 돌려줌 (평활화 전) |
| `dispose()` | 감지기를 닫음. 여러 번 불러도 됩니다 |
| `version` | `{ modelVersion, gazeBackbone, gazeClassifier }` ([8절](#8-버전과-호환)) |

**대화형(카메라 화면용)** — `web/src/camera/`가 쓰는 보정 · 점검 흐름입니다.

| 메서드 | 한 줄 설명 |
|---|---|
| `checkPreconditions(frame, tMs)` | 촬영 전 조건(얼굴 유무 · 크기 · 밝기 등) 점검 리포트 |
| `startSweep(tMs)` / `offerSweepFrame(frame, tMs)` | 머리 원 그리기 점검 시작 / 프레임 입력 |
| `startCue(cue, tMs, checkDirection?)` | 보정 단계(`CAMERA`/`SCREEN`/`BOTTOM`) 하나 시작 |
| `offerCalibrationFrame(frame, tMs)` | 보정 프레임 입력, 게이지 상태를 돌려줌 |
| `estimatePlacement()` | 지금까지 모은 프레임으로 카메라 위치 판단 |
| `finishCalibration()` | 보정을 마치고 `{ quality, model }`을 돌려줌 |
| `beginReanchor(tMs)` | 발표 중 자세가 바뀐 뒤 기준점을 다시 잡는 수집 시작 (보정 전에 부르면 오류) |

### 2-3. 프레임 판정 `FrameDecision`

`classify`와 camera 화면의 `live` 모드가 돌려주는 값입니다(`web/src/engine/contract.ts`). **1초 기록의 재료일 뿐 서버로 보내지 않습니다.**

| 필드 | 타입 | 용도 |
|---|---|---|
| `label` | `'CAMERA' \| 'BOTTOM' \| 'UNCERTAIN'` | FE 계약. 구역 확률을 FE 묶음(`CAMERA` · `SCREEN`+`BOTTOM` · `OTHER`)으로 더해 정한 값. 확신이 낮거나 `OTHER`가 이기면(`otherAs: 'UNCERTAIN'`) `UNCERTAIN` |
| `p_camera` | number | FE 계약 |
| `p_bottom` | number | FE 계약. `SCREEN`+`BOTTOM`(+ `otherAs: 'BOTTOM'`이면 `OTHER`) 확률 |
| `face_valid` | boolean | FE 계약. `false`면 1초 기록에서 `UNMEASURED`로 셉니다 |
| `t_ms` | number | 프레임 시각 |
| `state` | `CAMERA`/`SCREEN`/`BOTTOM`/`OTHER`/`UNCERTAIN` | 엔진의 4구역 판정(판정 보류는 `UNCERTAIN`). **1초 기록의 `state`가 이 값의 다수결** |
| `direction` | 8방향 \| null | `OTHER`일 때만. 1초 기록의 `direction` 재료 |
| `condition` | `ConditionState` \| null | 촬영 조건. 1초 기록의 `reliability` · `issues` 재료. 보정 전에는 `null` |
| `probs` | 구역별 확률 \| null | 표시용 |
| `uncertain_reason` | string \| null | 표시용 |
| `head_yaw_deg` · `head_pitch_deg` | number | 표시용 |
| `offset_deg` | `[오른쪽도, 위쪽도]` \| null | 표시용. 보정한 화면 영역 밖으로 얼마나 벗어났는지 |
| `aim_deg` | `[오른쪽도, 위쪽도]` \| null | 표시용. 화면 중앙을 본 자세에서 얼마나 벗어났는지 |

### 2-4. 보정 모델 `CalibrationModel`

`fitCalibration` · `finishCalibration`이 만드는 보정 결과입니다. 기준 자세(구역별 머리 방향)에 `scene`과 `configHash`가 붙습니다.

- `scene`은 **얼굴 측정값**입니다: 얼굴 중심 · 면적, 홍채 픽셀 크기, 밝기, 머리 방향, 구역별 머리 자세, 카메라까지 거리(cm).
- **메모리 전용입니다.** IndexedDB · localStorage · 서버 어디에도 저장하거나 보내지 않습니다. 세션이 끝나면 버리고 다음 세션에서 다시 보정합니다.
- 일반 데이터라 `structuredClone`을 통과합니다. 같은 세션에서 새 worker를 만들면 `calibrate(model)`로 넘겨 이어받습니다. camera 화면의 worker에서는 `{ type: 'restore', model }` 메시지가 같은 일을 하고, 이 엔진의 모델이 아니면(스키마나 설정이 다름) `{ type: 'restored', ok: false }`로 답하고 아무것도 바꾸지 않습니다.

### 2-5. 1초 기록 만들기

`web/src/engine/evidence.ts`입니다.

```
FrameDecision ─ frameFromDecision ─▶ GazeFrame ─ GazeSlicer(1초 격자, 다수결) ─▶ GazeSample ─ sampleToDict ─▶ 1초 기록
```

- `GazeEvidenceRecorder.record(decision)`은 프레임 판정을 받아 **끝난 1초 조각**을 돌려줍니다(없으면 빈 배열). `flush(tEndMs)`는 테이크 끝에서 마지막 조각을 닫습니다(`duration_ms`가 1000보다 짧을 수 있음). **테이크가 끝나면 꼭 부릅니다.** 안 부르면 마지막 1초 미만이 나가지 않습니다. `reset()`은 비웁니다.
- 시각은 `classify(frame, tMs)`에 넘긴 `tMs` 그대로입니다. 1초 격자는 **첫 프레임**에서 시작합니다. 그래서 FE는 테이크마다 새 기록기(또는 `reset()`)를 쓰고, `tMs`를 테이크 시작 기준(지금 − 테이크 시작)으로 넘깁니다. 다른 기준(예: 화면을 띄운 뒤 경과 시간)으로 넘기면 서버가 테이크 밖 기록으로 보고 버리거나, 앞부분을 측정 못 함으로 채웁니다. 만든 조각은 `samples`에 메모리로만 쌓고 기기에 저장하지 않습니다.
- 한 조각의 판정: 얼굴이 있는 프레임이 4개 미만이면 `UNMEASURED`, 이긴 상태의 득표율이 0.6 미만이면 `UNCERTAIN`, 아니면 다수결로 이긴 상태입니다. `reliability`는 조각 안 프레임의 촬영 조건 신뢰도 평균입니다.
- 프레임 사이가 1초 넘게 비었다가 다시 들어오면 그 사이도 `UNMEASURED` 조각으로 만듭니다. 프레임이 아예 끊기면(카메라 끊김 · 탭 숨김) 조각이 나오지 않고, 그 시간은 서버가 `UNMEASURED`로 채웁니다([4-1](#4-1-정리-normalize_samples)).
- `sampleToDict(sample)`이 전송 모양을 만듭니다(`confidence` · `reliability`를 소수 4자리로 반올림). FE는 이 결과를 그대로 BE에 보냅니다.

### 2-6. 카메라 화면 worker 프로토콜

`web/src/camera/view.ts`(화면)와 `web/src/worker/gaze.worker.ts`(worker)가 `web/src/worker/protocol.ts`의 메시지를 주고받습니다. FE의 `GazeWorkerIn/Out`의 상위 집합입니다.

| 방향 | 메시지 | 뜻 |
|---|---|---|
| → worker | `init { assetDir, otherAs?, delegate? }` | 엔진 시작 (`createClassifier`) |
| → worker | `frame { bitmap, tMs, mode }` | 프레임 한 장. `mode`는 `preview` / `check` / `sweep` / `calibrate` / `live` |
| → worker | `startSweep { tMs }` | 머리 원 점검 시작 |
| → worker | `startCue { cue, tMs, checkDirection? }` | 보정 단계 시작 |
| → worker | `estimatePlacement` | 카메라 위치 판단 요청 |
| → worker | `finishCalibration` | 보정 마감 |
| → worker | `resetCalibration` · `resetPreconditions` | 보정 · 사전 점검 초기화 |
| → worker | `reanchor { tMs }` | 기준점 재설정 시작 |
| → worker | `restore { model }` | 이번 세션의 보정 모델 이어받기 (메모리 전용) |
| ← worker | `ready { version, isolated }` | 시작 완료. `version`은 `modelVersion+gazeBackbone+gazeClassifier` 문자열, `isolated`는 `crossOriginIsolated` 값 |
| ← worker | `failed { message, reason? }` | 시작 실패 또는 프레임 실패 |
| ← worker | `frame { frame, check?, sweep?, gauge?, baseline?, decision?, reanchor? }` | 프레임 하나의 결과. `decision`은 `live` 모드에서만 옵니다. 프레임마다 반드시 한 번 답합니다 |
| ← worker | `placement { result }` | 카메라 위치 판단 결과 (`null` 가능) |
| ← worker | `calibrated { quality, model, placement, classes }` | 보정 결과. `model`은 `null` 가능 |
| ← worker | `restored { ok, classes }` | `restore`의 결과 |

**실패 이유** (`failed.reason`, `EngineFailure`):

| reason | 언제 | 뒤처리 |
|---|---|---|
| `UNSUPPORTED_BROWSER` · `TIMEOUT` · `INIT_FAILED` | 엔진 시작 실패 ([2-1](#2-1-시작-createclassifier)) | 시선 분석 불가로 처리 |
| `FRAME_FAILED` | 프레임 처리 중 예외 | 그 프레임은 "얼굴 없음"으로 답하고 분석은 다음 프레임부터 계속. 연속 실패 중에는 첫 번째만 알립니다 |

**화면이 내는 오류** (`CameraView`의 `error` 이벤트, `ViewFailure` = `EngineFailure` + 아래 둘):

| reason | 언제 | 뒤처리 |
|---|---|---|
| `WORKER_FAILED` | worker가 멈추거나 오류 이벤트를 냄 | 더는 프레임을 보내지 않음. 호출하는 쪽이 안내를 정합니다 |
| `CAMERA_LOST` | 카메라 트랙이 끝남(뽑힘, 다른 앱이 가져감) | 한 번만 알리고 프레임 수집을 멈춤. 이 뒤로 1초 기록이 끊기며, 서버는 그 시간을 `UNMEASURED`로 채웁니다([4절](#4-서버-코어-진입점)) |

---

## 3. 1초 기록 (전송 단위)

FE가 BE로 보내고 BE가 AI 서버 코어로 넘기는 기록입니다. 발표 중 1초마다 하나씩 생깁니다. 얼굴 영상 · 랜드마크 · 얼굴 측정값은 들어 있지 않습니다. 모델은 `src/gaze/schemas.py`의 `GazeSampleRecord`입니다.

| 필드 | 타입 | 필수 | 제약 | 뜻 |
|---|---|---|---|---|
| `t_ms` | int | 필수 | 0 이상 | 테이크 시작 기준 이 1초의 시작 시각(ms). 엔진은 `classify`에 받은 `tMs`를 그대로 쓰므로, FE가 `tMs`를 테이크 시작 기준으로 넘깁니다([2-5](#2-5-1초-기록-만들기)) |
| `duration_ms` | int | 필수 | 0 초과 | 보통 1000. 테이크 끝의 마지막 조각만 짧음 |
| `state` | string | 필수 | 6개 중 하나 | `CAMERA` · `SCREEN` · `BOTTOM` · `OTHER` · `UNCERTAIN` · `UNMEASURED` |
| `direction` | string \| null | 선택, 기본 `null` | 8방향 중 하나 | `state`가 `OTHER`일 때만. 발표자 기준 `RIGHT` · `UP_RIGHT` · `UP` · `UP_LEFT` · `LEFT` · `DOWN_LEFT` · `DOWN` · `DOWN_RIGHT` |
| `confidence` | float | 선택, 기본 0.0 | 0~1 | 이긴 상태에 투표한 프레임 비율 (`UNMEASURED`는 0) |
| `reliability` | float | 선택, 기본 0.0 | 0~1 | 촬영 조건 신뢰도의 평균 |
| `issues` | string 목록 | 선택, 기본 `[]` | | 프레임의 절반 이상에 나온 촬영 조건 이슈 이름 |
| `frames` | int | 선택, 기본 0 | 0 이상 | 이 1초에 들어온 프레임 수 |

상태의 뜻: `CAMERA`는 카메라(청중)를 봄, `SCREEN`은 화면을 봄, `BOTTOM`은 아래(대본)를 봄, `OTHER`는 그 밖의 곳을 봄입니다. `UNCERTAIN`은 얼굴은 있으나 판정을 보류한 1초, `UNMEASURED`는 얼굴이 없거나 프레임이 모자라 재지 못한 1초입니다.

**예시** (왼쪽을 본 1초)

```json
{"t_ms": 12000, "duration_ms": 1000, "state": "OTHER", "direction": "LEFT", "confidence": 0.75, "reliability": 0.91, "issues": ["OFF_CENTER"], "frames": 8}
```

### 받을 때의 규칙 `parse_records`

기록 하나가 이상해도 그 1초만 버리고 나머지는 씁니다. 버린 수는 함수가 알려 주니 로그나 응답에 남기세요.

| 상황 | 처리 |
|---|---|
| 모르는 `state`, `state` 없음 | **그 기록을 버림** |
| `confidence` · `reliability`가 0~1 밖 | 그 기록을 버림 |
| `t_ms` · `frames`가 음수, `duration_ms`가 0 이하 | 그 기록을 버림 |
| `t_ms` · `duration_ms`가 없거나 정수가 아님, 기록이 객체(dict)가 아님 | 그 기록을 버림 |
| `OTHER`가 아닌데 `direction`이 있음 | `direction`만 `null`로 버리고 기록은 씀 |
| 모르는 `direction` 값 | `direction`만 버리고 기록은 씀 |
| `issues`가 `null` | `[]`로 읽음 |
| `confidence` · `reliability` · `frames`가 없음 | 0으로 읽음 |
| 모르는 추가 필드 | 무시 |

버린 기록의 시간은 `normalize_samples`가 `UNMEASURED`로 채웁니다. 기록 하나 때문에 테이크 전체의 시선 정보를 잃지 않습니다.

### `issues`에 들어오는 촬영 조건 이슈

`web/src/engine/condition.ts`의 `ConditionIssue`입니다. 시선 이슈(5절)와 다른 것으로, "측정 환경이 나쁘다"는 뜻입니다. 코어는 이름을 해석하지 않고 `condition_issue_ms`에 시간만 합산합니다.

| 이름 | 뜻 |
|---|---|
| `TOO_FAR` · `TOO_CLOSE` | 보정 때보다 카메라에서 멀다 · 가깝다 (얼굴이 너무 작다 · 너무 크다 포함) |
| `OFF_CENTER` | 보정 때의 위치에서 옆으로 벗어남 |
| `LOW_VALID_RATIO` | 최근 프레임 중 얼굴을 잡은 비율이 낮음 |
| `NOISY_TRACKING` | 머리 방향이 심하게 떨림 (랜드마크가 흔들림) |
| `FACE_LOST` | 얼굴이 일정 시간 사라짐 |
| `FACE_REPLACED` | 얼굴 위치 · 크기가 갑자기 바뀐 채 이어져 다른 얼굴이 잡힌 것으로 보임 (누구인지 알아보는 얼굴 인식은 하지 않습니다) |
| `MOVED_TOO_FAR` | 보정 위치에서 너무 멀리 이동해 계속 머묾 |
| `HEAD_TURNED` | 머리가 돌아감. 눈을 보는 엔진에서만 나옵니다. 현재 머리 방향 엔진(`head_pose`)은 내지 않지만 타입에는 있으니 받는 쪽은 모르는 이름도 견디게 만드세요 |

`SECOND_FACE`(다른 얼굴이 보임)도 타입에는 있지만 신뢰도를 낮추지 않는 "알림"이라 `issues`로 가지 않습니다.

---

## 4. 서버 코어 진입점

`src/gaze/`의 함수들입니다. 모두 순수 함수이고 파일 · DB · 네트워크를 쓰지 않습니다. 호출 순서는 `parse_records` → `normalize_samples` → 나머지입니다.

| 진입점 | 언제 | 입력 | 출력 |
|---|---|---|---|
| `schemas.parse_records(raw)` | 요청 본문을 받을 때 | 1초 기록 목록(dict) | `(GazeSample 목록, 버린 기록 수)` |
| `core.normalize_samples(samples, start_ms=, end_ms=)` | 읽기 전에 매번 | `GazeSample` 목록 | `GazeTimeline` |
| `core.evaluate_gaze(timeline, t_ms, cfg)` | 실시간 코치: 지금 이슈가 있나 | 타임라인, 시각, `EvidenceConfig` | 이슈 dict 목록 (심한 순) |
| `core.take_summary(timeline, cfg)` | 리뷰: 테이크 종료 후 | 타임라인, `EvidenceConfig` | 요약 dict ([6-2](#6-2-테이크-요약-gazetakesummary)) |
| `core.compare_summaries(previous, current)` | 리뷰: 이전 테이크와 비교 | 요약 둘 | 비교 dict ([6-4](#6-4-이전-테이크-비교-gazesummarycomparison)) |
| `core.intervention_outcome(timeline, t_ms, issue_type, cfg)` | 코치: 피드백 뒤 효과 | 타임라인, 피드백 시각, 이슈 종류, `EvidenceConfig` | 효과 dict ([6-5](#6-5-피드백-효과-gazeinterventionoutcome)) |

`EvidenceConfig`는 `from gaze.config import EvidenceConfig`로 기본값을 쓰면 됩니다(7절).

### 4-1. 정리 `normalize_samples`

- 테이크(`start_ms` ~ `end_ms`) 밖의 기록은 버립니다. 경계에 걸친 기록은 둡니다.
- 시각 순으로 정렬하고, 앞 기록과 겹치는 기록은 버립니다. 같은 시각이면 먼저 받은 기록이 남습니다(재전송 등).
- 기록 사이 · `start_ms` 앞 · `end_ms` 뒤의 **빈 시간을 `UNMEASURED` 기록으로 채웁니다.** 빈 시간 하나를 길이와 상관없이 기록 하나로 채웁니다. 읽는 함수들이 시간 가중이라 1초씩 채운 것과 결과가 같고, 시각이 잘못된 기록(다른 단위의 시각 등)이 와도 계산량이 늘지 않습니다.
- `start_ms`를 안 주면 첫 기록의 시작이 시작점입니다. 기록이 하나도 없는데 `start_ms`와 `end_ms`를 주면 그 구간 전체가 `UNMEASURED`가 됩니다.

| 호출 | `start_ms` | `end_ms` |
|---|---|---|
| 실시간 코치 | 테이크 시작(0) | **지금 시각** |
| 리뷰 | 테이크 시작 | 테이크 끝 |

**공백 규칙.** 기록이 오지 않은 시간(카메라 끊김, 숨은 탭, 전송 유실)은 `UNMEASURED`입니다. 이렇게 하는 이유는 두 가지입니다.

- 마지막 상태가 계속 이어진 것처럼 읽혀 오래된 이슈(예: 아직 대본을 보는 중)가 나가는 것을 막습니다.
- 재지 못한 시간이 "청중을 안 봤다"로 읽히지 않게, 모든 비율을 **측정된 시간**(`CAMERA`/`SCREEN`/`BOTTOM`/`OTHER`)으로만 나눕니다.

공백이 길어지면 `evaluate_gaze`가 `GAZE_UNMEASURABLE`을 냅니다. 이 이슈는 `actionable: false`이고, 최근을 제대로 재지 못했으니 **시선 피드백을 보류하라**는 신호입니다([5절](#5-이슈-종류)). 공백이 없는 기록은 그대로 통과합니다.

### 4-2. 실시간 코치 `evaluate_gaze` 예시

1초 기록을 `CAMERA` 10초, `BOTTOM` 4초(0~14초) 보낸 뒤에 `evaluate_gaze`를 부릅니다. 첫 번째는 14초에 `end_ms=14000`으로 정리한 경우입니다.

```json
[
  {
    "evaluator": "gaze",
    "issue_type": "GAZE_ON_SCRIPT",
    "t_ms": 14000,
    "severity": 0.4,
    "confidence": 0.855,
    "persistence_sec": 4.0,
    "evidence": {
      "state": "BOTTOM",
      "run_start_ms": 10000,
      "continuous_ms": 4000,
      "bottom_ratio_5s": 0.8,
      "bottom_ratio_30s": 0.2857,
      "camera_ratio_30s": 0.7143,
      "mean_reliability": 0.95
    },
    "actionable": true
  }
]
```

두 번째는 그 뒤 카메라가 끊겨 기록이 오지 않은 채 30초가 된 경우입니다(`end_ms=30000`으로 정리, `t_ms=30000`). 마지막 5초가 전부 `UNMEASURED`라서 대본 이슈는 사라지고 `GAZE_UNMEASURABLE`만 나옵니다.

```json
[
  {
    "evaluator": "gaze",
    "issue_type": "GAZE_UNMEASURABLE",
    "t_ms": 30000,
    "severity": 1.0,
    "confidence": 1.0,
    "persistence_sec": 5.0,
    "evidence": {
      "coverage_5s": 0.0,
      "mean_reliability_5s": 0.0,
      "unmeasured_ms": 5000,
      "uncertain_ms": 0,
      "condition_issue_ms": {}
    },
    "actionable": false
  }
]
```

이슈가 없으면 빈 목록 `[]`입니다. 타임라인이 비었을 때도 `[]`입니다. 이슈는 `severity`가 큰 순서로 정렬됩니다.

### 4-3. 리뷰 `take_summary` 예시

짧은 테이크(`CAMERA` 5초, `BOTTOM` 4초, `CAMERA` 3초, `OTHER`(왼쪽) 3초, `UNMEASURED` 1초, 총 16초)입니다. `segments`는 5개 중 앞 1개와 마지막 1개, `problem_segments`는 2개 모두 남겼습니다.

```json
{
  "evaluator": "gaze",
  "start_ms": 0,
  "end_ms": 16000,
  "tracked_ms": 16000,
  "measured_ms": 15000,
  "uncertain_ms": 0,
  "unmeasured_ms": 1000,
  "coverage": 0.9375,
  "state_ms": {"CAMERA": 8000, "SCREEN": 0, "BOTTOM": 4000, "OTHER": 3000, "UNCERTAIN": 0, "UNMEASURED": 1000},
  "state_ratio": {"CAMERA": 0.5333, "SCREEN": 0.0, "BOTTOM": 0.2667, "OTHER": 0.2},
  "eye_contact_ratio": 0.5333,
  "other_direction_ms": {"RIGHT": 0, "UP_RIGHT": 0, "UP": 0, "UP_LEFT": 0, "LEFT": 3000, "DOWN_LEFT": 0, "DOWN": 0, "DOWN_RIGHT": 0},
  "mean_reliability": 0.8906,
  "condition_issue_ms": {},
  "longest_run_ms": {"CAMERA": 5000, "SCREEN": 0, "BOTTOM": 4000, "OTHER": 3000},
  "episodes": {"CAMERA": 2, "SCREEN": 0, "BOTTOM": 1, "OTHER": 1},
  "segments": [
    {"state": "CAMERA", "start_ms": 0, "end_ms": 5000, "duration_ms": 5000, "direction": null, "mean_confidence": 0.9, "mean_reliability": 0.95},
    {"state": "UNMEASURED", "start_ms": 15000, "end_ms": 16000, "duration_ms": 1000, "direction": null, "mean_confidence": 0.0, "mean_reliability": 0.0}
  ],
  "problem_segments": [
    {"state": "BOTTOM", "start_ms": 5000, "end_ms": 9000, "duration_ms": 4000, "direction": null, "mean_confidence": 0.9, "mean_reliability": 0.95, "issue_type": "GAZE_ON_SCRIPT"},
    {"state": "OTHER", "start_ms": 12000, "end_ms": 15000, "duration_ms": 3000, "direction": "LEFT", "mean_confidence": 0.9, "mean_reliability": 0.95, "issue_type": "GAZE_AWAY"}
  ]
}
```

기록이 하나도 없고 테이크 시작 · 끝도 주지 않은 타임라인은 `evaluator` · `tracked_ms` · `measured_ms`와 빈 목록 둘만 옵니다([6-2](#6-2-테이크-요약-gazetakesummary)).

```json
{"evaluator": "gaze", "tracked_ms": 0, "measured_ms": 0, "segments": [], "problem_segments": []}
```

### 4-4. 비교 `compare_summaries` 예시

앞의 테이크(이전)와, `CAMERA` 9초 · `BOTTOM` 3초 · `CAMERA` 4초 테이크(현재)를 비교합니다. `delta = current - previous`입니다. `state_ratio`는 `CAMERA`와 `OTHER`만, `episodes` · `longest_run_ms`는 `CAMERA`만 남겼습니다.

```json
{
  "eye_contact_ratio": {"previous": 0.5333, "current": 0.8125, "delta": 0.2792},
  "coverage": {"previous": 0.9375, "current": 1.0, "delta": 0.0625},
  "mean_reliability": {"previous": 0.8906, "current": 0.95, "delta": 0.0594},
  "state_ratio": {
    "CAMERA": {"previous": 0.5333, "current": 0.8125, "delta": 0.2792},
    "OTHER": {"previous": 0.2, "current": 0.0, "delta": -0.2}
  },
  "episodes": {
    "CAMERA": {"previous": 2, "current": 2, "delta": 0.0}
  },
  "longest_run_ms": {
    "CAMERA": {"previous": 5000, "current": 9000, "delta": 4000.0}
  }
}
```

실제 출력에는 `state_ratio` · `episodes` · `longest_run_ms` 모두 4상태(`CAMERA` `SCREEN` `BOTTOM` `OTHER`)가 다 들어 있습니다.

### 4-5. 피드백 효과 `intervention_outcome` 예시

`BOTTOM` 10초 뒤 `CAMERA` 10초가 이어졌고, 10초에 "대본을 보고 있다"(`GAZE_ON_SCRIPT`) 피드백을 한 경우입니다. 피드백 전 5초(5~10초)와 피드백 5초 뒤부터의 5초(15~20초)의 `BOTTOM` 비율을 비교합니다.

```json
{
  "intervention": {"type": "LOOK_AT_CAMERA", "issue_type": "GAZE_ON_SCRIPT", "t_ms": 10000},
  "before": {"bottom_ratio_5s": 1.0, "measured_ms": 5000},
  "after_5s": {"bottom_ratio_5s": 0.0, "measured_ms": 5000},
  "effective": true
}
```

`effective`는 `true`(대상 비율이 기준 0.2 이상 좋아짐) · `false`(그만큼 좋아지지 않음, 변화 없음 포함) · `null`(어느 창이든 측정된 시간이 0)입니다. 뒤 창이 다 차기 전에 부르면 그때까지의 데이터로 판정하므로, **피드백 시각 + `outcome_delay_ms` + `outcome_after_ms`(기본 10초) 뒤에 부릅니다.** `issue_type`이 `GAZE_UNMEASURABLE`처럼 규칙이 없는 값이면 `ValueError`를 던집니다(`GAZE_ON_SCRIPT` · `GAZE_ON_SCREEN` · `GAZE_AWAY` · `GAZE_LOW_EYE_CONTACT`만 가능).

---

## 5. 이슈 종류

`evaluate_gaze`가 만드는 이슈입니다(`src/gaze/core.py`). 공통 필드는 `evaluator`(`"gaze"`) · `issue_type` · `t_ms` · `severity` · `confidence` · `persistence_sec` · `evidence` · `actionable`입니다. 시각은 `t_ms`(ms), 지속 시간은 `persistence_sec`(초)입니다.

임계값 기본값은 7절입니다. **이슈 판단은 "가장 최근에 이어진 같은 상태 구간"으로 합니다.** 구간이 길어지면 이슈가 나고, 상태가 바뀌면 이슈가 사라집니다.

| `issue_type` | 조건 | severity | confidence | persistence_sec | actionable |
|---|---|---|---|---|---|
| `GAZE_ON_SCRIPT` | 지금 이어진 `BOTTOM` 구간이 3초 이상 | 구간 길이 ÷ 10초 (최대 1) | 구간의 평균 `confidence` × 평균 `reliability` | 구간 길이 | `true` |
| `GAZE_ON_SCREEN` | 지금 이어진 `SCREEN` 구간이 5초 이상 | 구간 길이 ÷ 15초 (최대 1) | 위와 같음 | 구간 길이 | `true` |
| `GAZE_AWAY` | 지금 이어진 `OTHER` 구간이 2초 이상 (방향이 바뀌어도 한 구간) | 구간 길이 ÷ 8초 (최대 1) | 위와 같음 | 구간 길이 | `true` |
| `GAZE_LOW_EYE_CONTACT` | 최근 30초에 측정된 시간이 15초 이상이고 `CAMERA` 비율이 0.30 미만 | (0.30 − 카메라 비율) ÷ 0.30 | 30초 창의 평균 `confidence` × 평균 `reliability` | 30초 창의 측정된 시간 | `true` |
| `GAZE_UNMEASURABLE` | 최근 5초에서 측정된 비율(coverage)이 0.5 미만이거나 평균 `reliability`가 0.5 미만. `UNCERTAIN` · `UNMEASURED` 1초는 측정되지 않은 시간입니다 | 1 − min(coverage÷0.5, reliability÷0.5) | 1.0 | 5초 창에서 측정 못 한 시간 | **`false`** |

- 앞 셋 중 최대 하나(지금의 구간)와 `GAZE_LOW_EYE_CONTACT` · `GAZE_UNMEASURABLE`이 동시에 나올 수 있습니다.
- `severity` · `confidence`는 0~1이고 소수 4자리입니다.
- `GAZE_UNMEASURABLE`은 "시선 피드백 보류" 신호입니다. 최근 5초를 제대로 재지 못했으니, 이 신호가 있는 동안에는 시선 피드백을 보류하는 것을 권장합니다. 코어는 다른 이슈를 걸러 내지 않습니다(같이 나온 이슈는 `actionable: true` 그대로). 어떻게 할지는 코치가 정하고, 발표는 그대로 이어집니다.
- 코치가 피드백할 때의 행동 이름은 앞 네 이슈 모두 `LOOK_AT_CAMERA`입니다. 실제로 끼어들지는 코치가 정합니다. 코어는 측정까지만 합니다.

**`evidence` 키** (창 길이가 이름에 들어갑니다)

| 이슈 | 키 |
|---|---|
| `GAZE_ON_SCRIPT` | `state`(`"BOTTOM"`), `run_start_ms`, `continuous_ms`, `bottom_ratio_5s`, `bottom_ratio_30s`, `camera_ratio_30s`, `mean_reliability` |
| `GAZE_ON_SCREEN` | 위와 같되 `screen_ratio_5s` · `screen_ratio_30s`, `state`는 `"SCREEN"` |
| `GAZE_AWAY` | 위와 같되 `other_ratio_5s` · `other_ratio_30s`, `state`는 `"OTHER"`, 그리고 `direction`(구간에서 가장 오래 본 방향) 추가 |
| `GAZE_LOW_EYE_CONTACT` | `camera_ratio_30s`, `screen_ratio_30s`, `bottom_ratio_30s`, `other_ratio_30s`, `measured_ms` |
| `GAZE_UNMEASURABLE` | `coverage_5s`, `mean_reliability_5s`, `unmeasured_ms`, `uncertain_ms`, `condition_issue_ms` (촬영 조건 이슈별 ms) |

`5s` · `30s`는 `short_window_ms` · `long_window_ms`에서 옵니다. **창 길이를 바꾸면 키 이름도 바뀝니다**(예: 10초 창이면 `bottom_ratio_10s`). 키 이름을 코드에 박지 말고, 받는 쪽은 `evidence`를 키 집합이 고정되지 않은 dict로 다루세요. 측정된 시간이 0이면 비율은 `null`입니다.

`GAZE_AWAY` 예시(왼쪽을 3초 본 경우, `t_ms=3000`):

```json
{
  "evaluator": "gaze",
  "issue_type": "GAZE_AWAY",
  "t_ms": 3000,
  "severity": 0.375,
  "confidence": 0.855,
  "persistence_sec": 3.0,
  "evidence": {
    "state": "OTHER",
    "run_start_ms": 0,
    "continuous_ms": 3000,
    "other_ratio_5s": 1.0,
    "other_ratio_30s": 1.0,
    "camera_ratio_30s": 0.0,
    "mean_reliability": 0.95,
    "direction": "LEFT"
  },
  "actionable": true
}
```

---

## 6. 출력 데이터 모델

코어는 dict를 그대로 돌려줍니다. 아래 모델(`src/gaze/schemas.py`)은 API 응답과 계약 테스트(`tests/unit/test_schemas.py`)에 쓰고, **키 집합이 고정**입니다(코어가 키를 더하거나 빼면 계약 테스트가 깨집니다). `Ratio`는 0~1의 소수 4자리 float입니다.

### 6-1. 이슈 `GazeIssue`

| 필드 | 타입 | 뜻 |
|---|---|---|
| `evaluator` | `"gaze"` | 에이전트 공통 평가기 이름 |
| `issue_type` | `IssueType` | 5절의 5가지 중 하나 |
| `t_ms` | int | 이슈를 읽은 시각 |
| `severity` | Ratio | 0~1, 클수록 심함 |
| `confidence` | Ratio | 0~1 |
| `persistence_sec` | float | 지속 시간(초), 0 이상 |
| `evidence` | dict | 이슈마다 키가 다름 (5절) |
| `actionable` | bool | `false`면 이 이슈로는 피드백하지 않음 (`GAZE_UNMEASURABLE`만 `false`) |

### 6-2. 테이크 요약 `GazeTakeSummary`

| 필드 | 타입 | 뜻 |
|---|---|---|
| `evaluator` | `"gaze"` | |
| `tracked_ms` | int | 기록된 시간(`UNMEASURED` 포함) |
| `measured_ms` | int | 측정된 시간(`CAMERA`+`SCREEN`+`BOTTOM`+`OTHER`) |
| `segments` | `GazeSegment` 목록 | 1초 이상 이어진 구간 |
| `problem_segments` | `GazeProblemSegment` 목록 | 이슈 조건을 넘긴 구간 |
| `start_ms` · `end_ms` | int \| 없음 | 타임라인의 시작 · 끝 (`normalize_samples`에 준 테이크 시작 · 끝) |
| `uncertain_ms` · `unmeasured_ms` | int \| 없음 | `UNCERTAIN` · `UNMEASURED` 시간 |
| `coverage` | Ratio \| 없음 | 측정된 시간 ÷ 기록된 시간 |
| `state_ms` | 6상태 → int | 상태별 시간. 키: `CAMERA` `SCREEN` `BOTTOM` `OTHER` `UNCERTAIN` `UNMEASURED` |
| `state_ratio` | 4상태 → Ratio \| null | 측정된 시간 대비 비율. 측정된 시간이 0이면 각 값이 `null` |
| `eye_contact_ratio` | Ratio \| 없음 | `state_ratio.CAMERA`와 같은 값 |
| `other_direction_ms` | 8방향 → int | `OTHER`를 본 방향별 시간 |
| `mean_reliability` | Ratio \| 없음 | 기록 시간 가중 평균 신뢰도 |
| `condition_issue_ms` | dict[str, int] \| 없음 | 촬영 조건 이슈별 시간(ms) |
| `longest_run_ms` | 4상태 → int | 상태별 가장 긴 이어짐 |
| `episodes` | 4상태 → int | 상태별 1초 이상 구간 수 |

**빈 테이크**(기록 0개)에는 `evaluator` · `tracked_ms`(0) · `measured_ms`(0) · 빈 `segments` · 빈 `problem_segments`만 옵니다. 표에서 "없음"이라고 쓴 필드는 이때 키 자체가 없습니다. 받는 쪽은 이 필드들을 없을 수 있는 값으로 읽어야 합니다. `normalize_samples`에 `start_ms` · `end_ms`를 주면 기록이 없어도 전체가 `UNMEASURED`로 채워지므로 빈 모양이 아니라 `coverage: 0.0`인 요약이 됩니다.

### 6-3. 구간 `GazeSegment` · `GazeProblemSegment`

같은 상태가 이어진 구간입니다(`OTHER`는 방향이 달라도 한 구간). `GazeProblemSegment`는 `GazeSegment`에 `issue_type`이 하나 더 붙은 것입니다.

| 필드 | 타입 | 뜻 |
|---|---|---|
| `state` | `SampleState` | 구간의 상태 |
| `start_ms` · `end_ms` · `duration_ms` | int | 구간 시작 · 끝 · 길이 |
| `direction` | 방향 \| null | `OTHER` 구간에서 가장 오래 본 방향 |
| `mean_confidence` | Ratio | 구간의 길이 가중 평균 |
| `mean_reliability` | Ratio | 구간의 길이 가중 평균 |
| `issue_type` | `IssueType` | (`GazeProblemSegment`만) `GAZE_ON_SCRIPT`(3초 이상) · `GAZE_ON_SCREEN`(5초 이상) · `GAZE_AWAY`(2초 이상) |

`segments`에는 길이 1초 이상인 구간만 들어가고, `UNCERTAIN` · `UNMEASURED` 구간도 포함됩니다(위 예시 참고). `problem_segments`에는 `BOTTOM` · `SCREEN` · `OTHER`만 나옵니다.

### 6-4. 이전 테이크 비교 `GazeSummaryComparison`

| 필드 | 타입 | 뜻 |
|---|---|---|
| `eye_contact_ratio` | `Delta` | 카메라 응시 비율 |
| `coverage` | `Delta` | 측정된 비율 |
| `mean_reliability` | `Delta` | 평균 신뢰도 |
| `state_ratio` | 4상태 → `Delta` | 상태 비율 |
| `episodes` | 4상태 → `Delta` | 구간 수 |
| `longest_run_ms` | 4상태 → `Delta` | 가장 긴 이어짐(ms) |

`Delta`는 `{ "previous": 숫자|null, "current": 숫자|null, "delta": 숫자|null }`이고 `delta = current − previous`입니다. 둘 중 하나라도 `null`이면 `delta`도 `null`입니다. 정수 항목(`episodes` · `longest_run_ms`)의 `delta`도 float(`4000.0`)로 나옵니다.

### 6-5. 피드백 효과 `GazeInterventionOutcome`

| 필드 | 타입 | 뜻 |
|---|---|---|
| `intervention` | `{ type, issue_type, t_ms }` | 피드백. `type`은 지금 모두 `LOOK_AT_CAMERA` |
| `before` | `{ <상태>_ratio_5s, measured_ms }` | 피드백 전 5초의 대상 비율과 측정된 시간 |
| `after_<지연초>s` | `{ <상태>_ratio_5s, measured_ms }` | 피드백 지연(기본 5초) 뒤부터 5초 동안. 키 이름에 지연 초가 들어갑니다(기본 `after_5s`). **이 키가 정확히 하나** 있어야 합니다 |
| `effective` | bool \| null | 비율이 올바른 방향으로 0.2 이상 움직였는가. 측정 없으면 `null` |

대상 상태와 방향은 이슈마다 다릅니다.

| `issue_type` | 대상 비율 | 좋아진 방향 |
|---|---|---|
| `GAZE_ON_SCRIPT` | `BOTTOM` | 감소 |
| `GAZE_ON_SCREEN` | `SCREEN` | 감소 |
| `GAZE_AWAY` | `OTHER` | 감소 |
| `GAZE_LOW_EYE_CONTACT` | `CAMERA` | 증가 |

비율 키 이름도 대상 상태에 따라 `bottom_ratio_5s` · `screen_ratio_5s` · `other_ratio_5s` · `camera_ratio_5s`로 달라집니다. 창 길이를 바꾸면 키 이름도 바뀝니다.

---

## 7. 임계값

`src/gaze/config.py`의 `EvidenceConfig` 기본값입니다. 서버는 파일을 읽지 않고 이 기본값을 그대로 씁니다. 원본은 `configs/evidence.yaml`(연구용)이고, 두 값이 같은지는 `tests/lab/test_core_sync.py`가 검사합니다. **바꿀 때는 YAML과 이 파일을 같이 고칩니다.** 모든 값은 측정으로 정한 것이 아니라 초기값입니다.

| 필드 | 기본값 | 뜻 |
|---|---|---|
| `slice_ms` | 1000 | 1초 기록의 길이(ms). 엔진(브라우저)이 자르는 단위 (엔진 쪽 판정) |
| `min_frames_per_slice` | 4 | 이보다 얼굴 있는 프레임이 적은 1초는 `UNMEASURED` (엔진 쪽 판정) |
| `slice_vote_threshold` | 0.6 | 이긴 상태의 득표율이 이보다 낮으면 `UNCERTAIN` (엔진 쪽 판정) |
| `short_window_ms` | 5000 | 실시간 통계의 짧은 창. 이슈 `evidence`의 `*_5s` |
| `long_window_ms` | 30000 | 실시간 통계의 긴 창. `*_30s` |
| `script_min_ms` · `script_full_ms` | 3000 · 10000 | `BOTTOM`이 이만큼 이어지면 이슈 · 이만큼이면 severity 1.0 |
| `screen_min_ms` · `screen_full_ms` | 5000 · 15000 | `SCREEN`의 같은 기준 |
| `away_min_ms` · `away_full_ms` | 2000 · 8000 | `OTHER`의 같은 기준 (가장 엄격) |
| `low_eye_contact_ratio` | 0.30 | 긴 창에서 `CAMERA` 비율이 이 미만이면 `GAZE_LOW_EYE_CONTACT` |
| `low_eye_contact_min_measured_ms` | 15000 | 긴 창에서 측정된 시간이 이 이상일 때만 위 이슈 |
| `unmeasurable_coverage` | 0.5 | 짧은 창의 측정 비율이 이 미만이면 `GAZE_UNMEASURABLE` |
| `unmeasurable_reliability` | 0.5 | 짧은 창의 평균 신뢰도가 이 미만이면 `GAZE_UNMEASURABLE` |
| `segment_min_ms` | 1000 | 이보다 짧은 구간은 `segments` · `episodes`에 안 넣음 |
| `outcome_before_ms` | 5000 | 피드백 전 비교 창 |
| `outcome_delay_ms` | 5000 | 피드백 뒤 기다리는 시간. `after_5s` 키의 5 |
| `outcome_after_ms` | 5000 | 피드백 뒤 비교 창 |
| `outcome_min_change` | 0.2 | 비율이 이만큼 좋아져야 `effective: true` |

---

## 8. 버전과 호환

| 이름 | 값 | 어디서 | 뜻 |
|---|---|---|---|
| `FEATURE_VERSION` | `"1.1"` | `src/gaze/version.py` | 서버 코어 출력의 의미 버전. 출력의 **의미**가 바뀔 때만 올립니다 |
| 엔진 `version` | `gaze_v1.1.0` + `head_pose` + `reference_anchor_v1` | `web/src/engine/defaults.generated.ts`의 `version` → `GazeEngine.version` | 엔진이 돌려주는 값은 `{ modelVersion, gazeBackbone, gazeClassifier }` 객체입니다. camera 화면 worker의 `ready.version`은 이를 `+`로 이은 문자열 `gaze_v1.1.0+head_pose+reference_anchor_v1`입니다 |
| `CONFIG_HASH` | `14ed457ab13b` | `web/src/engine/defaults.generated.ts` (`web/src/engine/config.ts`가 내보냄) | 엔진 설정(판정 파라미터)의 해시. 설정이 달라진 엔진과 보정 모델은 호환되지 않습니다 |

`FEATURE_VERSION` `1.1`은 엔진 `gaze_v1.1.0`과 짝입니다(상태 `CAMERA`/`SCREEN`/`BOTTOM`/`OTHER` + `UNCERTAIN` · `UNMEASURED`, `OTHER` 방향 8개). 엔진 설정 · 버전 파일(`defaults.generated.ts`)은 파이썬 설정에서 만든 생성 파일이고, 오래되면 `tests/lab/test_web_engine_sources.py`가 실패합니다.

**BE는 1초 기록을 저장할 때 엔진 버전 문자열(`ready.version` 또는 `modelVersion+gazeBackbone+gazeClassifier`)도 같이 저장하세요.** 엔진이 바뀌면 같은 시선이라도 다른 상태로 판정될 수 있어서, 예전 기록을 다시 읽거나 테이크끼리 비교할 때 어느 엔진의 기록인지 알아야 합니다. 비교는 같은 엔진 버전끼리만 하는 것이 안전합니다.

### 바꿀 때의 규칙

| 변경 | 호환 | 해야 할 일 |
|---|---|---|
| 1초 기록 · 출력에 **선택 필드 추가** | 안전. 받는 쪽은 모르는 필드를 무시합니다(`extra="ignore"`) | 이 문서의 표를 갱신 |
| `state` · `direction` · `issue_type` · 촬영 조건 이슈 이름 **값 추가** | **깨질 수 있음.** 값으로 분기(switch)하는 쪽이 새 값을 처리하지 못합니다. 모르는 `state`를 보내면 서버가 그 기록을 버리고, 모르는 `direction`은 방향만 버립니다 | 받는 쪽(BE · 코치 · 리뷰 에이전트)에 먼저 알리고, 서버 모델(`SampleState` · `Direction` · `IssueType`)을 같이 고침. 출력에 새 이슈를 내면 `FEATURE_VERSION`도 올림 |
| 필드의 **뜻 변경** (임계값 변경으로 이슈가 나는 기준이 달라지는 경우 포함) | 호환 안 됨 | `FEATURE_VERSION` 올림, 같은 엔진 버전의 기록끼리만 비교 |
| 필수 필드 추가 · 이름 변경 · 삭제 | 호환 안 됨 | `FEATURE_VERSION` 올림, FE · BE · AI 함께 배포 |
| 창 길이 변경 | `evidence` · 효과의 키 이름이 바뀜 | 키 이름을 코드에 박은 곳이 없는지 확인 |
| 엔진 설정 변경 | `CONFIG_HASH`가 바뀜 | 엔진 `version`을 확인하고 보정 모델은 새로 만듦 |

문서와 코드를 같이 고치세요. 출력 모델(`schemas.py`)은 `tests/unit/test_schemas.py`가, 1초 기록 규칙은 `tests/unit/test_records.py`가 검사합니다.
