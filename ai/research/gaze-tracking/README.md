# gaze-tracking (시선)

웹캠으로 발표자가 **카메라(청중) · 화면 · 대본 · 그 밖** 중 어디를 보는지 판정하고,
그 기록으로 코치가 읽을 **1초 판정 결과(시선 문제 · 지표)**와 리뷰가 읽을 **Take 시선 지표**를 만드는 기능입니다.

판정은 **사용자 브라우저 안에서(온디바이스)** 돕니다. 카메라 영상 · 얼굴 랜드마크 · 얼굴 측정값은 기기를 떠나지 않고,
서버에는 발표 중 **1초 기록**(1초마다 상태 하나, 필드 8개)과 발표 전 보정 결과 요약만 갑니다.

| | |
|---|---|
| 담당 | jewon-kim |
| 기능 버전 | 서버 판정 모듈 `gaze-1.3` (`features/gaze/version.py`), 브라우저 엔진 `gaze_v1.1.0+head_pose+reference_anchor_v1` |
| 판정 신호 | 고개 방향 (MediaPipe Face Landmarker의 변환 행렬). 눈 기반 백본은 비교 실험용으로 보관 |
| 상태 | 테스트 영상 · 합성 얼굴로만 검증했습니다. **실제 발표자 녹화로 정확도를 잰 적은 아직 없습니다** |
| 설계 | 엔진 계약 · 1초 기록 · 서버 판정 · 배포 → [`docs/ai/features/gaze-tracking.md`](../../../docs/ai/features/gaze-tracking.md) |

