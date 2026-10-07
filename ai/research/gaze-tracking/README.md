# gaze-tracking (시선)

웹캠으로 발표자가 **카메라(청중) · 화면 · 대본 · 그 밖** 중 어디를 보는지 판정하고,
그 기록으로 코치 · 리뷰 에이전트가 읽을 **시선 이슈와 테이크 요약**을 만드는 기능입니다.

판정은 **사용자 브라우저 안에서(온디바이스)** 돕니다. 카메라 영상 · 얼굴 랜드마크 · 얼굴 측정값은 기기를 떠나지 않고,
서버에는 **1초 기록**(1초마다 상태 하나, 필드 8개)만 갑니다.

| | |
|---|---|
| 담당 | jewon-kim |
| 기능 버전 | 서버 코어 `1.1` (`src/gaze/version.py`), 브라우저 엔진 `gaze_v1.1.0+head_pose+reference_anchor_v1` |
| 판정 신호 | 고개 방향 (MediaPipe Face Landmarker의 변환 행렬). 눈 기반 백본은 비교 실험용으로 보관 |
| 상태 | 테스트 영상 · 합성 얼굴로만 검증했습니다. **실제 발표자 녹화로 정확도를 잰 적은 아직 없습니다** |
| 입출력 | [INTERFACE.md](INTERFACE.md) — 엔진 계약, 1초 기록, 서버 코어의 입력 · 출력 |
| 배포 | [DEPLOY.md](DEPLOY.md) — 서버 코어를 AI 서버로, TS 엔진을 frontend로 옮기는 방법 |

> `ai/archive/workspaces/jewon-kim/gaze-tracking/v1/local/`을 구조를 바꿔 옮긴 프로젝트입니다.
> 판정 로직은 같고, 같은 입력이면 archive와 결과가 같습니다 ([옮긴 뒤 검증](#옮긴-뒤-검증)).
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
| 코치 이슈 | 지금 대본 · 화면 · 다른 곳을 오래 보는지, 눈맞춤이 적은지, 측정할 수 없는지 | 서버 |
| 리뷰 요약 | 테이크 전체의 상태별 시간 · 비율 · 구간 · 문제 구간, 이전 테이크와 차이, 피드백 전후 효과 | 서버 |

비율은 모두 **측정된 시간**(판정된 4개 상태)으로 나눕니다. 재지 못한 시간이 "청중을 안 봤다"로 읽히지 않게 하기 위해서입니다.
측정할 수 없으면 `GAZE_UNMEASURABLE`(시선 피드백 보류 신호)을 냅니다. 재지 못한 시간에 틀린 시선 피드백을 하지 않기 위해서이고, 발표는 그대로 이어집니다.

---

## 2. 코드 구조 한눈에 보기

코드는 **실행되는 곳**에 따라 세 덩어리입니다. 나중에 옮길 때 폴더째 복사하면 되도록 나눴습니다.

| 덩어리 | 폴더 | 하는 일 | 나중에 |
|---|---|---|---|
| **TS 엔진** | `web/src/engine/` | 프레임 → 준비 점검 · 고개 원 · 보정 · 프레임 판정 · 1초 기록 (Web Worker 안) | `frontend/src/workers/gaze/engine/`로 폴더째 |
| **서버 코어** | `src/gaze/` | 1초 기록 → 코치 이슈 · 테이크 요약 · 비교 · 개입 효과 | `ai/service`의 `features/gaze/`로 폴더째 |
| **Python 기준 구현** | `src/gaze_lab/` | 같은 판정의 Python 판 + 데이터셋 · 평가 · 실험. TS 엔진의 임계값 · 기준 답을 만든다 | research에 남음 |

```
tools/ · experiments/  ──import──▶  src/gaze_lab/  ──import──▶  src/gaze/         (반대 방향 금지)
web/src/{demo,camera,worker}  ──import──▶  web/src/engine/                         (엔진은 밖을 import 하지 않음)
tools/export_config.py · make_fixtures.py  ──생성──▶  web/src/engine/defaults.generated.ts · __tests__/fixtures/
```

### 지키는 규칙

1. **얼굴에서 나온 값은 기기 메모리에만.** 프레임 · 랜드마크 · 얼굴 측정값 · 보정 모델은 저장 · 전송 · 로그하지 않습니다.
   기기 밖으로 나가는 것은 1초 기록뿐입니다 ([7장](#7-얼굴-데이터와-예외-처리)). 테스트로 막습니다.
2. **막지 않는다.** 시선은 정보를 얻으려는 기능이지 발표를 통제하는 기능이 아닙니다. 조건이 나쁘면 "측정 못 함"으로 남기고,
   발표는 어떤 경우에도 멈추지 않습니다. 이상한 1초 기록은 그 1초만 버립니다.
3. **임계값 원본은 하나다.** `configs/*.yaml`이 원본입니다. TS는 생성된 `defaults.generated.ts`만 읽고,
   서버 코어 기본값은 YAML과 같은지 테스트로 묶습니다. 테스트 사진 하나에 맞춰 임계값을 바꾸지 않습니다(과적합).
4. **경계는 테스트로.** 서버 코어는 상대 import와 허용한 라이브러리(pydantic 등)만 쓰고 파일 · 네트워크 · 환경변수를 쓰지 않습니다.
   TS 엔진은 자기 폴더와 MediaPipe만 import하고, DOM 없이(Worker 전역만) 컴파일됩니다.

---

## 3. 폴더와 파일

### 3-1. 전체 트리

```
gaze-tracking/
├── README.md · INTERFACE.md · DEPLOY.md
├── pyproject.toml · uv.lock · .python-version   Python 3.12, mediapipe==1.0.1 고정 (uv)
├── .gitignore · .gitattributes                  artifacts · data · outputs · fixtures/local 제외, LF
├── run_demo.bat                                 Windows 더블클릭으로 Python 데모
├── configs/                                     임계값 · 설정 원본 YAML 11개
├── artifacts/                                   모델 파일 자리 (git 밖). manifest(*.json)만 커밋
├── outputs/                                     실행 결과 (git 밖)
├── src/
│   ├── gaze/                                    서버 코어
│   └── gaze_lab/                                Python 기준 구현 · 평가
├── experiments/                                 비교 실험 2개 (녹화 데이터 필요)
├── tools/                                       데모 · 자산 받기 · 생성기 · 유도 스크립트
├── tests/
│   ├── unit/                                    서버 코어 테스트 (코어와 함께 service로 간다)
│   ├── lab/                                     기준 구현 · 평가 · 도구 · 생성 파일 최신 검사
│   └── fixtures/                                테스트 얼굴의 출처 · 해시 (사진은 local/, git 밖)
└── web/                                         브라우저: TS 엔진 · Worker · 카메라 화면 · 데모 (Node 24)
```

### 3-2. 서버 코어 `src/gaze/`

| 파일 | 하는 일 |
|---|---|
| `core.py` | `GazeSample`(1초 기록), `GazeTimeline`(창 통계 · 연속 구간), `evaluate_gaze`(코치 이슈), `take_summary`, `compare_summaries`, `intervention_outcome`, 서버 입구의 `normalize_samples`(정렬 · 중복 제거 · 빈 시간을 측정 못 함으로) |
| `schemas.py` | 입출력 계약(pydantic): 1초 기록 `GazeSampleRecord`와 `parse_records`(읽을 수 없는 기록은 그 1초만 버림), 이슈 · 요약 · 비교 · 개입 효과 모델 |
| `config.py` | `EvidenceConfig`: 창 길이 · 이슈 임계값 (`configs/evidence.yaml`과 같은 값) |
| `version.py` | 기능 버전. 출력의 의미가 바뀌면 올린다 |

### 3-3. Python 기준 구현 `src/gaze_lab/`

| 폴더 · 파일 | 하는 일 | TS 짝 |
|---|---|---|
| `schemas.py` · `config.py` | 프레임 · 보정 · 이벤트 모양, `configs/` → `VisionConfig`, 경로 · 설정 해시 | `types.ts` · `contract.ts` · `config.ts` |
| `preprocess/` | 프레임 → MediaPipe 랜드마크 → 고개 방향 · 크롭 · 품질 | `landmarker.ts` · `headpose.ts` · `observe.ts` |
| `backbones/` · `eye/` | 고개 방향 백본(기본), 눈 기반 백본 3종(비교 실험용) | `engine.ts` 안 |
| `calibration/` | 3점 보정: 기준점 분류기 · 게이지 · 품질 (LR은 비교용) | `reference.ts` · `gauge.ts` |
| `runtime/` | `VisionSession`(전체 흐름) · 준비 점검 · 고개 원 · 조건 감시 · 카메라 배치 · 버전 | `engine.ts` · `preconditions.ts` · `sweep.ts` · `condition.ts` · `placement.ts` |
| `temporal/` | `GAZE_STATE` 이벤트 평활 | 없음 (1초 다수결은 FE) |
| `evidence/gaze.py` | 프레임 판정 → 1초 기록(`GazeSlicer`), 세션 기록기. 1초 기록을 읽는 부분은 `gaze.core`를 다시 내보냄 | `evidence.ts` |
| `data/` · `evaluation/` | 라벨 · 분할 · 특징 테이블, 지표 · 릴리스 게이트 · 평가 CLI · 임계값 스윕 · 실험 로그 | 없음 |

### 3-4. `tools/` · `experiments/`

| 파일 | 하는 일 |
|---|---|
| `tools/fetch_assets.py` | 모델과 테스트 얼굴을 받아 해시를 확인한다 (처음 한 번) |
| `tools/make_static_video.py` | 테스트 얼굴 원본 → 웹캠 구도의 사진 · 정지 영상 |
| `tools/pipeline_demo.py` | 웹캠(또는 영상)으로 준비 점검 → 고개 원 → 보정 → 실시간 판정을 보는 Python 데모 |
| `tools/export_config.py` · `make_fixtures.py` | `configs/` → TS 임계값, Python 실행 결과 → TS parity 기준 답 |
| `tools/derive_headpose_model.py` | 얼굴 사진 한 장 → PnP 모델 점 9개 (고개 방향 대체 경로의 상수를 만든 방법) |
| `tools/download_checkpoints.py` | 눈 기반 백본 가중치 (비교 실험용) |
| `tools/make_y4m.py` | 웹 smoke용 가짜 카메라 영상 |
| `experiments/exp1_backbone.py` · `exp2_calibration_ablation.py` | 백본 비교 · 보정 특징 절제 (녹화한 특징 테이블 필요) |

### 3-5. 브라우저 `web/`

| 폴더 | 하는 일 |
|---|---|
| `src/engine/` | **TS 엔진** — frontend로 갈 폴더. `index.ts`(진입점 · 지원 확인 · 초기화 실패 사유) · `contract.ts`(FE 경계) 외에는 엔진 안에서만 쓴다. `__tests__/`에 엔진 테스트와 Python 기준 답 |
| `src/worker/` | 카메라 화면용 Worker (메시지 처리) |
| `src/camera/` | 카메라 화면 모듈: 영상 · 얼굴 원 · 보정 안내 (FE가 가져가 고칠 UI, 지금은 그대로) |
| `src/demo/` | 로컬 데모 페이지 (FE 페이지 대역). `evidence-preview.ts`는 서버 코어의 TS 사본으로 데모의 이슈 · 요약 미리보기 전용 |
| `test/` | FE 계약 확인 · 미리보기 parity · 엔진 경계 · Worker · 자산 검증 |
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
8프레임(1초)이 모이면 다수결로 1초 기록 하나가 됩니다.

### 4-2. 서버 코어

```
요청의 1초 기록 ─▶ parse_records ─▶ normalize_samples ─▶ GazeTimeline
                   (읽을 수 없는 1초 버림)  (정렬 · 중복 제거 · 빈 시간 = 측정 못 함)
                                                         ├─ evaluate_gaze(지금)    코치 이슈
                                                         ├─ take_summary()        리뷰 요약
                                                         ├─ compare_summaries()   이전 테이크와 차이
                                                         └─ intervention_outcome() 피드백 전후 효과
```

예: 10초 청중을 보다가 4초 대본을 보면 `GAZE_ON_SCRIPT`(severity 0.4 = 4초/10초)가 나옵니다.
여기서 카메라가 끊겨 기록이 멈추면, 1초 뒤에는 대본 이슈가 사라지고 3초쯤 지나면 `GAZE_UNMEASURABLE`(시선 피드백 보류 신호)이 됩니다.
120초 테이크의 요약은 `coverage` 0.1167(측정 14초 / 120초)입니다.

### 4-3. research: 임계값과 기준 답

```
configs/*.yaml ─▶ gaze_lab.config.load_config ─┬─▶ VisionSession (Python 데모 · 평가)
                                               ├─▶ tools/export_config.py ─▶ web/src/engine/defaults.generated.ts
                                               └─▶ tools/make_fixtures.py ─▶ web/src/engine/__tests__/fixtures/parity.json
                                                                           web/test/fixtures/evidence.json
configs/evidence.yaml ◀── 같은지 테스트 ──▶ gaze.config.EvidenceConfig 기본값 (서버가 쓰는 값)
```

알고리즘을 바꿀 때는 Python에서 실험 · 평가 → TS에 같은 변경 → `npm run fixtures`로 기준 답 다시 생성 → 양쪽 테스트 순서로 합니다.
생성 파일이 낡으면 `tests/lab/test_web_engine_sources.py`가 실패합니다.

---

## 5. 실행법

### 준비

```bash
cd ai/research/gaze-tracking
uv sync                                  # Python 3.12, 라이브러리 + 개발 도구
uv run python tools/fetch_assets.py      # 모델 · 테스트 얼굴(합성, git 밖)을 받고 sha256 확인
cd web && npm ci                         # Node 24
```

- `fetch_assets.py`는 토큰 없이 받습니다(테스트 얼굴은 공개 CC0 데이터셋). 네트워크가 막히면 `--model-file` / `--face-file`.
- torch는 눈 기반 백본 비교 실험에만 씁니다: `uv sync --extra torch` (CPU 판).
- Windows에서는 `pytest.exe`가 앱 제어로 막혀 있을 수 있어 `uv run python -m pytest`로 부릅니다.

### 데모

```bash
run_demo.bat                                           # Python 데모 (웹캠, OpenCV 창). 또는:
uv run python tools/pipeline_demo.py --video tests/fixtures/local/static_face_30fps.mp4 --no-window --advisory --countdown 0
cd web && npm run dev                                  # 브라우저 데모 http://localhost:5180
```

### 테스트

```bash
uv run python -m pytest                    # 서버 코어(unit) + 기준 구현(lab)
uv run python -m pytest tests/unit         # 서버 코어만
uv run ruff check src tests tools experiments
cd web
npm test                                   # 엔진 · 미리보기 · 경계 · Worker · FE 계약
npm run typecheck                          # tsc 4종 (전체 / 설정 / FE 컴파일러 옵션 / DOM 없는 엔진)
npm run lint                               # FE 와 같은 oxlint 규칙으로 엔진 · Worker
npm run smoke                              # 실제 브라우저 (Chrome. CHROME=<경로> 로 다른 Chromium), COOP/COEP 켬
npm run smoke:prod                         # 운영처럼 격리 헤더 없이
```

### 생성 파일 · 평가

```bash
cd web && npm run fixtures                 # configs 나 gaze_lab 을 바꾼 뒤: TS 임계값 · 기준 답 다시 생성
uv run python -m gaze_lab.evaluation.gaze_eval --help      # 녹화한 특징 테이블이 있어야 돈다
uv run python experiments/exp1_backbone.py --help
```

---

## 6. 테스트와 검증

| 묶음 | 위치 | 무엇을 | 수 |
|---|---|---|---|
| 서버 코어 | `tests/unit/` | 타임라인 · 이슈 · 요약 · 비교 · 개입 효과, 입출력 계약, 서버 입구(끊김 · 유실 · 순서 · 테이크 밖 · 잘못된 시각), 코어 import 경계 | 89 |
| 기준 구현 | `tests/lab/` | 전처리 기하 · 고개 방향 · 보정 · 조건 감시 · 평가 · 데모 · 시나리오 S01~S24, 생성 파일 최신, 코어와 YAML 동기화 | 1745 (+ torch 없을 때 38 skip) |
| TS 엔진 | `web/src/engine/__tests__/` | Python 기준 답과 1e-9~1e-12 안에서 일치, 엔진 흐름, 시나리오, 초기화 실패 | 145 |
| 그 밖의 web | `web/test/` | 서버 코어 미리보기(parity), FE 계약(실제 `frontend/src/workers/aiAdapter.ts`), 엔진 import · 저장 API 경계, Worker, 자산 검증 | 104 |
| 실제 브라우저 | `web/scripts/smoke.mjs` | MediaPipe를 Worker에서 로드, 준비 점검 → 고개 원 → 보정 → 실시간 → 1초 기록, 카메라 끊김, 지연 | 24 항목 |

Python 테스트 중 얼굴 사진이 필요한 것(66개 함수)은 `tools/fetch_assets.py`를 돌려야 실행되고, 아니면 이유를 보이고 건너뜁니다.

### 테스트 얼굴

실존 인물이 아닌 **SFHQ 합성 얼굴(CC0)** 을 웹캠 구도로 맞춰 씁니다. 사진은 git에 올리지 않고 받기 도구와 해시만 커밋합니다.
고른 기준과 바꾸는 방법은 [tests/fixtures/README.md](tests/fixtures/README.md)에 있습니다.

### 옮긴 뒤 검증

- archive를 그대로 돌린 결과와 비교했습니다: pytest 테스트별 결과(옛 사진 기준 1785개 동일), 데모 영상 재생 출력(바이트 단위 동일),
  생성 파일(경로 문자열 · 설정 해시 · 소수점 끝자리 2.2e-16 이하 차이만), vitest(archive와 같은 수에서 시작).
- 서버 코어는 무작위 타임라인 1500개 × 설정 2종에서 archive 모듈과 출력이 모두 같습니다(381,140건).
- 설정 해시는 `958414c647b7` → `14ed457ab13b`로 바뀌었습니다. 해시에 모델 경로 문자열이 들어가서이고, 임계값은 하나도 바뀌지 않았습니다.

---

## 7. 얼굴 데이터와 예외 처리

### 얼굴 데이터

| 데이터 | 어디까지 | 보장 |
|---|---|---|
| 카메라 프레임, 랜드마크 478점, 변환 행렬 | Worker 안에서만. 처리 직후 `close` | 엔진 코드는 저장 · 전송 · 로그 API를 쓰면 테스트가 실패 |
| 얼굴 가이드 7점, 준비 점검 측정값(얼굴 크기 · 홍채 · 거리) | 화면 표시용 메모리 | 〃 |
| 보정 모델 `CalibrationModel` (기준점 각도 + 얼굴 위치 · 면적 · 홍채 · 거리) | 그 세션의 메모리에만. 다음 세션은 다시 보정 | 타입 · 주석에 "저장 금지" 명시 |
| **1초 기록** (상태 · 방향 · 투표 비율 · 신뢰도 · 조건 이슈 · 프레임 수) | **기기 밖으로 나가는 유일한 값** | 서버 계약이 필드 8개만 받음 (얼굴 값은 계약에 없음) |

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
| 읽을 수 없는 1초 기록 | 그 1초만 버리고(`parse_records`) 측정 못 함으로 채움 | — |
| 테이크 밖 시각의 기록 (다른 단위의 시각 등) | 버림(`normalize_samples`). 빈 시간은 길이와 상관없이 기록 하나라 계산량이 늘지 않음 | — |

---

## 8. 설계 결정

| 결정 | 이유 |
|---|---|
| 서버 코어는 1초 기록 이후만 | 프레임은 기기를 떠나지 않으므로 서버가 받는 첫 데이터가 1초 기록이다. 프레임 → 1초 기록은 기기 쪽(TS 엔진 · `gaze_lab`) |
| TS 엔진에서 이슈 · 요약 계산을 뺌 | 서버 몫이다. 엔진에 사본이 있으면 frontend로 서버 로직이 같이 가서 두 벌을 맞춰야 한다. 데모는 미리보기 사본(`src/demo/evidence-preview.ts`)을 쓴다 |
| 공백 처리는 서버 입구 한 곳 | 카메라 끊김 · 탭 숨김 · 전송 유실을 원인마다 막지 않고 `normalize_samples`가 같이 처리한다. 기존 계산 함수는 그대로라 parity가 유지된다 |
| 입력 계약은 읽을 수 있는 건 버리지 않음 | 정보를 얻는 기능이라, 모르는 방향 · 빠진 값 때문에 기록을 거절하지 않는다. 뜻을 읽을 수 없는 기록만 그 1초를 버린다 |
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
- **브라우저는 Chrome에서만 확인했습니다.** Edge는 이 PC에서 자동화로 띄우지 못했고, Safari · Firefox는 확인 전입니다.
- **카메라 화면(`web/src/camera/view.ts`)은 FE lint의 중첩 삼항식 규칙에 8곳 걸립니다.** UI를 지금 고치지 않기로 해서 그대로 두었습니다.
- 지연은 개발 PC 기준(Worker p95 약 30~38 ms / 예산 125 ms)입니다. 저사양 노트북에서는 다시 재야 합니다.

---

## 10. 코드 읽는 순서

1. `src/gaze/schemas.py` — 서버가 무엇을 받고 무엇을 내는지
2. `src/gaze/core.py` — 1초 기록이 이슈 · 요약이 되는 과정
3. `web/src/engine/index.ts` · `contract.ts` — 브라우저 엔진의 입구와 FE 경계
4. `web/src/engine/engine.ts` — 준비 점검 → 고개 원 → 보정 → 판정의 흐름
5. `src/gaze_lab/runtime/session.py` — 같은 흐름의 Python 판
6. `tools/pipeline_demo.py` · `web/src/demo/main.ts` — 실제로 돌려 보는 곳

---

## 11. 원본

원본은 `ai/archive/workspaces/jewon-kim/gaze-tracking/v1/local/`(태그 `ai-workspaces-final`)입니다.

| archive | 여기 |
|---|---|
| `ai/src/vision/` | `src/gaze_lab/` (1초 기록을 읽는 부분은 `src/gaze/`) |
| `ai/evaluation/` · `ai/evaluation/experiments/` | `src/gaze_lab/evaluation/` · `experiments/` |
| `ai/tools/{pipeline_demo,download_checkpoints}.py` | `tools/` |
| `ai/tools/{collect,label_review,extract_features}.py` | 옮기지 않음 (원본 얼굴 영상 저장) |
| `ai/configs/` · `ai/models/` · `ai/reports/` | `configs/` · `artifacts/` · `outputs/` |
| `tests/` | `tests/lab/` (+ 서버 코어 테스트는 `tests/unit/`) |
| `tests/fixtures/{face.jpg,static_face_30fps.mp4}` | 합성 얼굴로 교체, git 밖 (`tests/fixtures/`) |
| `web/` (+ `web/scripts/*.py`) | `web/` (+ `tools/`) |
| `v1/README.md` 등 문서 4벌 | 이 문서 · INTERFACE.md · DEPLOY.md · `web/README.md` |