> `ai/archive/workspaces/jewon-kim/gaze-tracking/v1/local/`을 구조를 바꿔 옮긴 프로젝트입니다.
> 브라우저 엔진 · 기준 구현의 판정 로직은 옮길 때와 같고 같은 입력이면 archive와 결과가 같습니다 ([옮긴 뒤 검증](#옮긴-뒤-검증)).
> 서버 판정 모듈은 AI 서비스(`ai/service/src/pitch_coach_ai/features/gaze/`)로 옮겼습니다([서비스로 옮긴 결과](#서비스로-옮긴-결과)). 아래 `features/gaze/`는 그 위치입니다.
> 서버 판정 모듈은 기능 문서에 맞춰 다시 만들었습니다([4-4](#4-4-코치와-이어지는-곳--연구용-대역과-달라진-판정)).
> 알고리즘을 어떻게 유도했는지(좌표계 · 보정 · 조건 감시 등)는 archive의 `v1/README.md`에 자세히 있습니다.

## 목차

1. [이 기능이 하는 일](#1-이-기능이-하는-일)
2. [코드 구조 한눈에 보기](#2-코드-구조-한눈에-보기)
3. [폴더와 파일](#3-폴더와-파일)
4. [실행 흐름 따라가기](#4-실행-흐름-따라가기)
5. [실행법](#5-실행법)
6. [테스트와 검증](#6-테스트와-검증)
7. [얼굴 데이터와 예외 처리](#7-얼굴-데이터와-예외-처리)
8. [설계 결정](#8-설계-결정)
9. [알려진 한계](#9-알려진-한계)
10. [코드 읽는 순서](#10-코드-읽는-순서)
11. [원본](#11-원본)

---

## 1. 이 기능이 하는 일

| 단계 | 하는 일 | 어디서 |
|---|---|---|
| 준비 점검 | 한 명인지, 화면 안에 있는지, 인식할 수 있는 크기 · 밝기인지 | 브라우저 |
| 고개 원 확인 | 고개를 천천히 돌려 원을 채우며, 어느 방향에서도 얼굴을 따라가는지 확인 (막지 않음) | 브라우저 |
| 보정 | 편한 자세로 화면 중앙 → 렌즈 → 대본을 차례로 봄. 사용자마다 기준점 3개 | 브라우저 |
| 실시간 판정 | 프레임마다 고개 방향을 기준점과 비교해 CAMERA / SCREEN / BOTTOM / OTHER(+ 8방향) / UNCERTAIN | 브라우저 (초당 8프레임) |
| 1초 기록 | 1초 동안의 프레임을 다수결로 모아 상태 하나. 재지 못한 1초는 UNMEASURED | 브라우저 → 서버 |
| 1초 판정 | 최근 30초 기록과 발화 시간으로 상태 · 응시 비율 · 지금 이어지는 응시 · 대본 의존을 재고 다섯 시선 문제를 판정 (`judge`) | 서버 (코치가 요청마다 부름) |
| Take 지표 | 장 · Take 동안 센 몫으로 청중 · 화면 · 대본 · 그 밖 비율, 대본 의존, 측정 비율 (`summarize`), 다섯 문제의 판정 기준 (`criteria`) | 서버 (코치가 장 끝 · Take 끝에 부름) |

응시 비율은 **측정된 시간**(판정된 4개 상태)으로 나눕니다. 재지 못한 시간이 "청중을 안 봤다"로 읽히지 않게 하기 위해서입니다.
대본 의존 비율(`reading_ratio`)은 말한 시간으로, 측정 비율(`measured_ratio`)은 전체 시간으로 나눕니다.
측정할 수 없으면 `GAZE_UNMEASURABLE`(시선 피드백 보류 신호)을 냅니다. 재지 못한 시간에 틀린 시선 피드백을 하지 않기 위해서이고, 발표는 그대로 이어집니다.

---

## 2. 코드 구조 한눈에 보기

코드는 **실행되는 곳**에 따라 세 덩어리입니다. 나중에 옮길 때 폴더째 복사하면 되도록 나눴습니다.

| 덩어리 | 폴더 | 하는 일 | 나중에 |
|---|---|---|---|
| **TS 엔진** | `web/src/engine/` | 프레임 → 준비 점검 · 고개 원 · 보정 · 프레임 판정 · 1초 기록 (Web Worker 안) | `frontend/src/vendor/gaze/engine/`로 폴더째 (카메라 화면 · Worker와 함께) |
| **서버 판정 모듈** | 서비스 `features/gaze/` | 1초 기록 → 1초 판정 결과(`judge`) · Take 지표(`summarize`) · 판정 기준(`criteria`) | `ai/service`로 옮겼음 |
| **Python 기준 구현** | `src/gaze_lab/` | 같은 판정의 Python 판 + 데이터셋 · 평가 · 실험. TS 엔진의 임계값 · 기준 답을 만든다 | research에 남음 |

```
tools/ · experiments/  ──import──▶  src/gaze_lab/  ──import──▶  pitch_coach_ai.features.gaze (서비스, 반대 방향 금지)
web/src/{demo,camera,worker}  ──import──▶  web/src/engine/                         (엔진은 밖을 import 하지 않음)
tools/export_config.py · make_fixtures.py  ──생성──▶  web/src/engine/defaults.generated.ts · __tests__/fixtures/
```

### 지키는 규칙

1. **얼굴에서 나온 값은 기기 메모리에만.** 프레임 · 랜드마크 · 얼굴 측정값 · 보정 모델은 저장 · 전송 · 로그하지 않습니다.
   기기 밖으로 나가는 것은 발표 중 1초 기록과 발표 전 보정 결과 요약 두 가지뿐입니다 ([7장](#7-얼굴-데이터와-예외-처리)). 테스트로 막습니다.
2. **막지 않는다.** 시선은 정보를 얻으려는 기능이지 발표를 통제하는 기능이 아닙니다. 조건이 나쁘면 "측정 못 함"으로 남기고,
   발표는 어떤 경우에도 멈추지 않습니다. 이상한 1초 기록은 그 1초만 버립니다.
3. **임계값 원본은 하나다.** 엔진 임계값은 `configs/*.yaml`이 원본이고 TS는 생성된 `defaults.generated.ts`만 읽습니다.
   서버 판정 기준값은 `features/gaze/config.py`에만 두고 YAML에는 두지 않습니다(테스트로 막음). 테스트 사진 하나에 맞춰 임계값을 바꾸지 않습니다(과적합).
4. **경계는 테스트로.** 서버 판정 모듈은 상대 import와 허용한 라이브러리(pydantic 등)만 쓰고 파일 · 네트워크 · 환경변수를 쓰지 않습니다.
   TS 엔진은 자기 폴더와 MediaPipe만 import하고, DOM 없이(Worker 전역만) 컴파일됩니다.

---

## 3. 폴더와 파일

### 3-1. 전체 트리

```
gaze-tracking/
├── README.md
├── pyproject.toml · uv.lock · .python-version   Python 3.12, mediapipe==1.0.1 고정 (uv)
├── .gitignore · .gitattributes                  artifacts · data · outputs · fixtures/local 제외, LF
├── run_demo.bat                                 Windows 더블클릭으로 Python 데모
├── configs/                                     임계값 · 설정 원본 YAML 11개
├── artifacts/                                   모델 파일 자리 (git 밖). manifest(*.json)만 커밋
├── outputs/                                     실행 결과 (git 밖)
├── src/
│   └── gaze_lab/                                Python 기준 구현 · 평가 (서버 판정 모듈은 서비스 features/gaze/)
├── experiments/                                 비교 실험 2개 (녹화 데이터 필요)
├── tools/                                       데모 · 자산 받기 · 생성기 · 유도 스크립트
├── tests/
│   ├── lab/                                     기준 구현 · 평가 · 도구 · 생성 파일 최신 검사
│   └── fixtures/                                테스트 얼굴의 출처 · 해시 (사진은 local/, git 밖)
└── web/                                         브라우저: TS 엔진 · Worker · 카메라 화면 · 데모 (Node 24)
```

### 3-2. 서버 판정 모듈 (서비스 `features/gaze/`)

| 파일 | 하는 일 |
|---|---|
| `core.py` | `judge`(1초 기록 읽기 → 타임라인 → 새 몫 `tally` · 지표 · 상태 · `measurable` · 다섯 문제), `summarize`(장 · Take 지표), `criteria`(문제마다 지표 · 방향 · 기준 · 늦게 잡히는 폭), `criteria_version` |
| `schemas.py` | 입출력 계약(pydantic): 입력 `GazeInput`(1초 기록 `GazeRecord` · 발화 시간 · 커서), 출력 `JudgmentResult` · `GazeSummary` · `IssueCriteria`(공통 결과 모양을 복사해 둠) |
| `config.py` | `GazeConfig`: 판정 기준값(창 · 이어짐 기준 · 비율 · 측정 불가 · 시작 직후 억제 · 말한 1초 · 대본 의존 구간). 값의 해시가 `criteria_version`에 들어간다 |
| `version.py` | 기능 버전 `gaze-1.3`. 출력의 의미가 바뀌면 올린다 |
| `examples/` | `judge` 입력 · 출력 8쌍(판정 창 30초 전체), `summarize` 3쌍, `criteria.json`. `tools/make_examples.py`가 출력을 만든다 |

### 3-3. Python 기준 구현 `src/gaze_lab/`

| 폴더 · 파일 | 하는 일 | TS 짝 |
|---|---|---|
| `schemas.py` · `config.py` | 프레임 · 보정 · 이벤트 모양, `configs/` → `VisionConfig`, 경로 · 설정 해시 | `types.ts` · `contract.ts` · `config.ts` |
| `preprocess/` | 프레임 → MediaPipe 랜드마크 → 고개 방향 · 크롭 · 품질 | `landmarker.ts` · `headpose.ts` · `observe.ts` |
| `backbones/` · `eye/` | 고개 방향 백본(기본), 눈 기반 백본 3종(비교 실험용) | `engine.ts` 안 |
| `calibration/` | 3점 보정: 기준점 분류기 · 게이지 · 품질 (LR은 비교용) | `reference.ts` · `gauge.ts` |
| `runtime/` | `VisionSession`(전체 흐름) · 준비 점검 · 고개 원 · 조건 감시 · 카메라 배치 · 버전 | `engine.ts` · `preconditions.ts` · `sweep.ts` · `condition.ts` · `placement.ts` |
| `temporal/` | `GAZE_STATE` 이벤트 평활 | 없음 (1초 다수결은 FE) |
| `evidence/gaze.py` | 프레임 판정 → 1초 기록(`GazeSample` · `GazeSlicer`), 세션 기록기(1초 기록을 서버 모듈 `judge` · `summarize`로 읽음) | `evidence.ts` |
| `data/` · `evaluation/` | 라벨 · 분할 · 특징 테이블, 지표 · 릴리스 게이트 · 평가 CLI · 임계값 스윕 · 실험 로그 | 없음 |

### 3-4. `tools/` · `experiments/`

| 파일 | 하는 일 |
|---|---|
| `tools/fetch_assets.py` | 모델과 테스트 얼굴을 받아 해시를 확인한다 (처음 한 번) |
| `tools/make_static_video.py` | 테스트 얼굴 원본 → 웹캠 구도의 사진 · 정지 영상 |
| `tools/pipeline_demo.py` | 웹캠(또는 영상)으로 준비 점검 → 고개 원 → 보정 → 실시간 판정을 보는 Python 데모 |
| `tools/export_config.py` · `make_fixtures.py` | `configs/` → TS 임계값, Python 실행 결과 → TS parity 기준 답 |
| `tools/make_examples.py` | 서버 판정 모듈의 `examples/` 출력을 지금 코드 · 기준값으로 다시 만든다 |
| `tools/derive_headpose_model.py` | 얼굴 사진 한 장 → PnP 모델 점 9개 (고개 방향 대체 경로의 상수를 만든 방법) |
| `tools/download_checkpoints.py` | 눈 기반 백본 가중치 (비교 실험용) |
| `tools/make_y4m.py` | 웹 smoke용 가짜 카메라 영상 |
| `experiments/exp1_backbone.py` · `exp2_calibration_ablation.py` | 백본 비교 · 보정 특징 절제 (녹화한 특징 테이블 필요) |

### 3-5. 브라우저 `web/`

| 폴더 | 하는 일 |
|---|---|
| `src/engine/` | **TS 엔진** — frontend로 갈 폴더. `index.ts`(진입점 · 지원 확인 · 초기화 실패 사유, MediaPipe를 불러옴) · `contract.ts`(MediaPipe 없이 쓰는 FE 진입점) 외에는 엔진 안에서만 쓴다. `__tests__/`에 엔진 테스트와 Python 기준 답 |
| `src/worker/` | 카메라 화면용 Worker (메시지 처리) |
| `src/camera/` | 카메라 화면 모듈: 영상 · 얼굴 원 · 보정 안내 (FE가 가져가 고칠 UI, 지금은 그대로) |
| `src/demo/` | 로컬 데모 페이지 (FE 페이지 대역). 서버로 가는 1초 기록과 그 기록의 테두리를 보여 준다 (문제 판정은 서버 모듈) |
| `test/` | FE 계약 확인(FE의 실제 시선 코드를 이 엔진에 붙여 실행) · 엔진 경계 · Worker · 자산 검증 |
| `scripts/` | `copy-assets`(해시 확인 후 복사) · `smoke`(실제 브라우저) · `bench`(지연 측정) |

자세한 내용은 [web/README.md](web/README.md)에 있습니다.

---

## 4. 실행 흐름 따라가기

### 4-1. 브라우저 (배포될 경로)

```
카메라 ─▶ camera/view.ts  createImageBitmap (초당 8장)
            │ postMessage {frame, mode}
            ▼
          worker/gaze.worker.ts ─▶ engine/engine.ts (GazeEngine)
            check      preconditions.ts        한 명 · 가운데 · 크기 · 밝기
            sweep      sweep.ts                고개 원 → 정면 기준
            calibrate  gauge.ts → reference.ts 화면 중앙 · 렌즈 · 대본 기준점 (보정 모델: 메모리에만)
            live       observe.ts → reference.ts → condition.ts → FrameDecision
            │
            ▼
          evidence.ts GazeSlicer ─▶ 1초 기록 ─▶ (FE → BE → AI 서버)
```

한 프레임은 이렇게 됩니다: MediaPipe가 얼굴 랜드마크와 변환 행렬을 냄 → 행렬에서 고개 yaw · pitch →
사용자의 세 기준점(화면 중앙 · 렌즈 · 대본)과 비교 → 상태와 확률, OTHER면 방향 → 촬영 조건 감시가 신뢰도를 붙임.
첫 프레임에서 시작하는 1초 격자로 프레임(초당 8개 목표)을 모아 다수결로 1초 기록 하나를 만듭니다.

### 4-2. 서버 판정 모듈

```
코치 ─ judge({t_ms, since_ms, records, voiced})          최근 30초 1초 기록 · 같은 1초들의 발화 시간
        read_records   이상한 기록은 그 1초만 버림
        _timeline      정렬 · 겹침 제거 · 빈 시간 = UNMEASURED
          ├─ _tally      since_ms 뒤 새 1초마다 상태별 ms · 말한 시간 · 대본 의존
          ├─ _window ×2  짧은 창 5초 · 최근 창 30초의 비율 · 측정 비율 · 신뢰도
          ├─ _run_at     지금 이어지는 대본 · 화면 · 그 밖 응시
          └─ _issues     measurable · state · 다섯 문제  ─▶ JudgmentResult
코치 ─ summarize(장 · Take 동안 더한 tally) ─▶ {"GAZE": 비율 · reading_ratio · reading_runs · measured_ratio}
코치 ─ criteria() ─▶ 다섯 문제의 {metric, direction, threshold, bad, onset_lag_ms, offset_lag_ms}
```

예(`features/gaze/examples/`):
- 26초 청중을 보다가 말하면서 대본을 4초 이어 보면 `GAZE_ON_SCRIPT`(severity 0.5714)가 나오고, 그 1초는 `reading_ms` 1000입니다.
- 말하면서 대본을 5초 보다가 청중으로 돌아오면, 돌아온 1초의 `tally`에 `reading_runs` 1이 들어갑니다.
- 카메라가 끊겨 기록이 5초 오지 않으면 짧은 창이 비어 `measurable: false`이고, 문제는 `GAZE_UNMEASURABLE`(시선 피드백 보류 신호)만 나옵니다.
- Take 시작 뒤 5초 동안은 대본을 오래 봐도 `GAZE_UNMEASURABLE` 말고는 내지 않습니다.

### 4-3. research: 임계값과 기준 답

```
configs/*.yaml ─▶ gaze_lab.config.load_config ─┬─▶ VisionSession (Python 데모 · 평가)
                                               ├─▶ tools/export_config.py ─▶ web/src/engine/defaults.generated.ts
                                               └─▶ tools/make_fixtures.py ─▶ web/src/engine/__tests__/fixtures/parity.json
features/gaze/config.py (GazeConfig, 서버 판정 기준값) ─▶ tools/make_examples.py ─▶ features/gaze/examples/*_output.json
configs/evidence.yaml 에는 1초 나누기 값만 (서버 판정 값이 다시 생기면 tests/lab/test_core_sync.py 실패)
```

알고리즘을 바꿀 때는 Python에서 실험 · 평가 → TS에 같은 변경 → `npm run fixtures`로 기준 답 다시 생성 → 양쪽 테스트 순서로 합니다.
생성 파일이 낡으면 `tests/lab/test_web_engine_sources.py`가, 예시 출력이 낡으면 서비스 `tests/unit/gaze/test_examples.py`가 실패합니다.

### 4-4. 코치와 이어지는 곳 · 연구용 대역과 달라진 판정

코치는 판정 모듈을 import하지 않고 `Judges`로 `judge` · `summarize` · `criteria`를 받습니다(기능 문서 5-1).
코치 연구 폴더의 재생 · 채점 · 복구 실험은 이 모듈을 씁니다(`coach_lab.judges.real_judges()`, 2026-10-26 #558). 옛 코치 평가기를 옮긴 연구용 대역(`coach_lab/judges/gaze.py`, `gaze-0.2`)은 BE가 읽는 예시 생성(`lab_judges()`)과 대역 자체의 테스트에만 남았습니다.
같은 입력에서 이 모듈과 대역이 달라지는 점은 아래와 같습니다.

| | 연구용 대역 (`gaze-0.2`) | 이 모듈 (`gaze-1.3`) |
|---|---|---|
| 판정 창 | 10초 한 창 | 짧은 창 5초 · 최근 창 30초 |
| `GAZE_ON_SCRIPT` | 대본 비율 0.7 이상, 또는 이어짐 5초 이상이고 비율 0.6 이상 | 이어짐 3초 이상이고 짧은 창 대본 비율 0.6 이상 |
| 그 밖의 문제 | `GAZE_UNMEASURABLE`만 | `GAZE_ON_SCREEN` · `GAZE_AWAY` · `GAZE_LOW_EYE_CONTACT`도 낸다 |
| 상태 | `AUDIENCE` · `SCRIPT` · `UNMEASURABLE` | `AUDIENCE` · `SCREEN` · `SCRIPT` · `AWAY` · `UNMEASURABLE` |
| 측정 불가 | `UNCERTAIN` + `UNMEASURED`(+ 기록 없는 시간)를 합친 비율이 0.5 초과(최근 평균과 나쁜 쪽) | 측정 비율(짧은 창 · 최근 창 중 나쁜 쪽)이 0.5 미만이거나 짧은 창 평균 신뢰도가 0.5 미만 |
| 빠진 `reliability` | 1.0으로 봄 | 0으로 봄(기능 문서 5-2 "없으면 0") |
| 기록이 잠깐 끊김 | 마지막 기록이 2초 안이면 이어짐을 유지 | 빈 1초가 끼면 이어짐이 끊김 |

바꾸기 전에 코치 코어의 `Judges`에 대역 대신 이 모듈을 넣어 재생 시나리오(`ai/research/coach-agent/scenarios/`) 16개를 돌려 본 결과입니다(2026-10-25).
- 코치의 `JudgmentResult` · `IssueCriteria` 검증 오류는 0건이었습니다(`judge` 2,756번).
- 그때 시뮬레이터가 만드는 시선 기록에는 `reliability`가 없어, 이 모듈에서는 Take 내내 측정 불가가 됐습니다.
- 기록에 신뢰도를 실제 엔진처럼 채우면 16개 중 15개가 대역과 같게 기대를 통과했습니다.
- 남은 하나(대본 응시를 포기하는 시나리오)는 시선 사다리의 두 번째 개입이 새로 생긴 `GAZE_LOW_EYE_CONTACT`로 가서 기대가 달라졌습니다.
- 바꿀 때 시뮬레이터 기록에 `reliability`(0.95)를 넣고 그 시나리오의 기대를 맞췄습니다. 지금 재생은 16/16 통과입니다(코치 연구 폴더 README 7절).

---

## 5. 실행법

### 준비

```bash
cd ai/research/gaze-tracking
uv sync                                  # Python 3.12, 라이브러리 + 개발 도구
uv run python tools/fetch_assets.py      # 모델 · 테스트 얼굴(합성, git 밖)을 받고 sha256 확인
cd web && npm ci && cd ..               # Node 24
```

- 아래 명령은 모두 `ai/research/gaze-tracking`에서 돌립니다. `npm` 명령은 `web/`에서 돌므로 그 줄에서 `cd web`으로 들어가고 블록 끝에서 돌아옵니다.
- `fetch_assets.py`는 토큰 없이 받습니다(테스트 얼굴은 공개 CC0 데이터셋). 네트워크가 막히면 `--model-file` / `--face-file`.
- torch는 눈 기반 백본 비교 실험에만 씁니다: `uv sync --extra torch` (CPU 판).
- Windows에서는 `pytest.exe`가 앱 제어로 막혀 있을 수 있어 `uv run python -m pytest`로 부릅니다.

### 데모

```bash
run_demo.bat                                           # Python 데모 (웹캠, OpenCV 창). 또는:
uv run python tools/pipeline_demo.py --video tests/fixtures/local/static_face_30fps.mp4 --no-window --advisory --countdown 0
cd web && npm run dev                                  # 브라우저 데모 http://localhost:5180 (끝낸 뒤 cd ..)
```

### 테스트

```bash
uv run python -m pytest                    # 기준 구현(lab)
# 서버 판정 모듈은 ai/service 에서: uv run python -m pytest tests/unit/gaze
uv run ruff check src tests tools experiments
cd web
npm test                                   # 엔진 · 경계 · Worker · FE 계약
npm run typecheck                          # tsc 4종 (전체 / 설정 / FE 컴파일러 옵션 / DOM 없는 엔진)
npm run lint                               # FE 와 같은 oxlint 규칙으로 엔진 · Worker
npm run smoke                              # 실제 브라우저 (Chrome. CHROME=<경로> 로 다른 Chromium), COOP/COEP 켬
npm run smoke:prod                         # 운영처럼 격리 헤더 없이
cd ..
```

### 생성 파일 · 평가

```bash
uv run python tools/make_examples.py       # 서비스 features/gaze 의 기준값 · 판정을 바꾼 뒤: examples/ 출력 다시 생성
cd web && npm run fixtures && cd ..        # configs 나 gaze_lab 을 바꾼 뒤: TS 임계값 · 기준 답 다시 생성
uv run python -m gaze_lab.evaluation.gaze_eval --help      # 녹화한 특징 테이블이 있어야 돈다
uv run python experiments/exp1_backbone.py --help
```

---

## 6. 테스트와 검증

| 묶음 | 위치 | 무엇을 | 수 |
|---|---|---|---|
| 서버 판정 모듈 | 서비스 `tests/unit/gaze/` | `judge`(읽기 · 빈 시간 · 겹침 · 커서 · 대본 의존 · 지표 · 상태 · 다섯 문제 · 시작 직후 억제 · 기준값 경계), `summarize` · `criteria`, `examples/` 계약, 입출력 모델, 코어 import 경계 | 206 passed (서비스, 2026-10-28) |
| 기준 구현 | `tests/lab/` | 전처리 기하 · 고개 방향 · 보정 · 조건 감시 · 평가 · 데모 · 시나리오 S01~S24, 생성 파일 최신, 1초 나누기 값과 서버 판정 값의 분리 | 1628 passed · 155 skipped (얼굴 자산 없이, 2026-10-26) |
| TS 엔진 | `web/src/engine/__tests__/` | Python 기준 답과 1e-9~1e-12 안에서 일치, 엔진 흐름, 시나리오, 초기화 실패 | 145 |
| 그 밖의 web | `web/test/` | FE 계약(FE의 실제 `workers/modelClassifier.ts` · `aiAdapter.ts`를 이 엔진에 붙여 실행), 엔진 import · 저장 API 경계(`contract.ts`가 MediaPipe에 닿지 않음 포함), Worker, 자산 검증 | 53 |
| 실제 브라우저 | `web/scripts/smoke.mjs` | MediaPipe를 Worker에서 로드, 준비 점검 → 고개 원 → 보정 → 실시간 → 1초 기록, 카메라 끊김, 지연 | 23 항목 (Chrome `smoke` · `smoke:prod` 통과, Edge는 기능 22개 통과 · 지연만 초과) |

Python 테스트 중 얼굴 사진이 필요한 것(66개 함수)은 `tools/fetch_assets.py`를 돌려야 실행되고, 아니면 이유를 보이고 건너뜁니다.

### 테스트 얼굴

실존 인물이 아닌 **SFHQ 합성 얼굴(CC0)** 을 웹캠 구도로 맞춰 씁니다. 사진은 git에 올리지 않고 받기 도구와 해시만 커밋합니다.
고른 기준과 바꾸는 방법은 [tests/fixtures/README.md](tests/fixtures/README.md)에 있습니다.

### 옮긴 뒤 검증

- archive를 그대로 돌린 결과와 비교했습니다: pytest 테스트별 결과(옛 사진 기준 1785개 동일), 데모 영상 재생 출력(바이트 단위 동일),
  생성 파일(경로 문자열 · 설정 해시 · 소수점 끝자리 2.2e-16 이하 차이만), vitest(archive와 같은 수에서 시작).
- 옮길 때의 옛 서버 코어(`evaluate_gaze` 등)는 무작위 타임라인 1500개 × 설정 2종에서 archive 모듈과 출력이 모두 같았습니다(381,140건).
  지금 서버 판정 모듈은 기능 문서 5 · 6절대로 다시 만든 것이라 archive와 비교하지 않습니다(4-4).
- 설정 해시는 `958414c647b7` → `14ed457ab13b`로 바뀌었습니다. 해시에 모델 경로 문자열이 들어가서이고, 임계값은 하나도 바뀌지 않았습니다.
  그 뒤 서버 판정 값을 엔진 설정(`configs/evidence.yaml`)에서 빼며 `32ad2751de59`가 됐습니다. 엔진이 쓰는 값은 그대로입니다.

### 서비스로 옮긴 결과

서버 판정 모듈은 `ai/service/src/pitch_coach_ai/features/gaze/`, 그 테스트는 `ai/service/tests/unit/gaze/`로 옮겼습니다([설계 문서](../../../docs/ai/features/gaze-tracking.md) 11절, [서비스 문서](../../../docs/ai/ai-service.md) 6-1). 이 폴더는 서비스를 editable로 설치해(`pyproject.toml`의 `[tool.uv.sources]`) `pitch_coach_ai.features.gaze`로 import합니다(`gaze_lab` · `tools/make_examples.py` · `tests/lab`). 판정 모듈은 서비스에서 고칩니다.

| 확인 (2026-10-26) | 결과 |
|---|---|
| 옮긴 코어 · 예시 | 이름만 바뀜(100%), 코드 그대로 |
| 테스트 변경 | import 접두어 줄만(앞서 함수 안의 import 하나를 파일 맨 위로 올림) |
| 테스트 수 | 옮기기 전 연구 1834 passed = 옮긴 뒤 연구 1628 + 서비스 시선 205 passed + 1 skipped |
| `tools/make_examples.py` | 서비스 위치의 예시를 다시 만들어도 git diff 없음 |
| 공통 패키지 판 | 연구 `uv.lock`을 서비스 `uv.lock`과 같게 맞춤 |

- 서비스의 '말한 1초' 기준값 비교(`test_speaking_second_threshold_matches_volume`)는 같은 `features/` 아래 `volume/config.py`를 먼저 찾고, 소리 크기가 서비스에 오기 전에는 연구 폴더의 소리 크기 `config.py`(`ai/research/volume-analysis/src/volume/`)와 비교합니다. 옮길 때(위 표)는 서비스 위치의 경로만 찾아 건너뛰었고(1 skipped), 서비스 탑재 리뷰를 반영하며 고쳤습니다.

---

## 7. 얼굴 데이터와 예외 처리

### 얼굴 데이터

| 데이터 | 어디까지 | 보장 |
|---|---|---|
| 카메라 프레임, 랜드마크 478점, 변환 행렬 | Worker 안에서만. 처리 직후 `close` | 엔진 코드는 저장 · 전송 · 로그 API를 쓰면 테스트가 실패 |
| 얼굴 가이드 7점, 준비 점검 측정값(얼굴 크기 · 홍채 · 거리) | 화면 표시용 메모리 | 〃 |
| 보정 모델 `CalibrationModel` (기준점 각도 + 얼굴 위치 · 면적 · 홍채 · 거리) | 그 세션의 메모리에만. 다음 세션은 다시 보정 | 타입 · 주석에 "저장 금지" 명시 |
| **1초 기록** (상태 · 방향 · 투표 비율 · 신뢰도 · 조건 이슈 · 프레임 수) | **발표 중 기기 밖으로 나가는 값** (보정 결과 요약은 발표 전에 따로, 기능 문서 12-1) | 서버 계약이 필드 8개만 받음 (얼굴 값은 계약에 없음) |

원본 얼굴 영상을 녹화 · 저장하는 archive의 수집 도구(`collect` · `label_review` · `extract_features`)는 옮기지 않았습니다.
실측 데이터 수집은 동의 · 보관 기간 · 삭제 절차를 정한 뒤 별도 작업으로 합니다.

### 예외 처리

| 상황 | 동작 | 발표 |
|---|---|---|
| 지원하지 않는 브라우저 | `checkSupport()`가 카메라 권한을 묻기 전에 알려 줌 (WebAssembly · createImageBitmap만 필수) | 계속 |
| 모델 · 런타임 로딩 실패 · 지연 | `EngineInitError` (`TIMEOUT` 기본 60초 / `INIT_FAILED`) | 계속 |
| 다른 모델 파일 · 런타임 버전 | `npm run assets`가 해시 · 버전을 확인하고 복사하지 않음 (빌드 단계) | — |
| 프레임 처리 중 예외 | 그 프레임은 얼굴 없음으로 응답하고 다음 프레임 계속. 오류는 연속 실패당 한 번 알림 | 계속 |
| Worker 종료 | 카메라 화면이 `WORKER_FAILED`를 한 번 알리고 프레임 전송을 멈춤 | 계속 |
| 발표 중 카메라 끊김 | 카메라 화면이 `CAMERA_LOST`를 한 번 알리고 캡처를 멈춤. 서버는 빈 시간을 측정 못 함으로 봄 | 계속 |
| 탭 숨김 · 전송 유실 | 서버가 빈 시간을 측정 못 함으로 채움 → 오래된 이슈를 내지 않고, 길어지면 시선 피드백 보류 | 계속 |
| 얼굴이 안 보임 · 다른 사람 · 자세가 크게 바뀜 | 측정 못 함 / 판정 보류 / 신뢰도 낮춤 (조건 감시) | 계속 |
| 읽을 수 없는 1초 기록 | 그 1초만 버리고(`read_records`) 측정 못 함으로 채움 | — |
| 판정 시각 뒤의 기록 · 시각이 크게 어긋난 기록 | 판정 시각 뒤는 버림. 빈 시간은 판정에서 조각 하나이고 `tally`도 30초보다 오래된 빈 시간은 한 조각이라 계산량이 늘지 않음 | — |

---

## 8. 설계 결정

기능 사이 경계(기기에 남는 값 · 서버 판정 · 받는 쪽 규칙)를 정한 이유는 [설계 문서](../../../docs/ai/features/gaze-tracking.md) 4-3에 있습니다. 아래는 연구 폴더의 개발 방식에 관한 결정입니다.

| 결정 | 이유 |
|---|---|
| 생성 파일에 `.generated.` | 손으로 고치지 않는 파일 표시. FE의 prettier · lint에서 패턴 하나로 뺄 수 있다 |
| 엔진 테스트는 엔진 폴더 안(`__tests__`) | 폴더째 옮길 때 테스트와 기준 답이 같이 간다 |
| PnP 상수는 그대로 | 상수를 바꾸면 판정이 바뀐다. 여러 얼굴로 유도 · 평가한 뒤 기능 버전을 올리는 별도 변경으로 다룬다 ([9장](#9-알려진-한계)) |
| uv · Python 3.12 · mediapipe 정확히 고정 | AI 서버와 같은 Python, 브라우저 엔진(`@mediapipe/tasks-vision` 1.0.1)과 같은 MediaPipe. 버전이 다르면 랜드마크가 조금 달라진다 |

---

## 9. 알려진 한계

- **정확도를 실제 데이터로 잰 적이 없습니다.** 실제 발표자 녹화가 없고, 평가 도구(`gaze_lab.evaluation`)는 데이터가 생기면 바로 쓸 수 있는 상태입니다.
  임계값(`configs/*.yaml`)은 모두 초기값입니다. 여러 사람의 실측으로 평가하기 전에는 바꾸지 않습니다.
- **PnP 대체 경로의 상수는 한 사람의 얼굴 기하입니다.** archive 사진에서 유도한 값이라 지금 테스트 얼굴에서는 최대 3.3 cm 다르고
  PnP 경로가 pitch를 약 18° 더 읽습니다. MediaPipe가 변환 행렬을 주면 이 경로를 타지 않고, 브라우저 엔진에는 이 경로가 없습니다.
- **GPU delegate는 Python과 같은 값인지 확인하지 않았습니다.** 운영은 CPU입니다.
- **브라우저는 Chrome · Edge(둘 다 Chromium)에서만 확인했습니다.** Edge는 기능 항목은 모두 통과했고, 부하가 있는 PC에서 프레임 지연이 예산을 넘었습니다. Safari · Firefox는 확인 전입니다.
- **카메라 화면(`web/src/camera/view.ts`)은 FE lint의 중첩 삼항식 규칙에 8곳 걸립니다.** UI를 지금 고치지 않기로 해서 그대로 두었습니다.
- 지연은 개발 PC 기준입니다. 한가할 때 Worker p95 약 30~38 ms였지만, 다른 일로 CPU를 쓰는 상태에서는 74~207 ms까지 흔들려
  예산(125 ms)을 넘기도 했습니다([web/README.md](web/README.md) '지연 시간'). 실제 사용자 기기 · 저사양 노트북에서 다시 재야 합니다.

---

## 10. 코드 읽는 순서

1. `features/gaze/schemas.py` · `examples/judge_input_script.json` — 서버가 무엇을 받고 무엇을 내는지
2. `features/gaze/core.py` — 1초 기록이 1초 판정 결과 · Take 지표가 되는 과정
3. `web/src/engine/index.ts` · `contract.ts` — 브라우저 엔진의 입구와 FE 경계
4. `web/src/engine/engine.ts` — 준비 점검 → 고개 원 → 보정 → 판정의 흐름
5. `src/gaze_lab/runtime/session.py` — 같은 흐름의 Python 판
6. `tools/pipeline_demo.py` · `web/src/demo/main.ts` — 실제로 돌려 보는 곳

---

## 11. 원본

원본은 `ai/archive/workspaces/jewon-kim/gaze-tracking/v1/local/`(태그 `ai-workspaces-final`)입니다.

| archive | 여기 |
|---|---|
| `ai/src/vision/` | `src/gaze_lab/` (1초 기록을 읽는 부분은 서비스 `features/gaze/`) |
| `ai/evaluation/` · `ai/evaluation/experiments/` | `src/gaze_lab/evaluation/` · `experiments/` |
| `ai/tools/{pipeline_demo,download_checkpoints}.py` | `tools/` |
| `ai/tools/{collect,label_review,extract_features}.py` | 옮기지 않음 (원본 얼굴 영상 저장) |
| `ai/configs/` · `ai/models/` · `ai/reports/` | `configs/` · `artifacts/` · `outputs/` |
| `tests/` | `tests/lab/` (+ 서버 판정 모듈 테스트는 서비스 `tests/unit/gaze/`) |
| `tests/fixtures/{face.jpg,static_face_30fps.mp4}` | 합성 얼굴로 교체, git 밖 (`tests/fixtures/`) |
| `web/` (+ `web/scripts/*.py`) | `web/` (+ `tools/`) |
| `v1/README.md` 등 문서 4벌 | 이 문서 · `web/README.md` · 설계 문서(`docs/ai/features/gaze-tracking.md`) |
